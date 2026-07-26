"""The one place that decides whether a URL may be fetched.

Deny by default. The allowlist lives in sources.json (data, not code) so that
adding a source is a diff to a data file and so slot_yield can be scored per
source later. A URL is allowed only if its hostname IS an allowlisted domain
or is a true subdomain of one — evil-huggingface.co must not match
huggingface.co, hence the dot-boundary check rather than a substring test.
"""

from urllib.parse import urlsplit

import config
import runs

_cache: list[dict] | None = None


def all_sources() -> list[dict]:
    global _cache
    if _cache is None:
        data = runs.load_data_file(config.SOURCES_FILE)
        _cache = data["sources"]
        for entry in _cache:
            for field in ("domain", "feed_url", "name", "added_by", "added_at", "slot_yield"):
                if field not in entry:
                    raise ValueError(
                        f"source entry {entry.get('domain', '?')!r} missing {field!r}")
    return _cache


def reload() -> None:
    global _cache
    _cache = None


def allowed_domains() -> list[str]:
    return [s["domain"].lower() for s in all_sources()]


def is_allowed(url: str) -> bool:
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if parts.scheme not in ("http", "https"):
        return False
    host = parts.hostname  # strips port and userinfo; https://good.co@evil.com -> evil.com
    if not host:
        return False
    host = host.lower().rstrip(".")
    return any(host == d or host.endswith("." + d) for d in allowed_domains())


if __name__ == "__main__":
    for s in all_sources():
        print(f"source: {s['domain']:<20} feed={s['feed_url']}")

    cases = [
        ("https://huggingface.co/blog/some-post", True),
        ("https://www.theverge.com/ai/story", True),
        ("https://evil-huggingface.co/blog", False),        # subdomain spoof
        ("https://huggingface.co.evil.example/x", False),   # suffix spoof
        ("https://huggingface.co@evil.example/x", False),   # userinfo trick
        ("ftp://huggingface.co/file", False),               # scheme
        ("not a url", False),
    ]
    for url, expected in cases:
        got = is_allowed(url)
        status = "ok " if got == expected else "FAIL"
        print(f"{status} is_allowed({url!r}) = {got}")
        assert got == expected, url
    print("sources OK")
