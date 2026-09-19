"""Open an official bank page from the backend and report whether it was readable.

Page-level verification: a search snippet that merely mentions a bank URL proves
nothing. `fetch_page` actually GETs the page, so `ok=True` means the backend itself got a
2xx HTML/text response with readable text. It never follows a redirect off the bank
allow-list, refuses non-public addresses, and bounds time and size.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from collections.abc import Callable
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from app.services.official_sources import is_official_for_any_bank

TIMEOUT_SECONDS = 10
MAX_BYTES = 512 * 1024
MAX_REDIRECTS = 3
# Below this the page is an empty shell / JS-only stub, not something a person could read.
MIN_TEXT_CHARS = 80
_TEXT_TYPES = ("text/html", "application/xhtml+xml", "text/plain")
_USER_AGENT = "Mozilla/5.0 (compatible; card-recommendation-verifier/1.0)"

# (url) -> (status, headers, body). Injectable so tests never touch the network.
Getter = Callable[[str], tuple[int, dict[str, str], bytes]]
Resolver = Callable[[str], bool]


@dataclass(frozen=True)
class PageResult:
    """Outcome of fetch_page.

    On success (`ok=True`) `url` is the FINAL url after redirects and `text` is what the
    backend read there. On failure `url` is only where it stopped and must never be
    treated as evidence of anything.
    """

    ok: bool
    url: str
    status: int | None = None
    title: str = ""
    text: str = ""
    error: str = ""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # noqa: D102 - we follow manually
        return None


_opener = build_opener(_NoRedirect)


def _get(url: str) -> tuple[int, dict[str, str], bytes]:
    request = Request(url, headers={"User-Agent": _USER_AGENT, "Accept": "text/html,*/*;q=0.5"})
    try:
        with _opener.open(request, timeout=TIMEOUT_SECONDS) as response:
            return (
                response.status,
                {k.lower(): v for k, v in response.headers.items()},
                response.read(MAX_BYTES),
            )
    except HTTPError as exc:  # 3xx (redirect suppressed) and 4xx/5xx arrive here
        return exc.code, {k.lower(): v for k, v in exc.headers.items()}, b""


def is_public_host(host: str) -> bool:
    """True only if every address the name resolves to is a public one."""
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except OSError:
        return False
    addresses = {info[4][0] for info in infos}
    return bool(addresses) and all(ipaddress.ip_address(a).is_global for a in addresses)


class _TextExtractor(HTMLParser):
    _SKIP = {"script", "style", "noscript", "template", "svg"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.parts: list[str] = []
        self._skip_depth = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip_depth:
            self.parts.append(data)


def _decode(body: bytes, content_type: str) -> str:
    match = re.search(r"charset=([\w-]+)", content_type, re.IGNORECASE)
    for encoding in (match.group(1) if match else None, "utf-8"):
        if encoding:
            try:
                return body.decode(encoding)
            except (LookupError, UnicodeDecodeError):
                continue
    return body.decode("utf-8", errors="replace")


def readable_text(body: bytes, content_type: str) -> tuple[str, str]:
    """(title, visible text) with whitespace collapsed."""
    decoded = _decode(body, content_type)
    if "html" not in content_type.lower():
        return "", re.sub(r"\s+", " ", decoded).strip()
    extractor = _TextExtractor()
    extractor.feed(decoded)
    text = re.sub(r"\s+", " ", " ".join(extractor.parts)).strip()
    return re.sub(r"\s+", " ", extractor.title).strip(), text


def fetch_page(url: str, *, get: Getter = _get, is_public: Resolver = is_public_host) -> PageResult:
    current = url.strip()
    for _ in range(MAX_REDIRECTS + 1):
        if not is_official_for_any_bank(current):
            return PageResult(False, current, error="not an official https bank URL")
        host = urlparse(current).hostname or ""
        if not is_public(host):
            return PageResult(False, current, error="host does not resolve to a public address")
        try:
            status, headers, body = get(current)
        except (URLError, TimeoutError, OSError) as exc:
            return PageResult(False, current, error=f"could not open page: {exc}")

        if status in (301, 302, 303, 307, 308):
            location = headers.get("location")
            if not location:
                return PageResult(False, current, status, error="redirect without a location")
            current = urljoin(current, location)
            continue
        if not 200 <= status < 300:
            return PageResult(False, current, status, error=f"HTTP {status}")

        content_type = headers.get("content-type", "")
        if not any(t in content_type.lower() for t in _TEXT_TYPES):
            return PageResult(False, current, status, error=f"not a text page ({content_type})")
        title, text = readable_text(body, content_type)
        if len(text) < MIN_TEXT_CHARS:
            return PageResult(False, current, status, title, text, "page has no readable content")
        return PageResult(True, current, status, title, text)

    return PageResult(False, current, error="too many redirects")
