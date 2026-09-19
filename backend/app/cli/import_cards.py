"""Usage: uv run python -m app.cli.import_cards [path/to/cards.csv]"""

import argparse
import asyncio
from pathlib import Path

from app.db.bootstrap import initialize_database
from app.db.session import dispose_engine, get_sessionmaker
from app.services.card_catalog import (
    DEFAULT_CARD_CATALOG_PATH,
    import_card_catalog,
    load_card_catalog,
)


async def main(path: Path) -> None:
    try:
        await initialize_database()
        async with get_sessionmaker()() as session, session.begin():
            rows = load_card_catalog(path)
            stats = await import_card_catalog(session, rows)
    finally:
        await dispose_engine()
    print(f"cards import from {path}: {stats}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", type=Path, default=DEFAULT_CARD_CATALOG_PATH)
    asyncio.run(main(parser.parse_args().path))
