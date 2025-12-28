"""Alembic migration environment configuration with PostGIS support."""
import os
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from alembic import context

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Import Base metadata from our models
from src.core.database import Base

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Set target metadata for 'autogenerate' support
target_metadata = Base.metadata

# Override sqlalchemy.url with environment variable if present
database_url = os.getenv("DATABASE_URL")
if database_url:
    config.set_main_option("sqlalchemy.url", database_url)


def include_object(object, name, type_, reflected, compare_to):
    """
    Filter which objects should be included in autogenerate.

    This allows us to exclude certain tables, views, or other objects
    from being detected as changes.
    """
    # Exclude PostGIS system tables from autogenerate
    # These are internal PostGIS/TIGER geocoding tables that should not be managed by our migrations
    postgis_tables = {
        # Core PostGIS tables
        'spatial_ref_sys', 'topology', 'layer',
        # TIGER geocoding tables
        'state', 'county', 'edges', 'addr', 'addrfeat', 'faces', 'featnames',
        'place', 'cousub', 'tract', 'tabblock', 'tabblock10', 'tabblock20', 'zcta5', 'bg',
        # ZIP code lookup tables
        'zip_lookup', 'zip_lookup_all', 'zip_lookup_base', 'zip_state', 'zip_state_loc',
        # Geocoding lookup tables
        'county_lookup', 'countysub_lookup', 'place_lookup', 'state_lookup',
        'direction_lookup', 'secondary_unit_lookup', 'street_type_lookup',
        # Geocoding settings and loader tables
        'geocode_settings', 'geocode_settings_default',
        'loader_lookuptables', 'loader_platform', 'loader_variables',
        # PAGC (address standardizer) tables
        'pagc_gaz', 'pagc_lex', 'pagc_rules',
    }

    if type_ == "table":
        # Exclude PostGIS system tables
        return name not in postgis_tables

    # Exclude views from autogenerate (we'll handle them manually)
    if type_ == "view":
        return False

    return True


def compare_type(context, inspected_column, metadata_column, inspected_type, metadata_type):
    """
    Custom type comparison for PostGIS Geography types.

    Alembic doesn't natively understand GeoAlchemy2 Geography types,
    so we need custom comparison logic to prevent false positives
    when comparing database schema to model metadata.
    """
    from geoalchemy2 import Geography, Geometry

    # If the metadata type is a Geography or Geometry type
    if isinstance(metadata_type, (Geography, Geometry)):
        # Don't flag as a difference - assume it matches
        # (PostGIS types are complex and Alembic has trouble with them)
        return False

    # For all other types, use default comparison
    return None


def run_migrations_offline() -> None:
    """
    Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well. By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
        compare_type=compare_type,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """
    Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.
    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
            compare_type=compare_type,
            # Enable comparing of server defaults
            compare_server_default=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
