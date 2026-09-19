"""Which URLs count as a bank's official page.

Verification is decided here, in code, from the URL's hostname. The model can
claim a source is official; only a hostname on this allow-list makes it so.
"""

from __future__ import annotations

from urllib.parse import urlparse

# Keyed by the bank's short name; bank_name values in the data vary between
# "中國信託商業銀行" and "中國信託銀行", so lookups go through _bank_key().
BANK_DOMAINS: dict[str, tuple[str, ...]] = {
    "中國信託": ("ctbcbank.com",),
    "玉山": ("esunbank.com", "esunbank.com.tw"),
    "台北富邦": ("fubon.com", "taipeifubon.com.tw"),
    "國泰世華": ("cathaybk.com.tw",),
    "台新": ("taishinbank.com.tw",),
}


def _bank_key(bank_name: str | None) -> str | None:
    if not bank_name:
        return None
    for key in BANK_DOMAINS:
        if key in bank_name:
            return key
    return None


def official_domains(bank_name: str | None) -> tuple[str, ...]:
    key = _bank_key(bank_name)
    return BANK_DOMAINS[key] if key else ()


def is_official_url(bank_name: str | None, url: str | None) -> bool:
    if not url:
        return False
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host:
        return False
    return any(host == d or host.endswith("." + d) for d in official_domains(bank_name))


def is_official_for_any_bank(url: str | None) -> bool:
    """https on any allow-listed bank domain (used before the issuing bank is known)."""
    return any(is_official_url(bank, url) for bank in BANK_DOMAINS)


def normalize_url(url: str) -> str:
    """Comparable form of a URL: lower-case scheme/host, no fragment, no trailing slash."""
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return url.strip()
    path = parsed.path.rstrip("/")
    query = f"?{parsed.query}" if parsed.query else ""
    return f"{parsed.scheme.lower()}://{(parsed.hostname or '').lower()}{path}{query}"


def filter_official(bank_name: str | None, sources: list[dict]) -> list[dict]:
    """Keep only {title, url} entries on the bank's own domain, de-duplicated by URL."""
    seen: set[str] = set()
    kept: list[dict] = []
    for src in sources:
        if not isinstance(src, dict):
            continue
        url = src.get("url")
        if isinstance(url, str) and is_official_url(bank_name, url) and url not in seen:
            seen.add(url)
            kept.append({"title": str(src.get("title") or url), "url": url})
    return kept
