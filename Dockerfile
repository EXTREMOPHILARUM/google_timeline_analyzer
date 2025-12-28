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

# Create exports directory
RUN mkdir -p /app/exports

# Set Python path
ENV PYTHONPATH=/app

CMD ["/bin/bash"]
