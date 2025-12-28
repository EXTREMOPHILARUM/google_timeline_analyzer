"""
Multi-tier caching for Google Places API responses.

Implements two-tier caching:
1. Redis (optional, for sub-millisecond lookups)
2. PostgreSQL (persistent storage)
"""

import json
from typing import Optional
from datetime import timedelta

from rich.console import Console

from ..core.config import get_settings
from ..core.models import Place

console = Console()
settings = get_settings()


class CacheManager:
    """Multi-tier cache manager with Redis and PostgreSQL."""

    def __init__(self, redis_client: Optional[object] = None):
        """
        Initialize cache manager.

        Args:
            redis_client: Optional Redis client for L1 cache
        """
        self.redis = redis_client
        self.ttl_seconds = settings.cache_ttl

        if self.redis:
            try:
                # Test Redis connection
                self.redis.ping()
                console.print("[green]Redis cache connected")
            except Exception as e:
                console.print(f"[yellow]Redis unavailable, using PostgreSQL only: {e}")
                self.redis = None

    def get(self, place_id: str) -> Optional[Place]:
        """
        Get place from cache (Redis -> PostgreSQL).

        Args:
            place_id: Google Place ID

        Returns:
            Cached Place object or None
        """
        # Try Redis first (L1 cache)
        if self.redis:
            try:
                cached_json = self.redis.get(f"place:{place_id}")
                if cached_json:
                    data = json.loads(cached_json)
                    return Place(**data)
            except Exception as e:
                console.print(f"[yellow]Redis read error for {place_id}: {e}")

        # PostgreSQL cache is handled by PlacesAPIClient
        return None

    def set(self, place: Place):
        """
        Store place in cache (Redis + PostgreSQL).

        Args:
            place: Place object to cache
        """
        # Store in Redis (L1 cache)
        if self.redis:
            try:
                # Convert to JSON
                place_json = place.model_dump_json()

                # Store with TTL (1 day for Redis, PostgreSQL has longer TTL)
                self.redis.setex(
                    f"place:{place.place_id}",
                    86400,  # 24 hours in Redis
                    place_json
                )
            except Exception as e:
                console.print(f"[yellow]Redis write error for {place.place_id}: {e}")

        # PostgreSQL cache is handled by PlacesAPIClient

    def invalidate(self, place_id: str):
        """
        Invalidate cache for a specific place.

        Args:
            place_id: Place ID to invalidate
        """
        if self.redis:
            try:
                self.redis.delete(f"place:{place_id}")
            except Exception as e:
                console.print(f"[yellow]Redis delete error for {place_id}: {e}")

    def clear_all(self):
        """Clear all place caches in Redis (PostgreSQL unaffected)."""
        if self.redis:
            try:
                # Delete all keys matching pattern
                keys = self.redis.keys("place:*")
                if keys:
                    self.redis.delete(*keys)
                    console.print(f"[green]Cleared {len(keys)} places from Redis cache")
            except Exception as e:
                console.print(f"[yellow]Redis clear error: {e}")

    def get_stats(self) -> dict:
        """Get cache statistics."""
        stats = {
            'redis_enabled': self.redis is not None,
            'ttl_seconds': self.ttl_seconds,
        }

        if self.redis:
            try:
                place_keys = self.redis.keys("place:*")
                stats['redis_cached_places'] = len(place_keys)
                stats['redis_memory'] = self.redis.info('memory').get('used_memory_human', 'unknown')
            except Exception as e:
                console.print(f"[yellow]Redis stats error: {e}")

        return stats


def get_redis_client() -> Optional[object]:
    """
    Get Redis client if available.

    Returns:
        Redis client or None if unavailable/disabled
    """
    try:
        import redis

        client = redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2
        )

        # Test connection
        client.ping()
        return client

    except ImportError:
        console.print("[yellow]Redis package not installed, skipping Redis cache")
        return None

    except Exception as e:
        console.print(f"[yellow]Redis connection failed, using PostgreSQL only: {e}")
        return None
