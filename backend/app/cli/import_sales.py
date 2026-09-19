"""Usage: uv run python -m app.cli.import_sales [path/to/campaigns.json]

Accepts the crawler's object format (base_benefits + campaigns) or the older bare list.
"""

import argparse
import asyncio
from pathlib import Path

from app.db.session import dispose_engine, get_sessionmaker
from app.services.sales_import import DEFAULT_CAMPAIGNS_PATH, import_dataset, load_dataset


async def main(path: Path) -> None:
    dataset = load_dataset(path)
    try:
        async with get_sessionmaker()() as session, session.begin():
            stats = await import_dataset(session, dataset)
    finally:
        await dispose_engine()
    print(f"import from {path}:")
    print(f"  card_benefits: {stats['benefits']}")
    print(f"  sales:         {stats['sales']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", type=Path, default=DEFAULT_CAMPAIGNS_PATH)
    asyncio.run(main(parser.parse_args().path))
