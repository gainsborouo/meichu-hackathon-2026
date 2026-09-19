"""Usage: uv run python -m app.cli.import_sales [path/to/campaigns.json]"""

import argparse
import asyncio
from pathlib import Path

from app.db.session import dispose_engine, get_sessionmaker
from app.services.sales_import import DEFAULT_CAMPAIGNS_PATH, import_campaigns, load_campaigns


async def main(path: Path) -> None:
    items = load_campaigns(path)
    try:
        async with get_sessionmaker()() as session, session.begin():
            stats = await import_campaigns(session, items)
    finally:
        await dispose_engine()
    print(f"sales import from {path}: {stats}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", type=Path, default=DEFAULT_CAMPAIGNS_PATH)
    asyncio.run(main(parser.parse_args().path))
