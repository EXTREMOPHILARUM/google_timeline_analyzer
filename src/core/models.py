"""
Pydantic models for Google Timeline data structures.

These models provide validation and type safety for all timeline entities.
"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, field_validator


class Coordinate(BaseModel):
    """Represents a geographic coordinate (latitude, longitude)."""

    latitude: float = Field(..., ge=-90, le=90, description="Latitude in decimal degrees")
    longitude: float = Field(..., ge=-180, le=180, description="Longitude in decimal degrees")

    @classmethod
    def from_latlng_string(cls, latlng_str: str) -> "Coordinate":
        """Parse from Google's 'latitude°, longitude°' format."""
        # Format: "19.0669029°, 72.8513023°"
        parts = latlng_str.replace("°", "").split(",")
        return cls(
            latitude=float(parts[0].strip()),
            longitude=float(parts[1].strip())
        )

    def __str__(self) -> str:
        return f"{self.latitude}°, {self.longitude}°"


class VisitCandidate(BaseModel):
    """A candidate place for a visit location."""

    place_id: str
    semantic_type: str  # HOME, WORK, UNKNOWN, etc.
    probability: float = Field(..., ge=0, le=1)
    place_location: Optional[Coordinate] = None

    @field_validator('place_location', mode='before')
    @classmethod
    def parse_location(cls, v):
        if v is None:
            return None
        if isinstance(v, dict) and 'latLng' in v:
            return Coordinate.from_latlng_string(v['latLng'])
        return v


class Visit(BaseModel):
    """A visit to a specific location."""

    start_time: datetime
    end_time: datetime
    timezone_offset_minutes: int
    hierarchy_level: int
    probability: float = Field(..., ge=0, le=1)
    top_candidate: Optional[VisitCandidate] = None

    @property
    def duration_seconds(self) -> float:
        """Calculate visit duration in seconds."""
        return (self.end_time - self.start_time).total_seconds()

    @property
    def place_id(self) -> Optional[str]:
        """Get the place ID from top candidate if available."""
        return self.top_candidate.place_id if self.top_candidate else None

    @property
    def semantic_type(self) -> Optional[str]:
        """Get semantic type from top candidate if available."""
        return self.top_candidate.semantic_type if self.top_candidate else None


class ActivityCandidate(BaseModel):
    """A candidate activity type for movement."""

    type: str  # IN_PASSENGER_VEHICLE, WALKING, etc.
    probability: float = Field(..., ge=0, le=1)


class ActivityLocation(BaseModel):
    """Location information for an activity."""

    lat_lng: Coordinate

    @field_validator('lat_lng', mode='before')
    @classmethod
    def parse_location(cls, v):
        if isinstance(v, str):
            return Coordinate.from_latlng_string(v)
        return v


class Activity(BaseModel):
    """Movement/activity segment between locations."""

    start_time: datetime
    end_time: datetime
    timezone_offset_minutes: int
    start_location: Optional[ActivityLocation] = None
    end_location: Optional[ActivityLocation] = None
    distance_meters: Optional[float] = None
    top_candidate: ActivityCandidate

    @property
    def duration_seconds(self) -> float:
        """Calculate activity duration in seconds."""
        return (self.end_time - self.start_time).total_seconds()

    @property
    def activity_type(self) -> str:
        """Get the activity type."""
        return self.top_candidate.type


class TimelinePathPoint(BaseModel):
    """A single GPS point in a timeline path."""

    point: Coordinate
    time: datetime

    @field_validator('point', mode='before')
    @classmethod
    def parse_point(cls, v):
        if isinstance(v, str):
            return Coordinate.from_latlng_string(v)
        return v


class TimelinePath(BaseModel):
    """A sequence of GPS points showing movement."""

    start_time: datetime
    end_time: datetime
    timeline_path: list[TimelinePathPoint]


class TripDestination(BaseModel):
    """A destination in a timeline memory trip."""

    place_id: str

    @classmethod
    def from_dict(cls, data: dict) -> "TripDestination":
        """Parse from Google's format."""
        if 'identifier' in data and 'placeId' in data['identifier']:
            return cls(place_id=data['identifier']['placeId'])
        return cls(place_id=data.get('placeId', ''))


class TimelineMemory(BaseModel):
    """Google-identified trip from timeline memory."""

    start_time: datetime
    end_time: datetime
    distance_from_origin_kms: Optional[int] = None
    destinations: list[TripDestination] = Field(default_factory=list)

    @property
    def destination_place_ids(self) -> list[str]:
        """Get list of destination place IDs."""
        return [dest.place_id for dest in self.destinations]


class ParkingEvent(BaseModel):
    """A parking event location."""

    location: Coordinate
    start_time: datetime

    @field_validator('location', mode='before')
    @classmethod
    def parse_location(cls, v):
        if isinstance(v, dict) and 'latLng' in v:
            return Coordinate.from_latlng_string(v['latLng'])
        return v


class Place(BaseModel):
    """Google Place details from Places API."""

    place_id: str
    name: Optional[str] = None
    formatted_address: Optional[str] = None
    types: list[str] = Field(default_factory=list)
    location: Optional[Coordinate] = None
    rating: Optional[float] = Field(None, ge=0, le=5)
    user_ratings_total: Optional[int] = Field(None, ge=0)
    price_level: Optional[int] = Field(None, ge=0, le=4)
    photo_references: list[str] = Field(default_factory=list)
    business_status: Optional[str] = None
    opening_hours: Optional[dict] = None
    website: Optional[str] = None
    phone_number: Optional[str] = None
    raw_response: Optional[dict] = None  # Store full API response


class Trip(BaseModel):
    """A detected trip (algorithm-generated)."""

    id: Optional[int] = None
    start_time: datetime
    end_time: datetime
    origin_place_id: Optional[str] = None
    destination_place_ids: list[str] = Field(default_factory=list)
    is_multi_day: bool = False
    total_distance_meters: float = 0.0
    segment_ids: list[int] = Field(default_factory=list)
    primary_transport_mode: Optional[str] = None
    detection_algorithm: str  # 'memory', 'home', 'overnight', 'distance'

    @property
    def duration_seconds(self) -> float:
        """Calculate trip duration in seconds."""
        return (self.end_time - self.start_time).total_seconds()

    @property
    def duration_hours(self) -> float:
        """Calculate trip duration in hours."""
        return self.duration_seconds / 3600

    @property
    def distance_km(self) -> float:
        """Get distance in kilometers."""
        return self.total_distance_meters / 1000


class UserProfile(BaseModel):
    """User's location profile (home, work, etc.)."""

    home_place_id: Optional[str] = None
    work_place_id: Optional[str] = None
    home_location: Optional[Coordinate] = None
    work_location: Optional[Coordinate] = None


class TravelModeAffinity(BaseModel):
    """User's affinity for a travel mode."""

    mode: str
    affinity: float = Field(..., ge=0, le=1)


class Persona(BaseModel):
    """User's travel behavior profile."""

    travel_mode_affinities: list[TravelModeAffinity] = Field(default_factory=list)

    def get_preferred_modes(self, top_n: int = 3) -> list[tuple[str, float]]:
        """Get top N preferred travel modes."""
        sorted_modes = sorted(
            self.travel_mode_affinities,
            key=lambda x: x.affinity,
            reverse=True
        )
        return [(m.mode, m.affinity) for m in sorted_modes[:top_n]]


class TimelineData(BaseModel):
    """Complete timeline export data."""

    semantic_segments: list[dict] = Field(default_factory=list)
    user_location_profile: Optional[dict] = None
    persona: Optional[Persona] = None

    def extract_visits(self) -> list[Visit]:
        """Extract all visit segments."""
        visits = []
        for segment in self.semantic_segments:
            if 'visit' in segment:
                try:
                    visit = Visit(
                        start_time=datetime.fromisoformat(segment['startTime']),
                        end_time=datetime.fromisoformat(segment['endTime']),
                        timezone_offset_minutes=segment.get('startTimeTimezoneUtcOffsetMinutes', 0),
                        hierarchy_level=segment['visit'].get('hierarchyLevel', 0),
                        probability=segment['visit'].get('probability', 0.0),
                        top_candidate=VisitCandidate(**segment['visit']['topCandidate'])
                            if 'topCandidate' in segment['visit'] else None
                    )
                    visits.append(visit)
                except Exception as e:
                    # Log parsing error but continue
                    print(f"Warning: Failed to parse visit segment: {e}")
                    continue
        return visits

    def extract_activities(self) -> list[Activity]:
        """Extract all activity segments."""
        activities = []
        for segment in self.semantic_segments:
            if 'activity' in segment:
                try:
                    activity_data = segment['activity']
                    activity = Activity(
                        start_time=datetime.fromisoformat(segment['startTime']),
                        end_time=datetime.fromisoformat(segment['endTime']),
                        timezone_offset_minutes=segment.get('startTimeTimezoneUtcOffsetMinutes', 0),
                        start_location=ActivityLocation(lat_lng=activity_data['start']['latLng'])
                            if 'start' in activity_data and 'latLng' in activity_data['start'] else None,
                        end_location=ActivityLocation(lat_lng=activity_data['end']['latLng'])
                            if 'end' in activity_data and 'latLng' in activity_data['end'] else None,
                        distance_meters=activity_data.get('distanceMeters'),
                        top_candidate=ActivityCandidate(**activity_data['topCandidate'])
                    )
                    activities.append(activity)
                except Exception as e:
                    print(f"Warning: Failed to parse activity segment: {e}")
                    continue
        return activities

    def extract_timeline_memories(self) -> list[TimelineMemory]:
        """Extract timeline memory (Google-identified trips)."""
        memories = []
        for segment in self.semantic_segments:
            if 'timelineMemory' in segment and 'trip' in segment['timelineMemory']:
                try:
                    trip_data = segment['timelineMemory']['trip']
                    destinations = [
                        TripDestination.from_dict(dest)
                        for dest in trip_data.get('destinations', [])
                    ]
                    memory = TimelineMemory(
                        start_time=datetime.fromisoformat(segment['startTime']),
                        end_time=datetime.fromisoformat(segment['endTime']),
                        distance_from_origin_kms=trip_data.get('distanceFromOriginKms'),
                        destinations=destinations
                    )
                    memories.append(memory)
                except Exception as e:
                    print(f"Warning: Failed to parse timeline memory: {e}")
                    continue
        return memories
