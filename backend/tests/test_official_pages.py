"""Page-level verification: the backend must really open the page. No network here."""

import pytest

from app.services import official_pages
from app.services.official_pages import MIN_TEXT_CHARS, fetch_page, readable_text

GOOD = (
    "<html><head><title>玉山活動</title><script>var x=1</script></head><body>"
    + ("<p>指定網購享 3% 回饋，活動期間內每月上限 500 點。</p>" * 5)
    + "</body></html>"
)
HTML = {"content-type": "text/html; charset=utf-8"}
URL = "https://www.esunbank.com/promo"


def getter(responses):
    """Serve canned (status, headers, body) per URL and record what was requested."""
    calls: list[str] = []

    def get(url):
        calls.append(url)
        return responses[url]

    get.calls = calls
    return get


def public(_host):
    return True


def test_readable_page_opens_and_strips_scripts():
    result = fetch_page(URL, get=getter({URL: (200, HTML, GOOD.encode())}), is_public=public)
    assert result.ok and result.status == 200 and result.title == "玉山活動"
    assert "指定網購享 3% 回饋" in result.text and "var x" not in result.text


@pytest.mark.parametrize(
    ("status", "headers", "body", "reason"),
    [
        (404, HTML, b"", "HTTP 404"),
        (500, HTML, b"", "HTTP 500"),
        (200, {"content-type": "application/pdf"}, b"%PDF", "not a text page"),
        (200, HTML, b"<html><body>hi</body></html>", "no readable content"),
        (200, HTML, b"<html><script>" + b"x" * 500 + b"</script></html>", "no readable content"),
    ],
)
def test_unreadable_pages_do_not_open(status, headers, body, reason):
    result = fetch_page(URL, get=getter({URL: (status, headers, body)}), is_public=public)
    assert not result.ok and reason in result.error


@pytest.mark.parametrize(
    "url",
    [
        "http://www.esunbank.com/promo",
        "https://www.ptt.cc/bbs/creditcard/M.1.html",
        "https://www.esunbank.com.evil.example/promo",
        "https://evil.example/www.esunbank.com",
        "ftp://www.esunbank.com/x",
    ],
)
def test_non_official_urls_are_never_requested(url):
    get = getter({})
    result = fetch_page(url, get=get, is_public=public)
    assert not result.ok and get.calls == []


def test_redirect_within_the_bank_is_followed():
    final = "https://www.esunbank.com.tw/promo/new"
    get = getter(
        {
            URL: (301, {"location": "/promo/new"}, b""),
            "https://www.esunbank.com/promo/new": (302, {"location": final}, b""),
            final: (200, HTML, GOOD.encode()),
        }
    )
    result = fetch_page(URL, get=get, is_public=public)
    assert result.ok and result.url == final
    assert get.calls == [URL, "https://www.esunbank.com/promo/new", final]


def test_redirect_off_the_allow_list_is_refused_without_following():
    get = getter({URL: (302, {"location": "https://evil.example/phish"}, b"")})
    result = fetch_page(URL, get=get, is_public=public)
    assert not result.ok and "not an official" in result.error
    assert get.calls == [URL]  # the off-list hop was never requested


def test_redirect_loops_and_missing_location_fail():
    loop = getter({URL: (302, {"location": URL}, b"")})
    assert "too many redirects" in fetch_page(URL, get=loop, is_public=public).error
    bare = getter({URL: (302, {}, b"")})
    assert "without a location" in fetch_page(URL, get=bare, is_public=public).error


def test_non_public_hosts_are_refused():
    get = getter({URL: (200, HTML, GOOD.encode())})
    result = fetch_page(URL, get=get, is_public=lambda host: False)
    assert not result.ok and "public" in result.error and get.calls == []


def test_network_errors_do_not_open_the_page():
    def boom(url):
        raise TimeoutError("timed out")

    result = fetch_page(URL, get=boom, is_public=public)
    assert not result.ok and "timed out" in result.error


def test_public_host_check_rejects_private_and_loopback(monkeypatch):
    def fake(addr):
        return lambda host, port, proto=0: [(2, 1, 6, "", (addr, port))]

    for addr, expected in [("127.0.0.1", False), ("10.0.0.5", False), ("93.184.216.34", True)]:
        monkeypatch.setattr(official_pages.socket, "getaddrinfo", fake(addr))
        assert official_pages.is_public_host("www.esunbank.com") is expected

    def unresolvable(*a, **k):
        raise OSError("no such host")

    monkeypatch.setattr(official_pages.socket, "getaddrinfo", unresolvable)
    assert official_pages.is_public_host("www.esunbank.com") is False


def test_text_extraction_collapses_whitespace_and_honours_charset():
    title, text = readable_text(
        "<title> A \n B </title><p>你好\n\n世界</p>".encode("big5"), "text/html; charset=big5"
    )
    assert title == "A B" and text == "你好 世界"
    assert MIN_TEXT_CHARS >= 1
