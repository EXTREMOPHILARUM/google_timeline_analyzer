"""
Pattern detection module for identifying travel patterns and behaviors.

Finds frequent routes, peak travel times, and behavioral patterns.
"""

from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple
from collections import defaultdict, Counter

from sqlalchemy import func, extract
from sqlalchemy.orm import Session
from rich.console import Console
from rich.table import Table

from ..core.database import (
    TripModel,
    PlaceModel,
    ActivityModel,
    TimelineSegment,
    TripDestinationModel,
)

console = Console()


class PatternDetector:
    """Detect travel patterns and behaviors."""

    def __init__(self, db_session: Session):
        """Initialize pattern detector."""
        self.db = db_session

    def find_frequent_routes(
        self,
        min_occurrences: int = 3
    ) -> List[Dict]:
        """
        Find frequently traveled routes.

        A route is defined as origin -> destinations pattern.

        Args:
            min_occurrences: Minimum number of times route must occur

        Returns:
            List of routes with occurrence counts
        """
        # Get all trips with their destinations
        trips = self.db.query(TripModel).all()

        routes = defaultdict(int)
        route_examples = {}

        for trip in trips:
            # Get destinations for this trip
            destinations = self.db.query(
                TripDestinationModel.place_id
            ).filter(
                TripDestinationModel.trip_id == trip.id
            ).order_by(
                TripDestinationModel.visit_order
            ).all()

            if not destinations:
                continue

            dest_ids = tuple(d.place_id for d in destinations)

            # Create route signature (origin -> destinations)
            route_key = (trip.origin_place_id, dest_ids)
            routes[route_key] += 1

            # Store first example
            if route_key not in route_examples:
                route_examples[route_key] = trip.id

        # Filter and format results
        frequent_routes = []
        for route_key, count in routes.items():
            if count >= min_occurrences:
                origin_id, dest_ids = route_key

                # Get place names
                origin_name = None
                if origin_id:
                    origin = self.db.query(PlaceModel.name).filter(
                        PlaceModel.place_id == origin_id
                    ).first()
                    origin_name = origin.name if origin else origin_id

                dest_names = []
                for dest_id in dest_ids:
                    dest = self.db.query(PlaceModel.name).filter(
                        PlaceModel.place_id == dest_id
                    ).first()
                    dest_names.append(dest.name if dest else dest_id)

                frequent_routes.append({
                    'origin': origin_name or 'Unknown',
                    'destinations': dest_names,
                    'count': count,
                    'example_trip_id': route_examples[route_key]
                })

        # Sort by count
        frequent_routes.sort(key=lambda x: x['count'], reverse=True)
        return frequent_routes

    def find_peak_travel_times(self) -> Dict:
        """
        Identify peak travel times.

        Returns:
            Dictionary with hourly, daily, and monthly patterns
        """
        trips = self.db.query(TripModel).all()

        # Hour of day (0-23)
        hour_counts = defaultdict(int)
        # Day of week (0=Monday, 6=Sunday)
        dow_counts = defaultdict(int)
        # Month of year (1-12)
        month_counts = defaultdict(int)

        for trip in trips:
            hour_counts[trip.start_time.hour] += 1
            dow_counts[trip.start_time.weekday()] += 1
            month_counts[trip.start_time.month] += 1

        # Convert to sorted lists
        peak_hours = sorted(hour_counts.items(), key=lambda x: x[1], reverse=True)
        peak_days = sorted(dow_counts.items(), key=lambda x: x[1], reverse=True)
        peak_months = sorted(month_counts.items(), key=lambda x: x[1], reverse=True)

        # Day names
        day_names = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
        month_names = ['', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

        return {
            'peak_hours': [(f"{h:02d}:00", count) for h, count in peak_hours[:5]],
            'peak_days': [(day_names[d], count) for d, count in peak_days],
            'peak_months': [(month_names[m], count) for m, count in peak_months],
            'all_hours': dict(hour_counts),
            'all_days': {day_names[d]: count for d, count in dow_counts.items()},
            'all_months': {month_names[m]: count for m, count in month_counts.items()},
        }

    def find_transport_mode_preferences_by_distance(self) -> Dict[str, Dict[str, int]]:
        """
        Find transport mode preferences by distance ranges.

        Returns:
            Dictionary mapping distance ranges to mode counts
        """
        # Define distance ranges in km
        ranges = [
            ('< 10 km', 0, 10),
            ('10-50 km', 10, 50),
            ('50-100 km', 50, 100),
            ('100-250 km', 100, 250),
            ('250-500 km', 250, 500),
            ('500+ km', 500, float('inf')),
        ]

        preferences = {}

        for range_name, min_km, max_km in ranges:
            # Get trips in this distance range
            trips = self.db.query(TripModel).filter(
                TripModel.total_distance_meters >= min_km * 1000,
                TripModel.total_distance_meters < max_km * 1000,
                TripModel.primary_transport_mode.isnot(None)
            ).all()

            mode_counts = Counter(t.primary_transport_mode for t in trips)
            preferences[range_name] = dict(mode_counts)

        return preferences

    def find_seasonal_patterns(self) -> Dict[str, Dict]:
        """
        Find seasonal travel patterns.

        Returns:
            Dictionary with statistics per season
        """
        trips = self.db.query(TripModel).all()

        seasons = {
            'Winter': [12, 1, 2],
            'Spring': [3, 4, 5],
            'Summer': [6, 7, 8],
            'Fall': [9, 10, 11],
        }

        season_stats = {}

        for season_name, months in seasons.items():
            season_trips = [
                t for t in trips
                if t.start_time.month in months
            ]

            if season_trips:
                total_distance = sum(t.total_distance_meters for t in season_trips)
                avg_distance = total_distance / len(season_trips)

                season_stats[season_name] = {
                    'trip_count': len(season_trips),
                    'total_distance_km': total_distance / 1000,
                    'avg_distance_km': avg_distance / 1000,
                }
            else:
                season_stats[season_name] = {
                    'trip_count': 0,
                    'total_distance_km': 0,
                    'avg_distance_km': 0,
                }

        return season_stats

    def find_trip_companions(self) -> List[Dict]:
        """
        Find places often visited together in the same trip.

        Returns:
            List of place pairs with co-occurrence counts
        """
        trips = self.db.query(TripModel).all()

        # Track place pairs
        pair_counts = defaultdict(int)

        for trip in trips:
            # Get destinations for this trip
            destinations = self.db.query(
                TripDestinationModel.place_id
            ).filter(
                TripDestinationModel.trip_id == trip.id
            ).all()

            dest_ids = [d.place_id for d in destinations]

            # Create pairs
            for i in range(len(dest_ids)):
                for j in range(i + 1, len(dest_ids)):
                    pair = tuple(sorted([dest_ids[i], dest_ids[j]]))
                    pair_counts[pair] += 1

        # Get top pairs
        top_pairs = sorted(pair_counts.items(), key=lambda x: x[1], reverse=True)[:20]

        # Format results
        companions = []
        for (place1_id, place2_id), count in top_pairs:
            # Get place names
            place1 = self.db.query(PlaceModel.name).filter(
                PlaceModel.place_id == place1_id
            ).first()
            place2 = self.db.query(PlaceModel.name).filter(
                PlaceModel.place_id == place2_id
            ).first()

            if place1 and place2:
                companions.append({
                    'place1': place1.name,
                    'place2': place2.name,
                    'count': count,
                })

        return companions

    def find_travel_streaks(self) -> Dict:
        """
        Find longest travel streaks (consecutive days with trips).

        Returns:
            Dictionary with streak information
        """
        trips = self.db.query(TripModel).order_by(TripModel.start_time).all()

        if not trips:
            return {
                'longest_streak': 0,
                'current_streak': 0,
                'total_travel_days': 0,
            }

        # Track unique travel days
        travel_days = set()
        for trip in trips:
            travel_days.add(trip.start_time.date())

        # Sort days
        sorted_days = sorted(travel_days)

        # Find longest streak
        current_streak = 1
        longest_streak = 1

        for i in range(1, len(sorted_days)):
            if (sorted_days[i] - sorted_days[i-1]).days == 1:
                current_streak += 1
                longest_streak = max(longest_streak, current_streak)
            else:
                current_streak = 1

        # Check if currently on a streak
        from datetime import date
        today = date.today()
        current_active_streak = 0

        if sorted_days and today in travel_days:
            # Count backwards from today
            check_date = today
            while check_date in travel_days:
                current_active_streak += 1
                check_date = check_date - timedelta(days=1)

        return {
            'longest_streak': longest_streak,
            'current_streak': current_active_streak,
            'total_travel_days': len(travel_days),
        }

    def display_frequent_routes_table(self, min_occurrences: int = 3):
        """Display frequent routes as a rich table."""
        routes = self.find_frequent_routes(min_occurrences)

        if not routes:
            console.print(f"[yellow]No routes found with {min_occurrences}+ occurrences")
            return

        table = Table(
            title=f"Frequent Routes ({min_occurrences}+ trips)",
            show_header=True,
            header_style="bold cyan"
        )
        table.add_column("Origin", style="cyan", no_wrap=False)
        table.add_column("Destinations", style="yellow", no_wrap=False)
        table.add_column("Count", justify="right", style="green")

        for route in routes[:20]:  # Limit to top 20
            dest_str = " → ".join(route['destinations'][:3])  # Limit destinations shown
            if len(route['destinations']) > 3:
                dest_str += "..."

            table.add_row(
                route['origin'][:30],
                dest_str[:50],
                f"{route['count']:,}"
            )

        console.print(table)

    def display_peak_times_table(self):
        """Display peak travel times as a rich table."""
        patterns = self.find_peak_travel_times()

        # Peak hours
        table = Table(title="Peak Travel Hours", show_header=True, header_style="bold cyan")
        table.add_column("Hour", style="cyan")
        table.add_column("Trips", justify="right", style="green")

        for hour, count in patterns['peak_hours']:
            table.add_row(hour, f"{count:,}")

        console.print(table)
        console.print()

        # Peak days
        table = Table(title="Travel by Day of Week", show_header=True, header_style="bold cyan")
        table.add_column("Day", style="cyan")
        table.add_column("Trips", justify="right", style="green")

        for day, count in patterns['peak_days']:
            table.add_row(day, f"{count:,}")

        console.print(table)

    def display_seasonal_patterns_table(self):
        """Display seasonal patterns as a rich table."""
        patterns = self.find_seasonal_patterns()

        table = Table(title="Seasonal Patterns", show_header=True, header_style="bold cyan")
        table.add_column("Season", style="cyan")
        table.add_column("Trips", justify="right", style="green")
        table.add_column("Distance (km)", justify="right", style="yellow")
        table.add_column("Avg (km)", justify="right", style="magenta")

        for season in ['Winter', 'Spring', 'Summer', 'Fall']:
            stats = patterns[season]
            table.add_row(
                season,
                f"{stats['trip_count']:,}",
                f"{stats['total_distance_km']:,.0f}",
                f"{stats['avg_distance_km']:,.0f}" if stats['trip_count'] > 0 else "0"
            )

        console.print(table)
