"""
Configuration management for the Google Timeline Analyzer.

Loads settings from environment variables with sensible defaults.
"""

import os
from typing import Optional
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Database Configuration
    database_url: str = "postgresql://timeline_user:timeline_pass@localhost:5432/timeline_analyzer"

    # Redis Configuration
    redis_url: str = "redis://localhost:6379"

    # Google Places API Configuration
    google_places_api_key: Optional[str] = None
    places_api_rate_limit: int = 100  # requests per second
    cache_ttl: int = 2592000  # 30 days in seconds

    # Application Configuration
    batch_size: int = 1000  # for database inserts
    log_level: str = "INFO"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False


# Global settings instance
settings = Settings()


def get_settings() -> Settings:
    """Get the global settings instance."""
    return settings
