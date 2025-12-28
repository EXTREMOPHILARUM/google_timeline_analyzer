"""
Database session management and ORM models using SQLAlchemy with PostGIS support.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    Float,
    Boolean,
    DateTime,
    ForeignKey,
    ARRAY,
    Computed,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship, Session
from geoalchemy2 import Geography

from .config import get_settings

settings = get_settings()

# Create SQLAlchemy engine
engine = create_engine(
    settings.database_url,
    echo=False,  # Set to True for SQL query logging
    pool_pre_ping=True,  # Verify connections before using
)

# Create session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base class for ORM models
Base = declarative_base()


def get_db() -> Session:
    """
    Get a database session.

    Usage:
        with get_db() as db:
            # Use db session
            pass
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class TimelineSegment(Base):
    """Core timeline segments table (visits, activities, paths, memories)."""

    __tablename__ = "timeline_segments"

    id = Column(Integer, primary_key=True)
    segment_type = Column(String(20), nullable=False, index=True)
    start_time = Column(DateTime(timezone=True), nullable=False, index=True)
    end_time = Column(DateTime(timezone=True), nullable=False)
    duration_seconds = Column(
        Integer,
        Computed("EXTRACT(EPOCH FROM (end_time - start_time))"),
        stored=True
    )
    timezone_offset_minutes = Column(Integer)
    raw_data = Column(JSONB)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)

    # Relationships
    visit = relationship("VisitModel", back_populates="segment", uselist=False)
    activity = relationship("ActivityModel", back_populates="segment", uselist=False)
    timeline_memory = relationship("TimelineMemoryModel", back_populates="segment", uselist=False)


class VisitModel(Base):
    """Visits to specific locations."""

    __tablename__ = "visits"

    id = Column(Integer, primary_key=True)
    segment_id = Column(Integer, ForeignKey("timeline_segments.id", ondelete="CASCADE"), unique=True)
    place_id = Column(String(100), index=True)
    semantic_type = Column(String(50), index=True)
    probability = Column(Float)
    location = Column(Geography(geometry_type="POINT", srid=4326))
    hierarchy_level = Column(Integer)

    # Relationship
    segment = relationship("TimelineSegment", back_populates="visit")


class ActivityModel(Base):
    """Movement/activity segments."""

    __tablename__ = "activities"

    id = Column(Integer, primary_key=True)
    segment_id = Column(Integer, ForeignKey("timeline_segments.id", ondelete="CASCADE"), unique=True)
    start_location = Column(Geography(geometry_type="POINT", srid=4326))
    end_location = Column(Geography(geometry_type="POINT", srid=4326))
    distance_meters = Column(Float, index=True)
    activity_type = Column(String(50), index=True)
    probability = Column(Float)

    # Relationship
    segment = relationship("TimelineSegment", back_populates="activity")


class TimelinePathModel(Base):
    """Timeline path GPS points."""

    __tablename__ = "timeline_paths"

    id = Column(Integer, primary_key=True)
    segment_id = Column(Integer, ForeignKey("timeline_segments.id", ondelete="CASCADE"))
    location = Column(Geography(geometry_type="POINT", srid=4326))
    recorded_at = Column(DateTime(timezone=True), nullable=False, index=True)


class TimelineMemoryModel(Base):
    """Google-identified trips from timeline memories."""

    __tablename__ = "timeline_memories"

    id = Column(Integer, primary_key=True)
    segment_id = Column(Integer, ForeignKey("timeline_segments.id", ondelete="CASCADE"), unique=True)
    distance_from_origin_kms = Column(Integer)
    destination_place_ids = Column(ARRAY(String))

    # Relationship
    segment = relationship("TimelineSegment", back_populates="timeline_memory")


class PlaceModel(Base):
    """Cached Google Places API data."""

    __tablename__ = "places"

    place_id = Column(String(100), primary_key=True)
    name = Column(String(500))
    formatted_address = Column(String)
    types = Column(ARRAY(String))
    location = Column(Geography(geometry_type="POINT", srid=4326))
    rating = Column(Float)
    user_ratings_total = Column(Integer)
    price_level = Column(Integer)
    photo_references = Column(ARRAY(String))
    business_status = Column(String(50))
    api_response = Column(JSONB)
    last_updated = Column(DateTime(timezone=True), default=datetime.utcnow)
    fetch_attempts = Column(Integer, default=0)


class TripModel(Base):
    """Detected trips (algorithm-generated)."""

    __tablename__ = "trips"

    id = Column(Integer, primary_key=True)
    start_time = Column(DateTime(timezone=True), nullable=False, index=True)
    end_time = Column(DateTime(timezone=True), nullable=False)
    origin_place_id = Column(String(100), ForeignKey("places.place_id"))
    is_multi_day = Column(Boolean)
    total_distance_meters = Column(Float)
    primary_transport_mode = Column(String(50))
    detection_algorithm = Column(String(50))
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)

    # Relationships
    destinations = relationship("TripDestinationModel", back_populates="trip")
    segments = relationship("TripSegmentModel", back_populates="trip")


class TripDestinationModel(Base):
    """Trip destinations (many-to-many)."""

    __tablename__ = "trip_destinations"

    trip_id = Column(Integer, ForeignKey("trips.id", ondelete="CASCADE"), primary_key=True)
    place_id = Column(String(100), ForeignKey("places.place_id"), primary_key=True)
    visit_order = Column(Integer)

    # Relationship
    trip = relationship("TripModel", back_populates="destinations")


class TripSegmentModel(Base):
    """Trip segments (which segments belong to which trip)."""

    __tablename__ = "trip_segments"

    trip_id = Column(Integer, ForeignKey("trips.id", ondelete="CASCADE"), primary_key=True)
    segment_id = Column(Integer, ForeignKey("timeline_segments.id"), primary_key=True)
    segment_order = Column(Integer)

    # Relationship
    trip = relationship("TripModel", back_populates="segments")


class UserProfileModel(Base):
    """User location profile (home, work)."""

    __tablename__ = "user_profile"

    id = Column(Integer, primary_key=True)
    home_place_id = Column(String(100), ForeignKey("places.place_id"))
    work_place_id = Column(String(100), ForeignKey("places.place_id"))
    home_location = Column(Geography(geometry_type="POINT", srid=4326))
    work_location = Column(Geography(geometry_type="POINT", srid=4326))


class TravelModeAffinityModel(Base):
    """User's travel mode affinities."""

    __tablename__ = "travel_mode_affinities"

    mode = Column(String(50), primary_key=True)
    affinity = Column(Float)


def init_db():
    """
    Initialize the database by creating all tables.

    Note: In production, use Alembic for migrations instead.
    """
    Base.metadata.create_all(bind=engine)


def drop_all_tables():
    """
    Drop all tables (use with caution!).
    """
    Base.metadata.drop_all(bind=engine)
