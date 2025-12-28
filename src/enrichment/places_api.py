"""
Google Places API client with async batch fetching, caching, and rate limiting.

Efficiently fetches place details for thousands of place IDs while respecting
API rate limits and minimizing costs through aggressive caching.
"""

import asyncio
import time
from typing import Optional, Dict, List
from datetime import datetime, timedelta

import httpx
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from sqlalchemy.orm import Session

from ..core.config import get_settings
from ..core.database import PlaceModel
from ..core.models import Place

console = Console()
settings = get_settings()


class RateLimiter:
    """Token bucket rate limiter for API requests."""

    def __init__(self, max_calls: int, period: float):
        """
        Initialize rate limiter.

        Args:
            max_calls: Maximum number of calls allowed in the period
            period: Time period in seconds
        """
        self.max_calls = max_calls
        self.period = period
        self.calls: List[float] = []
        self.lock = asyncio.Lock()

    async def acquire(self):
        """Wait if necessary to respect rate limit."""
        async with self.lock:
            now = time.time()

            # Remove calls outside the time window
            self.calls = [t for t in self.calls if now - t < self.period]

            # If at limit, wait
            if len(self.calls) >= self.max_calls:
                sleep_time = self.period - (now - self.calls[0])
                if sleep_time > 0:
                    await asyncio.sleep(sleep_time)
                    # Recalculate after sleep
                    now = time.time()
                    self.calls = [t for t in self.calls if now - t < self.period]

            # Record this call
            self.calls.append(time.time())


class PlacesAPIClient:
    """Google Places API client with async support and caching."""

    def __init__(self, api_key: str, db_session: Session):
        """
        Initialize Places API client.

        Args:
            api_key: Google Places API key
            db_session: SQLAlchemy database session for caching
        """
        self.api_key = api_key
        self.db = db_session
        self.base_url = "https://maps.googleapis.com/maps/api/place/details/json"
        self.rate_limiter = RateLimiter(
            max_calls=settings.places_api_rate_limit,
            period=1.0  # per second
        )
        self.client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self):
        """Async context manager entry."""
        self.client = httpx.AsyncClient(timeout=30.0)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        if self.client:
            await self.client.aclose()

    async def get_place_details(
        self,
        place_id: str,
        force_refresh: bool = False
    ) -> Optional[Place]:
        """
        Get place details with caching.

        Args:
            place_id: Google Place ID
            force_refresh: Force API call even if cached

        Returns:
            Place object or None if failed
        """
        # Check cache first
        if not force_refresh:
            cached = self._get_from_cache(place_id)
            if cached:
                return cached

        # Fetch from API
        try:
            await self.rate_limiter.acquire()

            params = {
                'place_id': place_id,
                'key': self.api_key,
                'fields': ','.join([
                    'place_id',
                    'name',
                    'formatted_address',
                    'types',
                    'geometry',
                    'rating',
                    'user_ratings_total',
                    'price_level',
                    'photos',
                    'business_status',
                    'opening_hours',
                    'website',
                    'formatted_phone_number',
                ])
            }

            response = await self.client.get(self.base_url, params=params)
            response.raise_for_status()

            data = response.json()

            if data['status'] != 'OK':
                console.print(f"[yellow]API returned status {data['status']} for {place_id}")
                self._mark_failed(place_id)
                return None

            result = data['result']

            # Parse into Place model
            place = self._parse_place_result(result)

            # Cache it
            self._save_to_cache(place, raw_response=result)

            return place

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                console.print("[yellow]Rate limit hit, backing off...")
                await asyncio.sleep(2.0)
            else:
                console.print(f"[red]HTTP error for {place_id}: {e}")
            self._mark_failed(place_id)
            return None

        except Exception as e:
            console.print(f"[red]Error fetching {place_id}: {e}")
            self._mark_failed(place_id)
            return None

    async def batch_fetch_places(
        self,
        place_ids: List[str],
        batch_size: int = 50,
        force_refresh: bool = False
    ) -> Dict[str, Place]:
        """
        Fetch multiple places efficiently with progress tracking.

        Args:
            place_ids: List of place IDs to fetch
            batch_size: Number of concurrent requests
            force_refresh: Force API calls even if cached

        Returns:
            Dictionary mapping place_id to Place object
        """
        results = {}
        total = len(place_ids)

        console.print(f"[cyan]Fetching details for {total:,} places...")
        console.print(f"[cyan]Batch size: {batch_size} concurrent requests")
        console.print(f"[cyan]Rate limit: {settings.places_api_rate_limit} requests/second")

        # Filter out cached places unless force_refresh
        if not force_refresh:
            uncached = []
            for place_id in place_ids:
                cached = self._get_from_cache(place_id)
                if cached:
                    results[place_id] = cached
                else:
                    uncached.append(place_id)

            console.print(f"[green]Found {len(results):,} cached places")
            console.print(f"[cyan]Fetching {len(uncached):,} places from API")
            place_ids = uncached

        if not place_ids:
            console.print("[green]All places already cached!")
            return results

        # Estimate cost and time
        cost = len(place_ids) * 0.017
        estimated_time = len(place_ids) / settings.places_api_rate_limit
        console.print(f"[yellow]Estimated cost: ${cost:.2f}")
        console.print(f"[yellow]Estimated time: {estimated_time:.1f} seconds")
        console.print()

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console
        ) as progress:
            task = progress.add_task("[cyan]Fetching places...", total=len(place_ids))

            # Process in batches
            for i in range(0, len(place_ids), batch_size):
                batch = place_ids[i:i + batch_size]

                # Create tasks for concurrent execution
                tasks = [self.get_place_details(pid, force_refresh) for pid in batch]

                # Execute batch concurrently
                batch_results = await asyncio.gather(*tasks, return_exceptions=True)

                # Process results
                for place_id, result in zip(batch, batch_results):
                    if isinstance(result, Exception):
                        console.print(f"[red]Exception for {place_id}: {result}")
                    elif result is not None:
                        results[place_id] = result

                    progress.update(task, advance=1)

                # Small delay between batches to be nice to the API
                if i + batch_size < len(place_ids):
                    await asyncio.sleep(0.1)

        success_count = len([r for r in results.values() if r is not None])
        console.print()
        console.print(f"[bold green]Successfully fetched {success_count:,}/{total:,} places")

        return results

    def _get_from_cache(self, place_id: str) -> Optional[Place]:
        """Get place from database cache."""
        try:
            place_model = self.db.query(PlaceModel).filter(
                PlaceModel.place_id == place_id
            ).first()

            if place_model:
                # Check if cache is still fresh (within TTL)
                age = datetime.utcnow() - place_model.last_updated
                if age.total_seconds() < settings.cache_ttl:
                    # Convert ORM model to Pydantic model
                    return Place(
                        place_id=place_model.place_id,
                        name=place_model.name,
                        formatted_address=place_model.formatted_address,
                        types=place_model.types or [],
                        location=None,  # Would need to parse geography
                        rating=place_model.rating,
                        user_ratings_total=place_model.user_ratings_total,
                        price_level=place_model.price_level,
                        photo_references=place_model.photo_references or [],
                        business_status=place_model.business_status,
                        raw_response=place_model.api_response
                    )

            return None

        except Exception as e:
            console.print(f"[yellow]Cache read error for {place_id}: {e}")
            return None

    def _save_to_cache(self, place: Place, raw_response: dict):
        """Save place to database cache."""
        try:
            from geoalchemy2.shape import from_shape
            from shapely.geometry import Point

            # Convert location to PostGIS geography if available
            location_geog = None
            if place.location:
                point = Point(place.location.longitude, place.location.latitude)
                location_geog = from_shape(point, srid=4326)

            # Create or update place in database
            place_model = self.db.query(PlaceModel).filter(
                PlaceModel.place_id == place.place_id
            ).first()

            if place_model:
                # Update existing
                place_model.name = place.name
                place_model.formatted_address = place.formatted_address
                place_model.types = place.types
                place_model.location = location_geog
                place_model.rating = place.rating
                place_model.user_ratings_total = place.user_ratings_total
                place_model.price_level = place.price_level
                place_model.photo_references = place.photo_references
                place_model.business_status = place.business_status
                place_model.api_response = raw_response
                place_model.last_updated = datetime.utcnow()
            else:
                # Create new
                place_model = PlaceModel(
                    place_id=place.place_id,
                    name=place.name,
                    formatted_address=place.formatted_address,
                    types=place.types,
                    location=location_geog,
                    rating=place.rating,
                    user_ratings_total=place.user_ratings_total,
                    price_level=place.price_level,
                    photo_references=place.photo_references,
                    business_status=place.business_status,
                    api_response=raw_response,
                    last_updated=datetime.utcnow(),
                    fetch_attempts=1
                )
                self.db.add(place_model)

            self.db.commit()

        except Exception as e:
            console.print(f"[red]Cache write error for {place.place_id}: {e}")
            self.db.rollback()

    def _mark_failed(self, place_id: str):
        """Mark a place fetch attempt as failed."""
        try:
            place_model = self.db.query(PlaceModel).filter(
                PlaceModel.place_id == place_id
            ).first()

            if place_model:
                place_model.fetch_attempts += 1
            else:
                place_model = PlaceModel(
                    place_id=place_id,
                    fetch_attempts=1
                )
                self.db.add(place_model)

            self.db.commit()

        except Exception as e:
            console.print(f"[yellow]Error marking failed for {place_id}: {e}")
            self.db.rollback()

    @staticmethod
    def _parse_place_result(result: dict) -> Place:
        """Parse Google Places API result into Place model."""
        from ..core.models import Coordinate

        # Extract geometry
        location = None
        if 'geometry' in result and 'location' in result['geometry']:
            loc = result['geometry']['location']
            location = Coordinate(latitude=loc['lat'], longitude=loc['lng'])

        # Extract photo references
        photo_references = []
        if 'photos' in result:
            photo_references = [photo['photo_reference'] for photo in result['photos']]

        return Place(
            place_id=result['place_id'],
            name=result.get('name'),
            formatted_address=result.get('formatted_address'),
            types=result.get('types', []),
            location=location,
            rating=result.get('rating'),
            user_ratings_total=result.get('user_ratings_total'),
            price_level=result.get('price_level'),
            photo_references=photo_references,
            business_status=result.get('business_status'),
            opening_hours=result.get('opening_hours'),
            website=result.get('website'),
            phone_number=result.get('formatted_phone_number'),
            raw_response=result
        )
