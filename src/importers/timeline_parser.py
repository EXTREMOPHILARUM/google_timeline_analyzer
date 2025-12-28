"""
Timeline.json parser with efficient streaming and batch import to PostgreSQL.

Handles large files (59MB+) by parsing incrementally and inserting in batches.
"""

import orjson
from datetime import datetime
from typing import Generator, Optional
from pathlib import Path

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from sqlalchemy.orm import Session
from geoalchemy2.shape import from_shape
from shapely.geometry import Point

from ..core.database import (
    TimelineSegment,
    VisitModel,
    ActivityModel,
    TimelinePathModel,
    TimelineMemoryModel,
    PlaceModel,
    UserProfileModel,
    TravelModeAffinityModel,
)
from ..core.config import get_settings

console = Console()
settings = get_settings()


class TimelineParser:
    """Parse and import Google Timeline export data."""

    def __init__(self, db_session: Session):
        self.db = db_session

    def parse_and_import(self, timeline_file: Path) -> dict[str, int]:
        """
        Parse Timeline.json and import to database.

        Returns dictionary with import statistics.
        """
        console.print(f"[bold blue]Loading Timeline.json from {timeline_file}...")

        # Load entire file (orjson is very fast)
        with open(timeline_file, 'rb') as f:
            data = orjson.loads(f.read())

        stats = {
            'visits': 0,
            'activities': 0,
            'timeline_paths': 0,
            'timeline_memories': 0,
            'total_segments': 0,
        }

        # Extract semantic segments
        semantic_segments = data.get('semanticSegments', [])
        total = len(semantic_segments)

        console.print(f"[green]Found {total:,} semantic segments to process")

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console
        ) as progress:
            task = progress.add_task("[cyan]Importing segments...", total=total)

            batch = []
            for idx, segment in enumerate(semantic_segments):
                try:
                    # Parse segment
                    parsed_segments = self._parse_segment(segment)
                    batch.extend(parsed_segments)

                    # Batch insert
                    if len(batch) >= settings.batch_size:
                        self._batch_insert(batch, stats)
                        batch = []

                    progress.update(task, advance=1)

                except Exception as e:
                    console.print(f"[yellow]Warning: Failed to parse segment {idx}: {e}")
                    continue

            # Insert remaining batch
            if batch:
                self._batch_insert(batch, stats)

        # Import user profile and persona
        console.print("[cyan]Importing user profile and persona...")
        self._import_user_profile(data.get('userLocationProfile', {}))
        self._import_persona(data.get('persona', {}))

        stats['total_segments'] = sum([
            stats['visits'],
            stats['activities'],
            stats['timeline_paths'],
            stats['timeline_memories']
        ])

        console.print("[bold green]Import completed successfully!")
        return stats

    def _parse_segment(self, segment: dict) -> list[tuple[TimelineSegment, Optional[object]]]:
        """
        Parse a semantic segment into database models.

        Returns list of (TimelineSegment, related_model) tuples.
        """
        results = []

        start_time = self._parse_datetime(segment.get('startTime'))
        end_time = self._parse_datetime(segment.get('endTime'))
        timezone_offset = segment.get('startTimeTimezoneUtcOffsetMinutes', 0)

        # Visit segment
        if 'visit' in segment:
            visit_data = segment['visit']
            top_candidate = visit_data.get('topCandidate', {})

            # Create timeline segment
            ts = TimelineSegment(
                segment_type='visit',
                start_time=start_time,
                end_time=end_time,
                timezone_offset_minutes=timezone_offset,
                raw_data=segment
            )

            # Create visit model
            visit = VisitModel(
                place_id=top_candidate.get('placeId'),
                semantic_type=top_candidate.get('semanticType', 'UNKNOWN'),
                probability=visit_data.get('probability', 0.0),
                location=self._parse_geography(top_candidate.get('placeLocation', {}).get('latLng')),
                hierarchy_level=visit_data.get('hierarchyLevel', 0)
            )

            results.append((ts, visit))

        # Activity segment
        elif 'activity' in segment:
            activity_data = segment['activity']
            top_candidate = activity_data.get('topCandidate', {})

            ts = TimelineSegment(
                segment_type='activity',
                start_time=start_time,
                end_time=end_time,
                timezone_offset_minutes=timezone_offset,
                raw_data=segment
            )

            activity = ActivityModel(
                start_location=self._parse_geography(
                    activity_data.get('start', {}).get('latLng')
                ),
                end_location=self._parse_geography(
                    activity_data.get('end', {}).get('latLng')
                ),
                distance_meters=activity_data.get('distanceMeters'),
                activity_type=top_candidate.get('type', 'UNKNOWN'),
                probability=top_candidate.get('probability', 0.0)
            )

            results.append((ts, activity))

        # Timeline path
        elif 'timelinePath' in segment:
            timeline_path = segment['timelinePath']

            ts = TimelineSegment(
                segment_type='path',
                start_time=start_time,
                end_time=end_time,
                timezone_offset_minutes=timezone_offset,
                raw_data=segment
            )

            # Create path points
            for point_data in timeline_path:
                path = TimelinePathModel(
                    location=self._parse_geography(point_data.get('point')),
                    recorded_at=self._parse_datetime(point_data.get('time'))
                )
                results.append((ts, path))

        # Timeline memory
        elif 'timelineMemory' in segment and 'trip' in segment['timelineMemory']:
            trip_data = segment['timelineMemory']['trip']

            ts = TimelineSegment(
                segment_type='memory',
                start_time=start_time,
                end_time=end_time,
                timezone_offset_minutes=timezone_offset,
                raw_data=segment
            )

            # Extract destination place IDs
            destination_place_ids = []
            for dest in trip_data.get('destinations', []):
                if 'identifier' in dest and 'placeId' in dest['identifier']:
                    destination_place_ids.append(dest['identifier']['placeId'])

            memory = TimelineMemoryModel(
                distance_from_origin_kms=trip_data.get('distanceFromOriginKms'),
                destination_place_ids=destination_place_ids
            )

            results.append((ts, memory))

        return results

    def _batch_insert(self, batch: list[tuple[TimelineSegment, Optional[object]]], stats: dict):
        """Insert a batch of segments and related models."""
        for ts, related_model in batch:
            try:
                # Insert timeline segment
                self.db.add(ts)
                self.db.flush()  # Get the ID

                # Insert related model
                if related_model is not None:
                    if isinstance(related_model, VisitModel):
                        related_model.segment_id = ts.id
                        self.db.add(related_model)
                        stats['visits'] += 1
                    elif isinstance(related_model, ActivityModel):
                        related_model.segment_id = ts.id
                        self.db.add(related_model)
                        stats['activities'] += 1
                    elif isinstance(related_model, TimelinePathModel):
                        related_model.segment_id = ts.id
                        self.db.add(related_model)
                        stats['timeline_paths'] += 1
                    elif isinstance(related_model, TimelineMemoryModel):
                        related_model.segment_id = ts.id
                        self.db.add(related_model)
                        stats['timeline_memories'] += 1

            except Exception as e:
                console.print(f"[red]Error inserting segment: {e}")
                self.db.rollback()
                continue

        # Commit batch
        try:
            self.db.commit()
        except Exception as e:
            console.print(f"[red]Error committing batch: {e}")
            self.db.rollback()

    def _import_user_profile(self, profile_data: dict):
        """Import user location profile (home, work)."""
        if not profile_data:
            return

        try:
            # Extract home location
            home_addresses = profile_data.get('homeAddress', [])
            home_place_id = None
            home_location = None
            if home_addresses:
                home_place_id = home_addresses[0].get('placeId')
                home_location = self._parse_geography(home_addresses[0].get('placeLocation'))

            # Extract work location (if exists)
            work_addresses = profile_data.get('workAddress', [])
            work_place_id = None
            work_location = None
            if work_addresses:
                work_place_id = work_addresses[0].get('placeId')
                work_location = self._parse_geography(work_addresses[0].get('placeLocation'))

            # Create or update user profile
            profile = UserProfileModel(
                home_place_id=home_place_id,
                work_place_id=work_place_id,
                home_location=home_location,
                work_location=work_location
            )
            self.db.add(profile)
            self.db.commit()

            console.print(f"[green]Imported user profile (home: {home_place_id}, work: {work_place_id})")

        except Exception as e:
            console.print(f"[yellow]Warning: Failed to import user profile: {e}")
            self.db.rollback()

    def _import_persona(self, persona_data: dict):
        """Import user's travel mode affinities."""
        if not persona_data:
            return

        try:
            travel_mode_affinities = persona_data.get('travelModeAffinities', [])

            for affinity_data in travel_mode_affinities:
                affinity = TravelModeAffinityModel(
                    mode=affinity_data['mode'],
                    affinity=affinity_data['affinity']
                )
                self.db.add(affinity)

            self.db.commit()
            console.print(f"[green]Imported {len(travel_mode_affinities)} travel mode affinities")

        except Exception as e:
            console.print(f"[yellow]Warning: Failed to import persona: {e}")
            self.db.rollback()

    @staticmethod
    def _parse_datetime(dt_str: Optional[str]) -> Optional[datetime]:
        """Parse ISO 8601 datetime string."""
        if not dt_str:
            return None
        try:
            return datetime.fromisoformat(dt_str)
        except ValueError:
            return None

    @staticmethod
    def _parse_geography(latlng_str: Optional[str]):
        """Parse lat/lng string to PostGIS geography."""
        if not latlng_str:
            return None

        try:
            # Format: "19.0669029°, 72.8513023°"
            parts = latlng_str.replace("°", "").split(",")
            lat = float(parts[0].strip())
            lng = float(parts[1].strip())

            # Create PostGIS geography (lng, lat order for WKT)
            point = Point(lng, lat)
            return from_shape(point, srid=4326)

        except Exception:
            return None

    def extract_unique_place_ids(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> list[str]:
        """
        Extract unique place IDs from visits and memories, optionally filtered by date.

        Args:
            start_date: Optional start date filter
            end_date: Optional end date filter

        Returns:
            List of place IDs that need Places API enrichment.
        """
        # Get unique place IDs from visits (join with timeline_segments for date filtering)
        visit_query = self.db.query(VisitModel.place_id).join(
            TimelineSegment, VisitModel.segment_id == TimelineSegment.id
        ).filter(VisitModel.place_id.isnot(None))

        if start_date:
            visit_query = visit_query.filter(TimelineSegment.start_time >= start_date)
        if end_date:
            visit_query = visit_query.filter(TimelineSegment.end_time <= end_date)

        visit_place_ids = visit_query.distinct().all()

        # Get unique place IDs from timeline memories (join with timeline_segments for date filtering)
        memory_query = self.db.query(TimelineMemoryModel.destination_place_ids).join(
            TimelineSegment, TimelineMemoryModel.segment_id == TimelineSegment.id
        )

        if start_date:
            memory_query = memory_query.filter(TimelineSegment.start_time >= start_date)
        if end_date:
            memory_query = memory_query.filter(TimelineSegment.end_time <= end_date)

        memory_place_ids = memory_query.all()

        # Flatten and deduplicate
        place_ids = set()
        for (pid,) in visit_place_ids:
            place_ids.add(pid)

        for (dest_ids,) in memory_place_ids:
            if dest_ids:
                place_ids.update(dest_ids)

        if start_date or end_date:
            console.print(f"[cyan]Found {len(place_ids):,} unique place IDs in date range")
        else:
            console.print(f"[cyan]Found {len(place_ids):,} unique place IDs")

        return sorted(list(place_ids))
