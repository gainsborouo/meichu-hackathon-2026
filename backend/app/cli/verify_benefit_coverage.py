"""Check that every crawler-enabled card has a usable base benefit in the database.

    uv run python -m app.cli.verify_benefit_coverage [--require KEY,KEY,...]

Run after the crawler and `import_sales`. For each crawler-enabled card it looks at
card_benefits and counts the rows that are in force today and whose source_url is an official
https page of that card's own bank. A card with none of those fails the check, and so does a
required key that is not a crawler-enabled card at all. Exit 0 only when every card passes.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import dispose_engine, get_sessionmaker
from app.models import Card, CardBenefit
from app.repositories.benefits import is_effective
from app.services.official_sources import is_official_url


@dataclass
class CardCoverage:
    card_key: str
    bank: str | None
    total: int
    usable: int
    reason: str

    @property
    def ok(self) -> bool:
        return self.usable > 0


async def check_coverage(
    session: AsyncSession, *, today: date, require: list[str] | None = None
) -> list[CardCoverage]:
    cards = (
        await session.scalars(
            select(Card).where(Card.crawler_enabled.is_(True)).order_by(Card.catalog_key)
        )
    ).all()
    by_key = {c.catalog_key: c for c in cards}
    report: list[CardCoverage] = []

    for key in require or []:
        if key not in by_key:
            report.append(CardCoverage(key, None, 0, 0, "not a crawler-enabled card"))

    for card in cards:
        if require is not None and card.catalog_key not in require:
            continue
        benefits = (
            await session.scalars(select(CardBenefit).where(CardBenefit.card_id == card.id))
        ).all()
        bank = card.search_bank_name or card.bank_name
        usable = [
            b for b in benefits if is_effective(b, today) and is_official_url(bank, b.source_url)
        ]
        if usable:
            reason = "ok"
        elif not benefits:
            reason = "no card_benefits rows"
        elif not any(is_effective(b, today) for b in benefits):
            reason = "rows exist but none is in force today"
        else:
            reason = "rows exist but no official source_url"
        report.append(CardCoverage(card.catalog_key, bank, len(benefits), len(usable), reason))
    return sorted(report, key=lambda r: r.card_key)


def format_report(report: list[CardCoverage]) -> str:
    lines = [f"{'card_key':18} {'bank':10} rows usable  status"]
    for r in report:
        lines.append(
            f"{r.card_key:18} {(r.bank or '-'):10} {r.total:4} {r.usable:6}  "
            f"{'OK' if r.ok else 'MISSING: ' + r.reason}"
        )
    missing = [r.card_key for r in report if not r.ok]
    lines.append(
        f"coverage: {len(report) - len(missing)}/{len(report)} cards have a base benefit"
        + (f"; missing: {', '.join(missing)}" if missing else "")
    )
    return "\n".join(lines)


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument(
        "--require",
        help="comma-separated catalog_keys that must be crawler-enabled and covered "
        "(default: every crawler-enabled card)",
    )
    args = parser.parse_args(argv)
    require = [k.strip() for k in args.require.split(",") if k.strip()] if args.require else None

    try:
        async with get_sessionmaker()() as session:
            report = await check_coverage(session, today=date.today(), require=require)
    finally:
        await dispose_engine()

    print(format_report(report))
    return 0 if report and all(r.ok for r in report) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
