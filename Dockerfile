FROM python:3.11-slim

# Install system dependencies for PostgreSQL and PostGIS
RUN apt-get update && apt-get install -y \
    postgresql-client \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy uv configuration
COPY pyproject.toml ./

# Install uv and dependencies
RUN pip install uv
RUN uv pip install --system --no-cache-dir .

# Copy application code
COPY src/ ./src/

# Copy Alembic migration files
COPY alembic.ini ./
COPY alembic/ ./alembic/

# Copy and set up docker entrypoint script
COPY docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# Create exports directory
RUN mkdir -p /app/exports

# Set Python path
ENV PYTHONPATH=/app

# Set entrypoint to run migrations automatically
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]

# Default command
CMD ["/bin/bash"]
