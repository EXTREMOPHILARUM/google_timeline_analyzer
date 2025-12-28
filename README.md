# Google Timeline Analyzer

A comprehensive Python-based system to analyze, map, and gain insights from your Google Timeline export data, enriched with Google Places API details.

## Features

- **Trip Detection**: Multiple algorithms to identify trips from 10+ years of timeline data
  - Timeline Memory based (Google-identified trips)
  - Home-based detection (trips starting/ending at home)
  - Overnight stay detection (multi-day trips and vacations)
  - Distance-based clustering (irregular travel patterns)

- **Places Enrichment**: Fetch detailed information for visited places using Google Places API
  - Place names, addresses, and categories
  - Ratings and review counts
  - Photos, opening hours, and contact info
  - Smart caching to minimize API costs (~$35 one-time for 2,079 places)

- **Travel Analytics**:
  - Trip statistics (distance, duration, transport modes)
  - Pattern detection (frequent routes, peak travel times)
  - Place insights (favorite destinations, category analysis)
  - Behavior evolution over years

- **Geospatial Analysis**: PostgreSQL + PostGIS for accurate distance calculations and location queries

- **Dockerized**: Complete containerized setup for easy deployment

## Data Overview

Your Timeline.json contains:
- **26,751 semantic segments** spanning 2015-2025
- **7,022 visits** to places with Google Place IDs
- **6,032 activity segments** (movements/trips)
- **2,079 unique places** to enrich
- **73 timeline memories** (Google-identified trips)

## Quick Start

### Prerequisites

- Docker and Docker Compose installed
- Google Places API key ([Get one here](https://developers.google.com/maps/documentation/places/web-service/get-api-key))
- Timeline.json export from Google Takeout

### Setup

1. **Clone or create project directory**:
```bash
cd /path/to/google_timeline_analyzer
```

2. **Set up environment variables**:
```bash
cp .env.example .env
# Edit .env and add your GOOGLE_PLACES_API_KEY
```

3. **Place your Timeline.json in the project root**:
```bash
# Copy your Timeline.json from Google Takeout to the project directory
```

4. **Start all services**:
```bash
docker-compose up -d
```

This will start:
- PostgreSQL 16 with PostGIS extension
- Redis for caching
- Python application container

### Usage

#### 1. Import Timeline Data

Import all segments from Timeline.json into PostgreSQL:

```bash
docker-compose exec app uv run python3 -m src.cli.commands import Timeline.json
```

Expected time: ~2-3 minutes for 59MB file

#### 2. Enrich with Places API

Fetch place details for all 2,079 unique places:

```bash
docker-compose exec app uv run python3 -m src.cli.commands enrich places --batch-size 50
```

Expected time: ~3-4 minutes
Expected cost: ~$35 one-time (cached indefinitely)

#### 3. Detect Trips

Run all trip detection algorithms:

```bash
docker-compose exec app uv run python3 -m src.cli.commands detect trips --algorithm all
```

Or run specific algorithms:
```bash
# Timeline memory based (Google-identified trips)
docker-compose exec app uv run python3 -m src.cli.commands detect trips --algorithm memory

# Home-based detection
docker-compose exec app uv run python3 -m src.cli.commands detect trips --algorithm home

# Overnight stays
docker-compose exec app uv run python3 -m src.cli.commands detect trips --algorithm overnight

# Distance-based clustering
docker-compose exec app uv run python3 -m src.cli.commands detect trips --algorithm distance
```

#### 4. Analyze Your Travels

Get quick statistics:
```bash
docker-compose exec app uv run python3 -m src.cli.commands stats
docker-compose exec app uv run python3 -m src.cli.commands stats --year 2024
```

Analyze trips with detailed tables:
```bash
docker-compose exec app uv run python3 -m src.cli.commands analyze trips --format table
docker-compose exec app uv run python3 -m src.cli.commands analyze trips --year 2024
```

Find frequent routes:
```bash
docker-compose exec app uv run python3 -m src.cli.commands patterns routes --min-occurrences 3
```

Discover peak travel times:
```bash
docker-compose exec app uv run python3 -m src.cli.commands patterns times
```

Export data:
```bash
docker-compose exec app uv run python3 -m src.cli.commands export trips \
  --output exports/trips_2024.csv \
  --start 2024-01-01 \
  --end 2024-12-31
```

## Architecture

```
google_timeline_analyzer/
├── src/
│   ├── core/
│   │   ├── models.py          # Pydantic data models
│   │   ├── database.py        # Database session management
│   │   └── config.py          # Configuration management
│   ├── importers/
│   │   ├── timeline_parser.py # Parse Timeline.json efficiently
│   │   └── json_loader.py     # Streaming JSON loader
│   ├── enrichment/
│   │   ├── places_api.py      # Google Places API client (async)
│   │   └── cache_manager.py   # Multi-tier caching
│   ├── analysis/
│   │   ├── trip_detector.py   # Trip detection algorithms
│   │   ├── statistics.py      # Trip statistics
│   │   └── patterns.py        # Pattern detection
│   ├── storage/
│   │   ├── schema.sql         # PostgreSQL + PostGIS schema
│   │   └── repositories.py    # Data access layer
│   └── cli/
│       └── commands.py        # CLI interface (Typer)
├── tests/                     # Unit and integration tests
├── docs/                      # Additional documentation
├── exports/                   # Exported data (CSV, JSON, etc.)
├── Dockerfile                 # Application container
├── docker-compose.yml         # All services
└── pyproject.toml            # Python dependencies
```

## Database Schema

PostgreSQL with PostGIS extension provides:

- **Geospatial queries**: Accurate distance calculations using Earth's curvature
- **Spatial indexes**: Fast location-based queries
- **JSONB storage**: Raw API responses for future schema evolution
- **Array types**: Store lists without junction tables
- **Full-text search**: Search place names and addresses

Key tables:
- `timeline_segments` - All timeline events
- `visits` - Location visits with place IDs
- `activities` - Movements with transport modes
- `places` - Cached Google Places data
- `trips` - Detected trips with statistics
- `trip_destinations` - Trip destinations
- `user_profile` - Home/work locations

## Development

### Running Tests

```bash
docker-compose exec app uv run pytest
```

### Accessing Database

```bash
# Connect to PostgreSQL
docker-compose exec postgres psql -U timeline_user -d timeline_analyzer

# Run queries
SELECT COUNT(*) FROM visits;
SELECT COUNT(*) FROM activities;
SELECT COUNT(*) FROM places;
SELECT COUNT(*) FROM trips;
```

### Accessing Redis

```bash
# Connect to Redis CLI
docker-compose exec redis redis-cli

# Check cached keys
KEYS place:*
```

### Viewing Logs

```bash
# All services
docker-compose logs -f

# Specific service
docker-compose logs -f app
docker-compose logs -f postgres
docker-compose logs -f redis
```

## Stopping Services

```bash
# Stop containers
docker-compose down

# Stop and remove volumes (clean slate)
docker-compose down -v
```

## Cost Estimation

- **Google Places API**: ~$35 one-time for 2,079 unique places
  - Place Details API: $0.017 per request
  - Cached in database + Redis (no recurring costs)

- **Infrastructure**: Free (local Docker containers)

## Expected Outcomes

After setup, you'll be able to:

1. **Trip Analysis**:
   - View all trips from 2015-2025
   - Filter by date range, destination, transport mode
   - Identify multi-day vs day trips

2. **Place Insights**:
   - Most visited places (excluding home/work)
   - Place categories analysis
   - Ratings and reviews

3. **Travel Patterns**:
   - Frequent routes
   - Peak travel times
   - Transport mode preferences
   - 10-year behavior evolution

4. **Statistics**:
   - Total distance per year/month
   - Time in different transport modes
   - Unique places visited
   - Average trip metrics

## Future Enhancements

- Interactive map visualization (Folium)
- Web dashboard (FastAPI + React)
- ML-based trip prediction
- Photo integration from Google Photos
- Weather data correlation
- Export to GeoJSON/KML for mapping tools

## Troubleshooting

### Docker issues

```bash
# Rebuild containers
docker-compose build --no-cache

# Check service health
docker-compose ps
```

### Database connection issues

```bash
# Check PostgreSQL logs
docker-compose logs postgres

# Verify PostGIS extension
docker-compose exec postgres psql -U timeline_user -d timeline_analyzer -c "SELECT PostGIS_version();"
```

### API rate limiting

If you hit API rate limits, adjust in `.env`:
```
PLACES_API_RATE_LIMIT=50  # Reduce from default 100
```

## License

MIT License - See LICENSE file for details

## Contributing

Contributions welcome! Please open an issue or pull request.

## Support

For issues or questions, please open a GitHub issue.
