"""Dev seed loader entry point.

Usage:
    uv run python -m scripts.seed_dev_data [--reset] [--dry-run] [--sheets sheet1,sheet2]

Refuses to run if ``settings.environment == "production"`` (safety
guard: this script is for dev/local use only).
"""
import argparse
import asyncio
import sys

from admin_backend.config import get_settings


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Load dev seed data from Excel into Postgres.",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help=(
            "TRUNCATE seed tables before insert (in reverse-FK order). "
            "Destructive."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Read and validate the Excel; do not write to the DB.",
    )
    parser.add_argument(
        "--sheets",
        type=str,
        default=None,
        help=(
            "Comma-separated sheet names to load (default: all loadable "
            "sheets)."
        ),
    )
    args = parser.parse_args()

    settings = get_settings()
    if settings.environment == "production":
        print(
            "ERROR: seed loader refuses to run with "
            "ENVIRONMENT=production. This script is for dev/local use "
            "only.",
            file=sys.stderr,
        )
        return 2

    from scripts.seed_dev_data.runner import run_seed
    return asyncio.run(
        run_seed(
            reset=args.reset,
            dry_run=args.dry_run,
            sheets=(
                args.sheets.split(",") if args.sheets else None
            ),
        )
    )


if __name__ == "__main__":
    sys.exit(main())
