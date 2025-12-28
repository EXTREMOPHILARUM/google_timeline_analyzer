"""
Trip statistics and analytics module.

Provides comprehensive statistics about trips, travel patterns, and places.
"""

from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple
from collections import defaultdict

from sqlalchemy import func, extract, and_, or_
from sqlalchemy.orm import Session
from rich.console import Console
from rich.table import Table

from ..core.database import (
    TripModel,
    PlaceModel,
    ActivityModel,
    VisitModel,
    TimelineSegment,
    TripDestinationModel,
    TravelModeAffinityModel,
)

console = Console()


class TripStatistics:
    """Calculate various statistics for trips."""

    def __init__(self, db_session: Session):
        """Initialize statistics calculator."""
        self.db = db_session

    def get_overview(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> Dict:
        """
        Get overall trip statistics.

        Args:
            start_date: Optional start date filter
            end_date: Optional end date filter

        Returns:
            Dictionary with overview statistics
        """
        query = self.db.query(TripModel)

        if start_date:
            query = query.filter(TripModel.start_time >= start_date)
        if end_date:
            query = query.filter(TripModel.end_time <= end_date)

        trips = query.all()

        if not trips:
            return {
                'total_trips': 0,
                'total_distance_km': 0,
                'total_duration_hours': 0,
                'avg_distance_km': 0,
                'avg_duration_hours': 0,
                'multi_day_trips': 0,
                'single_day_trips': 0,
            }

        total_distance = sum(t.total_distance_meters for t in trips)
        total_duration = sum((t.end_time - t.start_time).total_seconds() for t in trips)
        multi_day = sum(1 for t in trips if t.is_multi_day)

        return {
            'total_trips': len(trips),
            'total_distance_km': total_distance / 1000,
            'total_duration_hours': total_duration / 3600,
            'avg_distance_km': (total_distance / 1000) / len(trips),
            'avg_duration_hours': (total_duration / 3600) / len(trips),
            'multi_day_trips': multi_day,
            'single_day_trips': len(trips) - multi_day,
        }

    def get_yearly_statistics(self) -> List[Dict]:
        """Get trip statistics grouped by year."""
        results = self.db.query(
            extract('year', TripModel.start_time).label('year'),
            func.count(TripModel.id).label('trip_count'),
            func.sum(TripModel.total_distance_meters).label('total_distance'),
            func.avg(TripModel.total_distance_meters).label('avg_distance'),
            func.count(func.distinct(
                func.case(
                    (TripModel.is_multi_day == True, TripModel.id)
                )
            )).label('multi_day_count')
        ).group_by('year').order_by('year').all()

        stats = []
        for row in results:
            stats.append({
                'year': int(row.year),
                'trip_count': row.trip_count,
                'total_distance_km': (row.total_distance or 0) / 1000,
                'avg_distance_km': (row.avg_distance or 0) / 1000,
                'multi_day_count': row.multi_day_count or 0,
            })

        return stats

    def get_monthly_statistics(self, year: int) -> List[Dict]:
        """Get trip statistics grouped by month for a specific year."""
        results = self.db.query(
            extract('month', TripModel.start_time).label('month'),
            func.count(TripModel.id).label('trip_count'),
            func.sum(TripModel.total_distance_meters).label('total_distance'),
        ).filter(
            extract('year', TripModel.start_time) == year
        ).group_by('month').order_by('month').all()

        stats = []
        for row in results:
            stats.append({
                'month': int(row.month),
                'trip_count': row.trip_count,
                'total_distance_km': (row.total_distance or 0) / 1000,
            })

        return stats

    def get_transport_mode_breakdown(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> Dict[str, Dict]:
        """
        Get trip breakdown by primary transport mode.

        Returns:
            Dictionary mapping mode to stats (count, distance, avg_distance)
        """
        query = self.db.query(
            TripModel.primary_transport_mode,
            func.count(TripModel.id).label('count'),
            func.sum(TripModel.total_distance_meters).label('total_distance'),
            func.avg(TripModel.total_distance_meters).label('avg_distance'),
        ).filter(
            TripModel.primary_transport_mode.isnot(None)
        )

        if start_date:
            query = query.filter(TripModel.start_time >= start_date)
        if end_date:
            query = query.filter(TripModel.end_time <= end_date)

        results = query.group_by(TripModel.primary_transport_mode).all()

        breakdown = {}
        for row in results:
            breakdown[row.primary_transport_mode] = {
                'count': row.count,
                'total_distance_km': (row.total_distance or 0) / 1000,
                'avg_distance_km': (row.avg_distance or 0) / 1000,
            }

        return breakdown

    def get_top_destinations(
        self,
        limit: int = 20,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> List[Dict]:
        """
        Get most visited destinations from trips.

        Args:
            limit: Number of destinations to return
            start_date: Optional start date filter
            end_date: Optional end date filter

        Returns:
            List of destinations with visit counts
        """
        query = self.db.query(
            PlaceModel.place_id,
            PlaceModel.name,
            PlaceModel.formatted_address,
            PlaceModel.rating,
            PlaceModel.types,
            func.count(TripDestinationModel.trip_id).label('trip_count')
        ).join(
            TripDestinationModel, PlaceModel.place_id == TripDestinationModel.place_id
        ).join(
            TripModel, TripDestinationModel.trip_id == TripModel.id
        )

        if start_date:
            query = query.filter(TripModel.start_time >= start_date)
        if end_date:
            query = query.filter(TripModel.end_time <= end_date)

        results = query.group_by(
            PlaceModel.place_id,
            PlaceModel.name,
            PlaceModel.formatted_address,
            PlaceModel.rating,
            PlaceModel.types
        ).order_by(func.count(TripDestinationModel.trip_id).desc()).limit(limit).all()

        destinations = []
        for row in results:
            destinations.append({
                'place_id': row.place_id,
                'name': row.name or 'Unknown',
                'address': row.formatted_address,
                'rating': row.rating,
                'types': row.types or [],
                'trip_count': row.trip_count,
            })

        return destinations

    def get_longest_trips(
        self,
        limit: int = 10,
        by: str = 'distance'
    ) -> List[Dict]:
        """
        Get longest trips by distance or duration.

        Args:
            limit: Number of trips to return
            by: 'distance' or 'duration'

        Returns:
            List of trips
        """
        query = self.db.query(TripModel)

        if by == 'distance':
            query = query.order_by(TripModel.total_distance_meters.desc())
        else:  # duration
            query = query.order_by(
                (TripModel.end_time - TripModel.start_time).desc()
            )

        trips = query.limit(limit).all()

        results = []
        for trip in trips:
            # Get destinations
            destinations = self.db.query(PlaceModel.name).join(
                TripDestinationModel, PlaceModel.place_id == TripDestinationModel.place_id
            ).filter(
                TripDestinationModel.trip_id == trip.id
            ).all()

            duration = (trip.end_time - trip.start_time).total_seconds() / 3600

            results.append({
                'trip_id': trip.id,
                'start_time': trip.start_time,
                'end_time': trip.end_time,
                'distance_km': trip.total_distance_meters / 1000,
                'duration_hours': duration,
                'destinations': [d.name for d in destinations if d.name],
                'transport_mode': trip.primary_transport_mode,
                'algorithm': trip.detection_algorithm,
            })

        return results

    def get_trip_duration_distribution(self) -> Dict[str, int]:
        """
        Get distribution of trip durations.

        Returns:
            Dictionary mapping duration ranges to trip counts
        """
        trips = self.db.query(TripModel).all()

        distribution = {
            '< 4 hours': 0,
            '4-8 hours': 0,
            '8-24 hours': 0,
            '1-3 days': 0,
            '3-7 days': 0,
            '1-2 weeks': 0,
            '2+ weeks': 0,
        }

        for trip in trips:
            duration_hours = (trip.end_time - trip.start_time).total_seconds() / 3600

            if duration_hours < 4:
                distribution['< 4 hours'] += 1
            elif duration_hours < 8:
                distribution['4-8 hours'] += 1
            elif duration_hours < 24:
                distribution['8-24 hours'] += 1
            elif duration_hours < 72:
                distribution['1-3 days'] += 1
            elif duration_hours < 168:
                distribution['3-7 days'] += 1
            elif duration_hours < 336:
                distribution['1-2 weeks'] += 1
            else:
                distribution['2+ weeks'] += 1

        return distribution

    def get_distance_distribution(self) -> Dict[str, int]:
        """
        Get distribution of trip distances.

        Returns:
            Dictionary mapping distance ranges to trip counts
        """
        trips = self.db.query(TripModel).all()

        distribution = {
            '< 50 km': 0,
            '50-100 km': 0,
            '100-250 km': 0,
            '250-500 km': 0,
            '500-1000 km': 0,
            '1000-2500 km': 0,
            '2500+ km': 0,
        }

        for trip in trips:
            distance_km = trip.total_distance_meters / 1000

            if distance_km < 50:
                distribution['< 50 km'] += 1
            elif distance_km < 100:
                distribution['50-100 km'] += 1
            elif distance_km < 250:
                distribution['100-250 km'] += 1
            elif distance_km < 500:
                distribution['250-500 km'] += 1
            elif distance_km < 1000:
                distribution['500-1000 km'] += 1
            elif distance_km < 2500:
                distribution['1000-2500 km'] += 1
            else:
                distribution['2500+ km'] += 1

        return distribution

    def display_overview_table(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ):
        """Display overview statistics as a rich table."""
        stats = self.get_overview(start_date, end_date)

        table = Table(title="Trip Overview", show_header=True, header_style="bold cyan")
        table.add_column("Metric", style="dim")
        table.add_column("Value", justify="right", style="green")

        table.add_row("Total Trips", f"{stats['total_trips']:,}")
        table.add_row("Total Distance", f"{stats['total_distance_km']:,.2f} km")
        table.add_row("Total Time", f"{stats['total_duration_hours']:,.1f} hours")
        table.add_row("", "")
        table.add_row("Average Distance", f"{stats['avg_distance_km']:,.2f} km")
        table.add_row("Average Duration", f"{stats['avg_duration_hours']:,.1f} hours")
        table.add_row("", "")
        table.add_row("Multi-Day Trips", f"{stats['multi_day_trips']:,}")
        table.add_row("Single-Day Trips", f"{stats['single_day_trips']:,}")

        console.print(table)

    def display_yearly_table(self):
        """Display yearly statistics as a rich table."""
        stats = self.get_yearly_statistics()

        if not stats:
            console.print("[yellow]No trip data available")
            return

        table = Table(title="Yearly Statistics", show_header=True, header_style="bold cyan")
        table.add_column("Year", justify="right", style="cyan")
        table.add_column("Trips", justify="right", style="green")
        table.add_column("Distance (km)", justify="right", style="yellow")
        table.add_column("Avg (km)", justify="right", style="magenta")
        table.add_column("Multi-Day", justify="right", style="blue")

        for stat in stats:
            table.add_row(
                str(stat['year']),
                f"{stat['trip_count']:,}",
                f"{stat['total_distance_km']:,.0f}",
                f"{stat['avg_distance_km']:,.0f}",
                f"{stat['multi_day_count']:,}"
            )

        console.print(table)

    def display_transport_mode_table(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ):
        """Display transport mode breakdown as a rich table."""
        breakdown = self.get_transport_mode_breakdown(start_date, end_date)

        if not breakdown:
            console.print("[yellow]No transport mode data available")
            return

        table = Table(title="Transport Modes", show_header=True, header_style="bold cyan")
        table.add_column("Mode", style="cyan")
        table.add_column("Trips", justify="right", style="green")
        table.add_column("Distance (km)", justify="right", style="yellow")
        table.add_column("Avg (km)", justify="right", style="magenta")

        # Sort by trip count
        sorted_modes = sorted(breakdown.items(), key=lambda x: x[1]['count'], reverse=True)

        for mode, stats in sorted_modes:
            table.add_row(
                mode.replace('_', ' ').title(),
                f"{stats['count']:,}",
                f"{stats['total_distance_km']:,.0f}",
                f"{stats['avg_distance_km']:,.0f}"
            )

        console.print(table)

    def display_top_destinations_table(
        self,
        limit: int = 20,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ):
        """Display top destinations as a rich table."""
        destinations = self.get_top_destinations(limit, start_date, end_date)

        if not destinations:
            console.print("[yellow]No destination data available")
            return

        table = Table(title=f"Top {limit} Destinations", show_header=True, header_style="bold cyan")
        table.add_column("Place", style="cyan", no_wrap=False)
        table.add_column("Trips", justify="right", style="green")
        table.add_column("Rating", justify="right", style="yellow")

        for dest in destinations:
            rating_str = f"{dest['rating']:.1f} ⭐" if dest['rating'] else "N/A"
            table.add_row(
                dest['name'][:50],  # Truncate long names
                f"{dest['trip_count']:,}",
                rating_str
            )

        console.print(table)
