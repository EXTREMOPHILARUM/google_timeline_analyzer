#!/bin/bash
set -e

echo "================================================"
echo "Google Timeline Analyzer - Docker Entrypoint"
echo "================================================"

# Wait for PostgreSQL to be ready
echo "Waiting for PostgreSQL to be ready..."
until pg_isready -h postgres -U timeline_user -d timeline_analyzer > /dev/null 2>&1; do
  echo "  PostgreSQL is unavailable - sleeping..."
  sleep 2
done

echo "✓ PostgreSQL is ready!"
echo ""

# Run Alembic migrations
echo "Checking for database migrations..."
cd /app

# Check if there are any migration files
if [ -z "$(ls -A alembic/versions/*.py 2>/dev/null)" ]; then
    echo "⚠ No migrations found yet. Skipping migration step."
    echo "  Generate initial migration with: docker-compose exec app python3 -m alembic revision --autogenerate -m 'Initial schema'"
else
    # Run migrations using Python module (no uv needed in container)
    echo "Running database migrations..."
    if python3 -m alembic upgrade head; then
        echo "✓ Migrations completed successfully"
    else
        echo "✗ Migration failed!"
        exit 1
    fi
fi

echo ""
echo "================================================"
echo "Ready! Executing command: $@"
echo "================================================"
echo ""

# Execute the main command
exec "$@"
