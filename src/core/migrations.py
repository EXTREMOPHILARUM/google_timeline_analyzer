"""
Database migration helpers using Alembic.

Provides programmatic access to Alembic migration operations
for use in CLI commands and application initialization.
"""

import subprocess
import sys
from pathlib import Path
from typing import Optional

from rich.console import Console

console = Console()


def run_migrations(revision: str = "head") -> bool:
    """
    Run Alembic migrations to upgrade database schema.

    Args:
        revision: Target revision (default: "head" for latest)

    Returns:
        True if migrations succeeded, False otherwise
    """
    try:
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", revision],
            capture_output=True,
            text=True,
            check=True,
        )
        console.print(f"[green]✓ Migrations applied successfully to: {revision}")
        if result.stdout:
            console.print(result.stdout)
        return True
    except subprocess.CalledProcessError as e:
        console.print(f"[red]✗ Migration failed: {e}")
        if e.stderr:
            console.print(f"[red]{e.stderr}")
        if e.stdout:
            console.print(e.stdout)
        return False
    except Exception as e:
        console.print(f"[red]✗ Unexpected error during migration: {e}")
        return False


def downgrade_migration(revision: str = "-1") -> bool:
    """
    Downgrade database schema to a previous revision.

    Args:
        revision: Target revision (default: "-1" for previous revision)

    Returns:
        True if downgrade succeeded, False otherwise
    """
    try:
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "downgrade", revision],
            capture_output=True,
            text=True,
            check=True,
        )
        console.print(f"[green]✓ Downgraded successfully to: {revision}")
        if result.stdout:
            console.print(result.stdout)
        return True
    except subprocess.CalledProcessError as e:
        console.print(f"[red]✗ Downgrade failed: {e}")
        if e.stderr:
            console.print(f"[red]{e.stderr}")
        return False


def get_current_revision() -> Optional[str]:
    """
    Get current database migration revision.

    Returns:
        Current revision ID or None if unavailable
    """
    try:
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "current"],
            capture_output=True,
            text=True,
            check=True,
        )
        # Parse output to extract revision ID
        output = result.stdout.strip()
        if output:
            # Output format: "revision_id (head)" or "revision_id"
            revision = output.split()[0] if output.split() else None
            return revision
        return None
    except subprocess.CalledProcessError:
        return None


def get_migration_history(verbose: bool = False) -> bool:
    """
    Display migration history.

    Args:
        verbose: Show detailed history

    Returns:
        True if successful, False otherwise
    """
    try:
        cmd = [sys.executable, "-m", "alembic", "history"]
        if verbose:
            cmd.append("--verbose")

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
        )
        console.print(result.stdout)
        return True
    except subprocess.CalledProcessError as e:
        console.print(f"[red]✗ Failed to get history: {e}")
        return False


def create_migration(message: str, autogenerate: bool = True) -> bool:
    """
    Create a new migration revision.

    Args:
        message: Migration description
        autogenerate: Auto-generate migration from model changes

    Returns:
        True if migration created successfully, False otherwise
    """
    try:
        cmd = [sys.executable, "-m", "alembic", "revision"]
        if autogenerate:
            cmd.append("--autogenerate")
        cmd.extend(["-m", message])

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
        )
        console.print(f"[green]✓ Migration created: {message}")
        if result.stdout:
            console.print(result.stdout)
        return True
    except subprocess.CalledProcessError as e:
        console.print(f"[red]✗ Failed to create migration: {e}")
        if e.stderr:
            console.print(f"[red]{e.stderr}")
        return False


def check_migration_status() -> dict:
    """
    Check database migration status.

    Returns:
        Dictionary with status information
    """
    current = get_current_revision()

    status = {
        "current_revision": current,
        "is_up_to_date": False,
    }

    try:
        # Check if there are pending migrations
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "heads"],
            capture_output=True,
            text=True,
            check=True,
        )
        head_revision = result.stdout.strip().split()[0] if result.stdout.strip() else None

        status["head_revision"] = head_revision
        status["is_up_to_date"] = (current == head_revision)

        return status
    except subprocess.CalledProcessError:
        return status


def ensure_migrations_current() -> bool:
    """
    Ensure database is up to date with latest migrations.

    Returns:
        True if database is current or was successfully updated
    """
    status = check_migration_status()

    if status.get("is_up_to_date"):
        console.print("[green]✓ Database is up to date")
        return True

    console.print("[yellow]⚠ Database migrations are pending")
    console.print(f"  Current: {status.get('current_revision', 'None')}")
    console.print(f"  Head: {status.get('head_revision', 'Unknown')}")

    # Automatically run migrations
    return run_migrations("head")
