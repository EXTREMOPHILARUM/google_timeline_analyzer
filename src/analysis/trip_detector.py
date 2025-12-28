"""
Trip detection algorithms for identifying travel patterns from timeline data.

Implements 4 complementary algorithms:
1. Timeline Memory Based - Uses Google's pre-identified trips
2. Home-Based Detection - Trips starting/ending at home with significant distance
3. Overnight Stay Detection - Multi-day trips with overnight stays away from home
4. Distance-Based Clustering - Clusters activities far from typical locations
"""

from datetime import datetime, timedelta
from typing import List, Optional, Tuple, Set
from collections import defaultdict

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from sqlalchemy import func
from sqlalchemy.orm import Session
from geoalchemy2.functions import ST_Distance
from shapely.geometry import Point

from ..core.database import (
    TimelineSegment,
    VisitModel,
    ActivityModel,
    TimelineMemoryModel,
    UserProfileModel,
    TripModel,
    TripDestinationModel,
    TripSegmentModel,
)

console = Console()


class TripDetector:
    """Detect trips using multiple algorithms."""

    def __init__(self, db_session: Session):
        """Initialize trip detector."""
        self.db = db_session

    def detect_all_trips(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        min_distance_km: float = 0.5,
        min_duration_hours: float = 0.1,
        distance_threshold_km: float = 5.0
    ) -> dict[str, int]:
        """
        Run all trip detection algorithms.

        Args:
            start_date: Optional start date filter
            end_date: Optional end date filter
            min_distance_km: Minimum trip distance in km
            min_duration_hours: Minimum trip duration in hours
            distance_threshold_km: Distance from home to consider as trip

        Returns:
            Dictionary with counts per algorithm
        """
        stats = {}

        console.print("[bold cyan]Running all trip detection algorithms...[/bold cyan]")
        console.print(f"[dim]Parameters: min_distance={min_distance_km}km, min_duration={min_duration_hours}h, distance_threshold={distance_threshold_km}km[/dim]")
        console.print()

        # Algorithm 1: Timeline Memory
        console.print("[cyan]1. Timeline Memory Based Detection...")
        count = self.detect_timeline_memory_trips(start_date, end_date)
        stats['timeline_memory'] = count
        console.print(f"[green]   Found {count} trips from timeline memories")
        console.print()

        # Algorithm 2: Home-Based
        console.print("[cyan]2. Home-Based Detection...")
        count = self.detect_home_based_trips(start_date, end_date, min_distance_km, min_duration_hours)
        stats['home_based'] = count
        console.print(f"[green]   Found {count} trips from home base")
        console.print()

        # Algorithm 3: Overnight Stays
        console.print("[cyan]3. Overnight Stay Detection...")
        count = self.detect_overnight_trips(start_date, end_date)
        stats['overnight'] = count
        console.print(f"[green]   Found {count} overnight trips")
        console.print()

        # Algorithm 4: Distance-Based
        console.print("[cyan]4. Distance-Based Clustering...")
        count = self.detect_distance_based_trips(start_date, end_date, distance_threshold_km)
        stats['distance_based'] = count
        console.print(f"[green]   Found {count} distance-based trips")
        console.print()

        total = sum(stats.values())
        console.print(f"[bold green]Total trips detected: {total}[/bold green]")

        return stats

    def detect_timeline_memory_trips(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> int:
        """
        Detect trips from Google's timeline memories.

        This is the simplest algorithm - uses trips already identified by Google.
        """
        query = self.db.query(TimelineMemoryModel).join(TimelineSegment)

        # Apply date filters
        if start_date:
            query = query.filter(TimelineSegment.start_time >= start_date)
        if end_date:
            query = query.filter(TimelineSegment.end_time <= end_date)

        memories = query.all()
        trip_count = 0

        for memory in memories:
            # Check if trip already exists
            existing = self.db.query(TripModel).filter(
                TripModel.start_time == memory.segment.start_time,
                TripModel.end_time == memory.segment.end_time,
                TripModel.detection_algorithm == 'timeline_memory'
            ).first()

            if existing:
                continue

            # Calculate if multi-day
            is_multi_day = (
                memory.segment.end_time.date() > memory.segment.start_time.date()
            )

            # Create trip
            trip = TripModel(
                start_time=memory.segment.start_time,
                end_time=memory.segment.end_time,
                is_multi_day=is_multi_day,
                total_distance_meters=(memory.distance_from_origin_kms or 0) * 1000,
                detection_algorithm='timeline_memory',
            )
            self.db.add(trip)
            self.db.flush()

            # Add destinations
            for idx, place_id in enumerate(memory.destination_place_ids or []):
                dest = TripDestinationModel(
                    trip_id=trip.id,
                    place_id=place_id,
                    visit_order=idx
                )
                self.db.add(dest)

            # Link segment
            seg_link = TripSegmentModel(
                trip_id=trip.id,
                segment_id=memory.segment_id,
                segment_order=0
            )
            self.db.add(seg_link)

            trip_count += 1

        self.db.commit()
        return trip_count

    def detect_home_based_trips(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        min_distance_km: float = 50,
        min_duration_hours: float = 4
    ) -> int:
        """
        Detect trips as sequences starting/ending at home.

        Args:
            start_date: Optional start date filter
            end_date: Optional end date filter
            min_distance_km: Minimum trip distance in kilometers
            min_duration_hours: Minimum trip duration in hours
        """
        # Get user home location
        profile = self.db.query(UserProfileModel).first()
        if not profile or (not profile.home_place_id and not profile.home_location):
            console.print("[yellow]   No home location set, skipping home-based detection")
            return 0

        home_place_id = profile.home_place_id
        home_location = profile.home_location
        use_distance_based = home_place_id is None and home_location is not None

        # Get all segments chronologically
        query = self.db.query(TimelineSegment).order_by(TimelineSegment.start_time)

        if start_date:
            query = query.filter(TimelineSegment.start_time >= start_date)
        if end_date:
            query = query.filter(TimelineSegment.end_time <= end_date)

        segments = query.all()

        current_trip_segments = []
        current_trip_distance = 0
        current_trip_destinations = set()
        trip_count = 0

        for segment in segments:
            # Check if this is a home visit
            is_home = False
            if segment.visit:
                if use_distance_based and segment.visit.location and home_location:
                    # Use distance-based check (within 1km of home)
                    distance = self.db.query(
                        ST_Distance(home_location, segment.visit.location)
                    ).scalar()
                    is_home = distance and (distance / 1000) < 1.0
                elif home_place_id:
                    # Use place_id check
                    is_home = segment.visit.place_id == home_place_id

            if is_home:
                # At home - potentially end current trip
                if current_trip_segments:
                    # Calculate trip metrics
                    trip_start = current_trip_segments[0].start_time
                    trip_end = segment.start_time
                    duration_hours = (trip_end - trip_start).total_seconds() / 3600

                    # Check if meets minimum criteria
                    if (current_trip_distance / 1000 >= min_distance_km and
                        duration_hours >= min_duration_hours):

                        # Create trip
                        self._create_trip(
                            start_time=trip_start,
                            end_time=trip_end,
                            segments=current_trip_segments,
                            distance_meters=current_trip_distance,
                            destinations=list(current_trip_destinations),
                            algorithm='home_based'
                        )
                        trip_count += 1

                    # Reset for next trip
                    current_trip_segments = []
                    current_trip_distance = 0
                    current_trip_destinations = set()

            else:
                # Not at home - add to current trip
                current_trip_segments.append(segment)

                # Add distance if activity
                if segment.activity:
                    current_trip_distance += segment.activity.distance_meters or 0

                # Add destination if visit
                if segment.visit and segment.visit.place_id:
                    current_trip_destinations.add(segment.visit.place_id)

        self.db.commit()
        return trip_count

    def detect_overnight_trips(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        min_nights: int = 1
    ) -> int:
        """
        Detect trips with overnight stays away from home.

        Args:
            start_date: Optional start date filter
            end_date: Optional end date filter
            min_nights: Minimum number of nights away
        """
        # Get user home location
        profile = self.db.query(UserProfileModel).first()
        if not profile or (not profile.home_place_id and not profile.home_location):
            console.print("[yellow]   No home location set, skipping overnight detection")
            return 0

        home_place_id = profile.home_place_id
        home_location = profile.home_location
        use_distance_based = home_place_id is None and home_location is not None

        # Find overnight visits (6+ hours between 8pm-8am) not at home
        # Calculate duration in seconds using EXTRACT(EPOCH FROM ...)
        duration_expr = func.extract('epoch', TimelineSegment.end_time - TimelineSegment.start_time)

        if use_distance_based:
            # Use distance-based filtering - query all overnight visits
            query = self.db.query(VisitModel, TimelineSegment).join(
                TimelineSegment, VisitModel.segment_id == TimelineSegment.id
            ).filter(
                func.extract('hour', TimelineSegment.start_time) >= 20,  # After 8pm
                func.extract('hour', TimelineSegment.end_time) <= 8,      # Before 8am
                duration_expr >= 6 * 3600                                  # At least 6 hours
            ).order_by(TimelineSegment.start_time)
        else:
            # Use place_id filtering
            query = self.db.query(VisitModel, TimelineSegment).join(
                TimelineSegment, VisitModel.segment_id == TimelineSegment.id
            ).filter(
                VisitModel.place_id != home_place_id,
                func.extract('hour', TimelineSegment.start_time) >= 20,  # After 8pm
                func.extract('hour', TimelineSegment.end_time) <= 8,      # Before 8am
                duration_expr >= 6 * 3600                                  # At least 6 hours
            ).order_by(TimelineSegment.start_time)

        if start_date:
            query = query.filter(TimelineSegment.start_time >= start_date)
        if end_date:
            query = query.filter(TimelineSegment.end_time <= end_date)

        overnight_visits = query.all()

        # Filter by distance if using distance-based detection
        if use_distance_based:
            filtered_visits = []
            for visit, segment in overnight_visits:
                if visit.location and home_location:
                    distance = self.db.query(
                        ST_Distance(home_location, visit.location)
                    ).scalar()
                    # Only include visits >1km from home
                    if distance and (distance / 1000) >= 1.0:
                        filtered_visits.append((visit, segment))
            overnight_visits = filtered_visits

        # Group into trips
        current_trip_visits = []
        trip_count = 0

        for visit, segment in overnight_visits:
            if not current_trip_visits:
                current_trip_visits.append((visit, segment))
            else:
                last_segment = current_trip_visits[-1][1]
                time_gap = (segment.start_time - last_segment.end_time).total_seconds()

                # If gap > 48 hours, it's a new trip
                if time_gap > 48 * 3600:
                    # Save previous trip if meets criteria
                    if len(current_trip_visits) >= min_nights:
                        self._create_overnight_trip(current_trip_visits)
                        trip_count += 1

                    current_trip_visits = [(visit, segment)]
                else:
                    current_trip_visits.append((visit, segment))

        # Don't forget last trip
        if len(current_trip_visits) >= min_nights:
            self._create_overnight_trip(current_trip_visits)
            trip_count += 1

        self.db.commit()
        return trip_count

    def detect_distance_based_trips(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        distance_threshold_km: float = 100,
        time_gap_hours: float = 6
    ) -> int:
        """
        Cluster activities/visits far from typical locations.

        Args:
            start_date: Optional start date filter
            end_date: Optional end date filter
            distance_threshold_km: Distance from typical location in km
            time_gap_hours: Maximum time gap to consider same trip
        """
        # Get user profile for typical location
        profile = self.db.query(UserProfileModel).first()
        if not profile:
            console.print("[yellow]   No user profile, skipping distance-based detection")
            return 0

        # Calculate typical location (use home if available, otherwise skip)
        if not profile.home_location:
            console.print("[yellow]   No home location, skipping distance-based detection")
            return 0

        # Get all visits and activities
        query = self.db.query(TimelineSegment).filter(
            TimelineSegment.segment_type.in_(['visit', 'activity'])
        ).order_by(TimelineSegment.start_time)

        if start_date:
            query = query.filter(TimelineSegment.start_time >= start_date)
        if end_date:
            query = query.filter(TimelineSegment.end_time <= end_date)

        segments = query.all()

        # Find segments far from home
        far_segments = []
        for segment in segments:
            location = None

            if segment.visit and segment.visit.location:
                location = segment.visit.location
            elif segment.activity and segment.activity.start_location:
                location = segment.activity.start_location

            if location:
                # Calculate distance from home
                distance = self.db.query(
                    ST_Distance(profile.home_location, location)
                ).scalar()

                if distance and distance / 1000 >= distance_threshold_km:
                    far_segments.append(segment)

        # Cluster by time gaps
        current_cluster = []
        trip_count = 0

        for segment in far_segments:
            if not current_cluster:
                current_cluster.append(segment)
            else:
                time_gap = (segment.start_time - current_cluster[-1].end_time).total_seconds() / 3600

                if time_gap <= time_gap_hours:
                    current_cluster.append(segment)
                else:
                    # Save previous cluster as trip
                    if len(current_cluster) >= 2:
                        self._create_clustered_trip(current_cluster)
                        trip_count += 1

                    current_cluster = [segment]

        # Don't forget last cluster
        if len(current_cluster) >= 2:
            self._create_clustered_trip(current_cluster)
            trip_count += 1

        self.db.commit()
        return trip_count

    def _create_trip(
        self,
        start_time: datetime,
        end_time: datetime,
        segments: List[TimelineSegment],
        distance_meters: float,
        destinations: List[str],
        algorithm: str
    ):
        """Helper to create a trip record."""
        # Check for duplicates
        existing = self.db.query(TripModel).filter(
            TripModel.start_time == start_time,
            TripModel.end_time == end_time,
            TripModel.detection_algorithm == algorithm
        ).first()

        if existing:
            return

        # Determine primary transport mode
        mode_distances = defaultdict(float)
        for segment in segments:
            if segment.activity:
                mode = segment.activity.activity_type
                dist = segment.activity.distance_meters or 0
                mode_distances[mode] += dist

        primary_mode = max(mode_distances.items(), key=lambda x: x[1])[0] if mode_distances else None

        # Create trip
        trip = TripModel(
            start_time=start_time,
            end_time=end_time,
            is_multi_day=(end_time.date() > start_time.date()),
            total_distance_meters=distance_meters,
            primary_transport_mode=primary_mode,
            detection_algorithm=algorithm
        )
        self.db.add(trip)
        self.db.flush()

        # Add destinations
        for idx, place_id in enumerate(destinations):
            dest = TripDestinationModel(
                trip_id=trip.id,
                place_id=place_id,
                visit_order=idx
            )
            self.db.add(dest)

        # Link segments
        for idx, segment in enumerate(segments):
            seg_link = TripSegmentModel(
                trip_id=trip.id,
                segment_id=segment.id,
                segment_order=idx
            )
            self.db.add(seg_link)

    def _create_overnight_trip(self, visits: List[Tuple[VisitModel, TimelineSegment]]):
        """Helper to create trip from overnight visits."""
        if not visits:
            return

        first_visit, first_segment = visits[0]
        last_visit, last_segment = visits[-1]

        segments = [seg for _, seg in visits]
        destinations = [v.place_id for v, _ in visits if v.place_id]

        self._create_trip(
            start_time=first_segment.start_time,
            end_time=last_segment.end_time,
            segments=segments,
            distance_meters=0,  # Could calculate if needed
            destinations=destinations,
            algorithm='overnight'
        )

    def _create_clustered_trip(self, segments: List[TimelineSegment]):
        """Helper to create trip from clustered segments."""
        if not segments:
            return

        total_distance = 0
        destinations = []

        for segment in segments:
            if segment.activity:
                total_distance += segment.activity.distance_meters or 0
            if segment.visit and segment.visit.place_id:
                destinations.append(segment.visit.place_id)

        self._create_trip(
            start_time=segments[0].start_time,
            end_time=segments[-1].end_time,
            segments=segments,
            distance_meters=total_distance,
            destinations=list(set(destinations)),
            algorithm='distance_based'
        )

    def get_trip_summary(self) -> dict:
        """Get summary statistics about detected trips."""
        total = self.db.query(func.count(TripModel.id)).scalar()

        by_algorithm = self.db.query(
            TripModel.detection_algorithm,
            func.count(TripModel.id)
        ).group_by(TripModel.detection_algorithm).all()

        multi_day = self.db.query(func.count(TripModel.id)).filter(
            TripModel.is_multi_day == True
        ).scalar()

        total_distance = self.db.query(
            func.sum(TripModel.total_distance_meters)
        ).scalar() or 0

        return {
            'total_trips': total,
            'by_algorithm': dict(by_algorithm),
            'multi_day_trips': multi_day,
            'total_distance_km': total_distance / 1000
        }
