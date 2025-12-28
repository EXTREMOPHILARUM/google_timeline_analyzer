"""
CLI entry point for Google Timeline Analyzer.

This allows running the CLI with: python3 -m src.cli
"""
from .commands import app

if __name__ == "__main__":
    app()
