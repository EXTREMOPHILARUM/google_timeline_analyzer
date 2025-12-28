"""
CLI commands for Google Timeline Analyzer.

Provides commands for importing, enriching, analyzing timeline data.
"""

from datetime import datetime
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table
from sqlalchemy.orm import Session

from ..core.database import SessionLocal, init_db
from ..importers.timeline_parser import TimelineParser
from ..core.config import get_settings

app = typer.Typer(
    name="timeline-analyzer",
    help="Google Timeline Analyzer - Analyze and map your Google Timeline data",
    add_completion=False
)
console = Console()
settings = get_settings()


def get_db() -> Session:
    """Get database session."""
    db = SessionLocal()
    try:
        return db
    except Exception as e:
        console.print(f"[red]Error connecting to database: {e}")
        console.print("[yellow]Make sure PostgreSQL is running and DATABASE_URL is correct")
        raise typer.Exit(1)


@app.command(name="import")
def import_timeline(
    timeline_file: Path = typer.Argument(
        ...,
        help="Path to Timeline.json file from Google Takeout",
        exists=True,
        file_okay=True,
        dir_okay=False
    ),
    run_migrations: bool = typer.Option(
        True,
        "--migrate/--no-migrate",
        help="Run database migrations before import"
    )
):
    """
    Import Timeline.json data into PostgreSQL database.

    This command parses your Google Timeline export and stores all segments
    (visits, activities, paths, memories) in the database.
    """
    console.print("[bold blue]Google Timeline Analyzer - Import[/bold blue]")
    console.print()

    # Run database migrations if requested
    if run_migrations:
        console.print("[cyan]Running database migrations...")
        from ..core.migrations import run_migrations as run_alembic_migrations
        if not run_alembic_migrations():
            console.print("[red]Migration failed, aborting import")
            raise typer.Exit(1)
        console.print()

    # Get database session
    db = get_db()

    try:
        # Parse and import
        parser = TimelineParser(db)
        stats = parser.parse_and_import(timeline_file)

        # Display statistics
        console.print()
        console.print("[bold green]Import Statistics:[/bold green]")
        table = Table(show_header=True, header_style="bold cyan")
        table.add_column("Metric", style="dim")
        table.add_column("Count", justify="right", style="green")

        table.add_row("Total Segments", f"{stats['total_segments']:,}")
        table.add_row("Visits", f"{stats['visits']:,}")
        table.add_row("Activities", f"{stats['activities']:,}")
        table.add_row("Timeline Paths", f"{stats['timeline_paths']:,}")
        table.add_row("Timeline Memories", f"{stats['timeline_memories']:,}")

        console.print(table)

        # Extract place IDs for enrichment
        console.print()
        console.print("[cyan]Extracting unique place IDs...")
        place_ids = parser.extract_unique_place_ids()

        console.print()
        console.print("[bold green]Import completed successfully! ✓[/bold green]")
        console.print()
        console.print(f"[yellow]Next steps:[/yellow]")
        console.print(f"  1. Run enrichment: [cyan]uv run python3 -m src.cli.commands enrich places[/cyan]")
        console.print(f"  2. Detect trips: [cyan]uv run python3 -m src.cli.commands detect trips --algorithm all[/cyan]")
        console.print(f"  3. Analyze data: [cyan]uv run python3 -m src.cli.commands stats[/cyan]")

    except Exception as e:
        console.print(f"[bold red]Error during import: {e}[/bold red]")
        raise typer.Exit(1)
    finally:
        db.close()


@app.command()
def stats(
    year: Optional[int] = typer.Option(
        None,
        "--year",
        "-y",
        help="Filter statistics for a specific year"
    )
):
    """
    Display quick statistics about imported timeline data.
    """
    console.print("[bold blue]Google Timeline Analyzer - Statistics[/bold blue]")
    console.print()

    db = get_db()

    try:
        from sqlalchemy import func, extract
        from ..core.database import (
            TimelineSegment, VisitModel, ActivityModel,
            TimelineMemoryModel, PlaceModel
        )

        # Build year filter
        year_filter = []
        if year:
            year_filter = [extract('year', TimelineSegment.start_time) == year]

        # Count segments
        total_segments = db.query(func.count(TimelineSegment.id)).filter(*year_filter).scalar()
        visit_count = db.query(func.count(VisitModel.id)).join(
            TimelineSegment
        ).filter(*year_filter).scalar()
        activity_count = db.query(func.count(ActivityModel.id)).join(
            TimelineSegment
        ).filter(*year_filter).scalar()
        memory_count = db.query(func.count(TimelineMemoryModel.id)).join(
            TimelineSegment
        ).filter(*year_filter).scalar()

        # Count places
        place_count = db.query(func.count(PlaceModel.place_id)).scalar()
        enriched_count = db.query(func.count(PlaceModel.place_id)).filter(
            PlaceModel.name.isnot(None)
        ).scalar()

        # Total distance
        total_distance_m = db.query(func.sum(ActivityModel.distance_meters)).join(
            TimelineSegment
        ).filter(*year_filter).scalar() or 0
        total_distance_km = total_distance_m / 1000

        # Date range
        date_range = db.query(
            func.min(TimelineSegment.start_time),
            func.max(TimelineSegment.end_time)
        ).filter(*year_filter).first()

        # Display statistics
        title = "Timeline Statistics"
        if year:
            title += f" ({year})"
        console.print(f"[bold cyan]{title}[/bold cyan]")
        console.print()

        table = Table(show_header=True, header_style="bold cyan")
        table.add_column("Metric", style="dim")
        table.add_column("Value", justify="right", style="green")

        if date_range[0] and date_range[1]:
            table.add_row("Date Range", f"{date_range[0].date()} to {date_range[1].date()}")

        table.add_row("Total Segments", f"{total_segments:,}")
        table.add_row("Visits", f"{visit_count:,}")
        table.add_row("Activities", f"{activity_count:,}")
        table.add_row("Timeline Memories", f"{memory_count:,}")
        table.add_row("", "")
        table.add_row("Total Distance", f"{total_distance_km:,.2f} km")
        table.add_row("", "")
        table.add_row("Unique Places", f"{place_count:,}")
        table.add_row("Enriched Places", f"{enriched_count:,} ({enriched_count/place_count*100 if place_count > 0 else 0:.1f}%)")

        console.print(table)

    except Exception as e:
        console.print(f"[bold red]Error generating statistics: {e}[/bold red]")
        raise typer.Exit(1)
    finally:
        db.close()


@app.command()
def enrich(
    batch_size: int = typer.Option(
        50,
        "--batch-size",
        "-b",
        help="Number of concurrent API requests"
    ),
    force_refresh: bool = typer.Option(
        False,
        "--force-refresh",
        "-f",
        help="Force refresh even if cached"
    ),
    limit: Optional[int] = typer.Option(
        None,
        "--limit",
        "-l",
        help="Limit number of places to enrich (for testing)"
    ),
    start_date: Optional[str] = typer.Option(
        None,
        "--start",
        "-s",
        help="Start date filter (YYYY-MM-DD)"
    ),
    end_date: Optional[str] = typer.Option(
        None,
        "--end",
        "-e",
        help="End date filter (YYYY-MM-DD)"
    )
):
    """
    Enrich places with Google Places API data.

    Fetches detailed information for unique place IDs found in your
    timeline. Use --start and --end to only enrich places visited
    within a specific date range, reducing API costs.
    Results are cached in PostgreSQL and optionally Redis.
    """
    import asyncio
    from ..enrichment.places_api import PlacesAPIClient
    from ..core.database import VisitModel, TimelineMemoryModel, TimelineSegment

    console.print("[bold blue]Google Timeline Analyzer - Enrich Places[/bold blue]")
    console.print()

    # Parse date filters
    start_dt = None
    end_dt = None

    if start_date:
        try:
            start_dt = datetime.fromisoformat(start_date)
            console.print(f"[cyan]Start date: {start_dt.date()}")
        except ValueError:
            console.print(f"[red]Invalid start date: {start_date}")
            console.print("[yellow]Use format: YYYY-MM-DD")
            raise typer.Exit(1)

    if end_date:
        try:
            end_dt = datetime.fromisoformat(end_date)
            console.print(f"[cyan]End date: {end_dt.date()}")
        except ValueError:
            console.print(f"[red]Invalid end date: {end_date}")
            console.print("[yellow]Use format: YYYY-MM-DD")
            raise typer.Exit(1)

    # Check API key
    if not settings.google_places_api_key:
        console.print("[bold red]Error: GOOGLE_PLACES_API_KEY not set in .env file")
        console.print("[yellow]Please add your Google Places API key to .env:")
        console.print("[cyan]GOOGLE_PLACES_API_KEY=your_key_here")
        raise typer.Exit(1)

    db = get_db()

    try:
        # Extract unique place IDs with optional date filtering
        if start_dt or end_dt:
            console.print("[cyan]Extracting unique place IDs from timeline (with date filter)...")
        else:
            console.print("[cyan]Extracting unique place IDs from timeline (all dates)...")

        # Get unique place IDs from visits (join with timeline_segments for date filtering)
        visit_query = db.query(VisitModel.place_id).join(
            TimelineSegment, VisitModel.segment_id == TimelineSegment.id
        ).filter(VisitModel.place_id.isnot(None))

        if start_dt:
            visit_query = visit_query.filter(TimelineSegment.start_time >= start_dt)
        if end_dt:
            visit_query = visit_query.filter(TimelineSegment.end_time <= end_dt)

        visit_place_ids = visit_query.distinct().all()

        # Get unique place IDs from timeline memories (join with timeline_segments for date filtering)
        memory_query = db.query(TimelineMemoryModel.destination_place_ids).join(
            TimelineSegment, TimelineMemoryModel.segment_id == TimelineSegment.id
        )

        if start_dt:
            memory_query = memory_query.filter(TimelineSegment.start_time >= start_dt)
        if end_dt:
            memory_query = memory_query.filter(TimelineSegment.end_time <= end_dt)

        memory_place_ids = memory_query.all()

        # Flatten and deduplicate
        place_ids = set()
        for (pid,) in visit_place_ids:
            place_ids.add(pid)

        for (dest_ids,) in memory_place_ids:
            if dest_ids:
                place_ids.update(dest_ids)

        place_ids = sorted(list(place_ids))

        console.print(f"[green]Found {len(place_ids):,} unique place IDs")

        # Apply limit if specified
        if limit and limit < len(place_ids):
            place_ids = place_ids[:limit]
            console.print(f"[yellow]Limited to {limit:,} places for testing")

        console.print()

        # Fetch place details
        async def fetch_all():
            async with PlacesAPIClient(settings.google_places_api_key, db) as client:
                return await client.batch_fetch_places(
                    place_ids,
                    batch_size=batch_size,
                    force_refresh=force_refresh
                )

        results = asyncio.run(fetch_all())

        # Display summary
        console.print()
        console.print("[bold green]Enrichment complete! ✓[/bold green]")
        console.print()
        console.print(f"[cyan]Successfully enriched: {len(results):,} places")
        console.print()
        console.print("[yellow]Next steps:[/yellow]")
        console.print("  1. Detect trips: [cyan]uv run python3 -m src.cli.commands detect trips --algorithm all[/cyan]")
        console.print("  2. View stats: [cyan]uv run python3 -m src.cli.commands stats[/cyan]")

    except Exception as e:
        console.print(f"[bold red]Error during enrichment: {e}[/bold red]")
        import traceback
        console.print(traceback.format_exc())
        raise typer.Exit(1)
    finally:
        db.close()


@app.command()
def detect(
    algorithm: str = typer.Option(
        "all",
        "--algorithm",
        "-a",
        help="Detection algorithm: all, memory, home, overnight, distance"
    ),
    start_date: Optional[str] = typer.Option(
        None,
        "--start",
        "-s",
        help="Start date filter (YYYY-MM-DD)"
    ),
    end_date: Optional[str] = typer.Option(
        None,
        "--end",
        "-e",
        help="End date filter (YYYY-MM-DD)"
    ),
    min_distance: float = typer.Option(
        0.5,
        "--min-distance",
        help="Minimum trip distance in km (default: 0.5)"
    ),
    min_duration: float = typer.Option(
        0.1,
        "--min-duration",
        help="Minimum trip duration in hours (default: 0.1)"
    ),
    distance_threshold: float = typer.Option(
        5.0,
        "--distance-threshold",
        help="Distance from home to consider as trip in km (default: 5)"
    )
):
    """
    Detect trips from timeline data using various algorithms.

    Algorithms:
    - memory: Use Google's pre-identified trips (timeline memories)
    - home: Detect trips starting/ending at home
    - overnight: Detect multi-day trips with overnight stays
    - distance: Cluster activities far from typical locations
    - all: Run all algorithms (default)
    """
    from datetime import datetime
    from ..analysis.trip_detector import TripDetector

    console.print("[bold blue]Google Timeline Analyzer - Trip Detection[/bold blue]")
    console.print()

    # Parse dates
    start_dt = None
    end_dt = None
    if start_date:
        try:
            start_dt = datetime.fromisoformat(start_date)
        except ValueError:
            console.print(f"[red]Invalid start date: {start_date}")
            console.print("[yellow]Use format: YYYY-MM-DD")
            raise typer.Exit(1)

    if end_date:
        try:
            end_dt = datetime.fromisoformat(end_date)
        except ValueError:
            console.print(f"[red]Invalid end date: {end_date}")
            console.print("[yellow]Use format: YYYY-MM-DD")
            raise typer.Exit(1)

    db = get_db()

    try:
        detector = TripDetector(db)

        # Run selected algorithm(s)
        if algorithm == "all":
            stats = detector.detect_all_trips(
                start_dt, end_dt,
                min_distance_km=min_distance,
                min_duration_hours=min_duration,
                distance_threshold_km=distance_threshold
            )

        elif algorithm == "memory":
            console.print("[cyan]Running Timeline Memory Detection...")
            count = detector.detect_timeline_memory_trips(start_dt, end_dt)
            stats = {'timeline_memory': count}
            console.print(f"[green]Found {count} trips")

        elif algorithm == "home":
            console.print("[cyan]Running Home-Based Detection...")
            count = detector.detect_home_based_trips(
                start_dt, end_dt,
                min_distance_km=min_distance,
                min_duration_hours=min_duration
            )
            stats = {'home_based': count}
            console.print(f"[green]Found {count} trips")

        elif algorithm == "overnight":
            console.print("[cyan]Running Overnight Stay Detection...")
            count = detector.detect_overnight_trips(start_dt, end_dt)
            stats = {'overnight': count}
            console.print(f"[green]Found {count} trips")

        elif algorithm == "distance":
            console.print("[cyan]Running Distance-Based Clustering...")
            count = detector.detect_distance_based_trips(
                start_dt, end_dt,
                distance_threshold_km=distance_threshold
            )
            stats = {'distance_based': count}
            console.print(f"[green]Found {count} trips")

        else:
            console.print(f"[red]Unknown algorithm: {algorithm}")
            console.print("[yellow]Valid options: all, memory, home, overnight, distance")
            raise typer.Exit(1)

        # Display summary
        console.print()
        console.print("[bold green]Trip Detection Complete! ✓[/bold green]")
        console.print()

        # Get overall summary with date filters
        summary = detector.get_trip_summary(start_dt, end_dt)

        table = Table(show_header=True, header_style="bold cyan")
        table.add_column("Metric", style="dim")
        table.add_column("Value", justify="right", style="green")

        table.add_row("Total Trips Detected", f"{summary['total_trips']:,}")
        table.add_row("Multi-Day Trips", f"{summary['multi_day_trips']:,}")
        table.add_row("Actual Distance Traveled", f"{summary['total_distance_km']:,.2f} km")
        table.add_row("", "")

        # Show per-algorithm breakdown
        table.add_row("[bold]By Detection Algorithm:[/bold]", "")
        for algo, count in summary['by_algorithm'].items():
            algo_distance = summary['distance_by_algorithm'].get(algo, 0)
            table.add_row(f"  {algo}", f"{count:,} trips ({algo_distance:,.0f} km)")

        console.print(table)
        console.print()
        console.print("[yellow]Next steps:[/yellow]")
        console.print("  1. View trips: [cyan]docker-compose exec postgres psql -U timeline_user -d timeline_analyzer[/cyan]")
        console.print("              [cyan]SELECT * FROM recent_trips_with_destinations LIMIT 10;[/cyan]")
        console.print("  2. Analyze: [cyan]uv run python3 -m src.cli.commands analyze trips[/cyan]")

    except Exception as e:
        console.print(f"[bold red]Error during trip detection: {e}[/bold red]")
        import traceback
        console.print(traceback.format_exc())
        raise typer.Exit(1)
    finally:
        db.close()


@app.command()
def analyze(
    what: str = typer.Argument(
        "overview",
        help="What to analyze: overview, trips, patterns, destinations"
    ),
    year: Optional[int] = typer.Option(
        None,
        "--year",
        "-y",
        help="Filter by year"
    ),
    start_date: Optional[str] = typer.Option(
        None,
        "--start",
        "-s",
        help="Start date filter (YYYY-MM-DD)"
    ),
    end_date: Optional[str] = typer.Option(
        None,
        "--end",
        "-e",
        help="End date filter (YYYY-MM-DD)"
    )
):
    """
    Analyze trips and patterns.

    Options:
    - overview: Overall trip statistics
    - trips: Detailed trip analysis
    - patterns: Travel patterns (routes, times, seasons)
    - destinations: Top destinations analysis
    """
    from datetime import datetime
    from ..analysis.statistics import TripStatistics
    from ..analysis.patterns import PatternDetector

    console.print("[bold blue]Google Timeline Analyzer - Analysis[/bold blue]")
    console.print()

    # Parse dates
    start_dt = None
    end_dt = None

    if year:
        start_dt = datetime(year, 1, 1)
        end_dt = datetime(year, 12, 31, 23, 59, 59)

    if start_date:
        try:
            start_dt = datetime.fromisoformat(start_date)
        except ValueError:
            console.print(f"[red]Invalid start date: {start_date}")
            raise typer.Exit(1)

    if end_date:
        try:
            end_dt = datetime.fromisoformat(end_date)
        except ValueError:
            console.print(f"[red]Invalid end date: {end_date}")
            raise typer.Exit(1)

    db = get_db()

    try:
        stats = TripStatistics(db)
        patterns = PatternDetector(db)

        if what == "overview":
            # Display overview
            stats.display_overview_table(start_dt, end_dt)
            console.print()
            stats.display_yearly_table()
            console.print()
            stats.display_transport_mode_table(start_dt, end_dt)

        elif what == "trips":
            # Detailed trip analysis
            stats.display_overview_table(start_dt, end_dt)
            console.print()

            # Duration distribution
            duration_dist = stats.get_trip_duration_distribution()
            table = Table(title="Trip Duration Distribution", show_header=True, header_style="bold cyan")
            table.add_column("Duration", style="cyan")
            table.add_column("Trips", justify="right", style="green")

            for duration, count in duration_dist.items():
                if count > 0:
                    table.add_row(duration, f"{count:,}")

            console.print(table)
            console.print()

            # Distance distribution
            distance_dist = stats.get_distance_distribution()
            table = Table(title="Trip Distance Distribution", show_header=True, header_style="bold cyan")
            table.add_column("Distance", style="cyan")
            table.add_column("Trips", justify="right", style="green")

            for distance, count in distance_dist.items():
                if count > 0:
                    table.add_row(distance, f"{count:,}")

            console.print(table)
            console.print()

            # Longest trips
            longest = stats.get_longest_trips(limit=10, by='distance')
            if longest:
                table = Table(title="Longest Trips (by distance)", show_header=True, header_style="bold cyan")
                table.add_column("Start", style="cyan")
                table.add_column("Destinations", style="yellow", no_wrap=False)
                table.add_column("Distance", justify="right", style="green")

                for trip in longest:
                    dest_str = ", ".join(trip['destinations'][:2])
                    if len(trip['destinations']) > 2:
                        dest_str += "..."
                    table.add_row(
                        trip['start_time'].strftime("%Y-%m-%d"),
                        dest_str[:40],
                        f"{trip['distance_km']:,.0f} km"
                    )

                console.print(table)

        elif what == "patterns":
            # Pattern analysis
            patterns.display_frequent_routes_table(min_occurrences=2)
            console.print()
            patterns.display_peak_times_table()
            console.print()
            patterns.display_seasonal_patterns_table()

        elif what == "destinations":
            # Destination analysis
            stats.display_top_destinations_table(limit=30, start_date=start_dt, end_date=end_dt)

        else:
            console.print(f"[red]Unknown analysis type: {what}")
            console.print("[yellow]Valid options: overview, trips, patterns, destinations")
            raise typer.Exit(1)

    except Exception as e:
        console.print(f"[bold red]Error during analysis: {e}[/bold red]")
        import traceback
        console.print(traceback.format_exc())
        raise typer.Exit(1)
    finally:
        db.close()


@app.command()
def export(
    what: str = typer.Argument(
        "trips",
        help="What to export: trips, places, activities"
    ),
    output: Path = typer.Option(
        "exports/export.csv",
        "--output",
        "-o",
        help="Output file path"
    ),
    format: str = typer.Option(
        "csv",
        "--format",
        "-f",
        help="Export format: csv or json"
    ),
    start_date: Optional[str] = typer.Option(
        None,
        "--start",
        "-s",
        help="Start date filter (YYYY-MM-DD)"
    ),
    end_date: Optional[str] = typer.Option(
        None,
        "--end",
        "-e",
        help="End date filter (YYYY-MM-DD)"
    )
):
    """
    Export data to CSV or JSON files.

    Examples:
      export trips --output exports/trips_2024.csv
      export places --format json --output exports/places.json
    """
    import csv
    import json
    from datetime import datetime
    from ..core.database import TripModel, PlaceModel, ActivityModel, TimelineSegment

    console.print("[bold blue]Google Timeline Analyzer - Export[/bold blue]")
    console.print()

    # Parse dates
    start_dt = None
    end_dt = None

    if start_date:
        try:
            start_dt = datetime.fromisoformat(start_date)
        except ValueError:
            console.print(f"[red]Invalid start date: {start_date}")
            raise typer.Exit(1)

    if end_date:
        try:
            end_dt = datetime.fromisoformat(end_date)
        except ValueError:
            console.print(f"[red]Invalid end date: {end_date}")
            raise typer.Exit(1)

    # Create output directory if needed
    output.parent.mkdir(parents=True, exist_ok=True)

    db = get_db()

    try:
        if what == "trips":
            # Export trips
            query = db.query(TripModel)

            if start_dt:
                query = query.filter(TripModel.start_time >= start_dt)
            if end_dt:
                query = query.filter(TripModel.end_time <= end_dt)

            trips = query.all()

            if format == "csv":
                with open(output, 'w', newline='') as f:
                    writer = csv.writer(f)
                    writer.writerow([
                        'trip_id', 'start_time', 'end_time', 'distance_km',
                        'duration_hours', 'is_multi_day', 'transport_mode', 'algorithm'
                    ])

                    for trip in trips:
                        duration = (trip.end_time - trip.start_time).total_seconds() / 3600
                        writer.writerow([
                            trip.id,
                            trip.start_time.isoformat(),
                            trip.end_time.isoformat(),
                            trip.total_distance_meters / 1000,
                            duration,
                            trip.is_multi_day,
                            trip.primary_transport_mode,
                            trip.detection_algorithm
                        ])

                console.print(f"[green]Exported {len(trips)} trips to {output}")

            else:  # JSON
                data = []
                for trip in trips:
                    duration = (trip.end_time - trip.start_time).total_seconds() / 3600
                    data.append({
                        'trip_id': trip.id,
                        'start_time': trip.start_time.isoformat(),
                        'end_time': trip.end_time.isoformat(),
                        'distance_km': trip.total_distance_meters / 1000,
                        'duration_hours': duration,
                        'is_multi_day': trip.is_multi_day,
                        'transport_mode': trip.primary_transport_mode,
                        'algorithm': trip.detection_algorithm
                    })

                with open(output, 'w') as f:
                    json.dump(data, f, indent=2)

                console.print(f"[green]Exported {len(trips)} trips to {output}")

        elif what == "places":
            # Export places
            places = db.query(PlaceModel).filter(PlaceModel.name.isnot(None)).all()

            if format == "csv":
                with open(output, 'w', newline='') as f:
                    writer = csv.writer(f)
                    writer.writerow([
                        'place_id', 'name', 'address', 'rating',
                        'ratings_total', 'price_level', 'types'
                    ])

                    for place in places:
                        writer.writerow([
                            place.place_id,
                            place.name,
                            place.formatted_address,
                            place.rating,
                            place.user_ratings_total,
                            place.price_level,
                            ','.join(place.types or [])
                        ])

                console.print(f"[green]Exported {len(places)} places to {output}")

            else:  # JSON
                data = []
                for place in places:
                    data.append({
                        'place_id': place.place_id,
                        'name': place.name,
                        'address': place.formatted_address,
                        'rating': place.rating,
                        'ratings_total': place.user_ratings_total,
                        'price_level': place.price_level,
                        'types': place.types or []
                    })

                with open(output, 'w') as f:
                    json.dump(data, f, indent=2)

                console.print(f"[green]Exported {len(places)} places to {output}")

        else:
            console.print(f"[red]Unknown export type: {what}")
            console.print("[yellow]Valid options: trips, places")
            raise typer.Exit(1)

    except Exception as e:
        console.print(f"[bold red]Error during export: {e}[/bold red]")
        import traceback
        console.print(traceback.format_exc())
        raise typer.Exit(1)
    finally:
        db.close()


@app.command()
def migrate(
    revision: str = typer.Argument(
        "head",
        help="Revision to migrate to (default: head for latest)"
    )
):
    """
    Run database migrations to upgrade schema.

    This applies pending Alembic migrations to bring the database
    schema up to date with the current models.
    """
    console.print("[bold blue]Database Migration[/bold blue]")
    console.print()

    from ..core.migrations import run_migrations
    if run_migrations(revision):
        console.print("[bold green]Migration completed successfully[/bold green]")
    else:
        console.print("[bold red]Migration failed[/bold red]")
        raise typer.Exit(1)


@app.command()
def revision(
    message: str = typer.Argument(..., help="Migration description"),
    autogenerate: bool = typer.Option(
        True,
        "--autogenerate/--empty",
        help="Auto-generate migration from model changes"
    )
):
    """
    Create a new migration revision.

    Generates a new migration file based on model changes (if autogenerate=True)
    or creates an empty migration template.
    """
    console.print("[bold blue]Create Migration Revision[/bold blue]")
    console.print()

    from ..core.migrations import create_migration
    if create_migration(message, autogenerate):
        console.print("[bold green]Migration revision created[/bold green]")
        console.print("[yellow]Remember to review and edit the migration file if needed[/yellow]")
    else:
        console.print("[bold red]Failed to create migration[/bold red]")
        raise typer.Exit(1)


@app.command()
def downgrade(
    revision: str = typer.Argument(
        "-1",
        help="Revision to downgrade to (default: -1 for previous revision)"
    )
):
    """
    Downgrade database to a previous revision.

    Use with caution - this can result in data loss if migrations
    drop tables or columns.
    """
    console.print("[bold yellow]Database Downgrade[/bold yellow]")
    console.print()

    # Confirm downgrade
    confirm = typer.confirm(
        f"Are you sure you want to downgrade to revision '{revision}'? This may cause data loss."
    )
    if not confirm:
        console.print("[yellow]Downgrade cancelled[/yellow]")
        raise typer.Abort()

    from ..core.migrations import downgrade_migration
    if downgrade_migration(revision):
        console.print("[bold green]Downgrade completed successfully[/bold green]")
    else:
        console.print("[bold red]Downgrade failed[/bold red]")
        raise typer.Exit(1)


@app.command(name="migration-history")
def migration_history(
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Show detailed history"
    )
):
    """
    Display migration history.

    Shows all available migrations and their status.
    """
    console.print("[bold blue]Migration History[/bold blue]")
    console.print()

    from ..core.migrations import get_migration_history, check_migration_status

    # Show current status
    status = check_migration_status()
    console.print(f"Current revision: [cyan]{status.get('current_revision', 'None')}[/cyan]")
    console.print(f"Head revision: [cyan]{status.get('head_revision', 'Unknown')}[/cyan]")

    if status.get('is_up_to_date'):
        console.print("[green]✓ Database is up to date[/green]")
    else:
        console.print("[yellow]⚠ Pending migrations available[/yellow]")

    console.print()

    # Show history
    get_migration_history(verbose)


@app.command()
def info():
    """
    Display information about the Google Timeline Analyzer.
    """
    console.print("[bold blue]Google Timeline Analyzer[/bold blue]")
    console.print()
    console.print("Version: 0.1.0")
    console.print()
    console.print("[bold]Features:[/bold]")
    console.print("  • Import Google Timeline export (Timeline.json)")
    console.print("  • Enrich with Google Places API data")
    console.print("  • Detect trips using multiple algorithms")
    console.print("  • Analyze travel patterns and statistics")
    console.print("  • Export data to various formats")
    console.print()
    console.print("[bold]Configuration:[/bold]")
    console.print(f"  Database URL: {settings.database_url}")
    console.print(f"  Redis URL: {settings.redis_url}")
    console.print(f"  Google Places API Key: {'Set' if settings.google_places_api_key else 'Not Set'}")
    console.print()
    console.print("[bold]Available Commands:[/bold]")
    console.print("  import            - Import Timeline.json data")
    console.print("  enrich            - Enrich with Google Places API")
    console.print("  detect            - Detect trips from timeline data")
    console.print("  analyze           - Analyze trips and patterns")
    console.print("  export            - Export data to CSV/JSON files")
    console.print("  stats             - Display quick statistics")
    console.print("  migrate           - Run database migrations")
    console.print("  revision          - Create new migration revision")
    console.print("  downgrade         - Downgrade to previous migration")
    console.print("  migration-history - Show migration history")
    console.print("  info              - Display this information")


if __name__ == "__main__":
    app()
