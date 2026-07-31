"""find: gather candidate stories. Writes the candidates artifact.

Candidates come exclusively from allowlisted RSS feeds. Hacker News is fetched
as RANKING SIGNAL ONLY — an HN match boosts a feed candidate's signal_score,
but HN-linked pages are never scraped and HN-only stories never become
candidates. If HN is unreachable we log a warning and rank on RSS alone.

A feed entry with no parseable published date is kept but marked ineligible:
"no date" must never be mistaken for "fresh".
"""

import datetime as _dt
import html
import json
import logging
import re
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import feedparser

import config
import ledger
import sources

log = logging.getLogger(__name__)


def _clean_summary(raw: str) -> str:
    text = re.sub(r"<[^>]+>", " ", raw or "")
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:config.SUMMARY_MAX_CHARS]


def _published_iso(entry) -> str | None:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    # feedparser normalises struct_time to UTC already
    return _dt.datetime(*parsed[:6], tzinfo=_dt.timezone.utc).isoformat()


def _fetch_json(url: str):
    with urllib.request.urlopen(url, timeout=config.HN_FETCH_TIMEOUT_SECONDS) as resp:
        return json.load(resp)


def _fetch_hn_items() -> list[dict] | None:
    """Top HN stories with outbound links, or None if HN is unreachable."""
    try:
        ids = _fetch_json(sources.hn_api_url("topstories.json"))[:config.HN_TOP_STORIES]
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as e:
        log.warning("HN unreachable (%s); ranking with RSS signal alone", e)
        return None

    def fetch_item(item_id):
        try:
            return _fetch_json(sources.hn_api_url(f"item/{item_id}.json"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
            return None  # one dead item is not worth losing the rest

    with ThreadPoolExecutor(max_workers=config.HN_FETCH_THREADS) as pool:
        items = list(pool.map(fetch_item, ids))
    return [i for i in items if i and i.get("url")]  # Ask HN etc. have no url


def _signal_score(hn_item: dict, now: _dt.datetime) -> float:
    posted = _dt.datetime.fromtimestamp(hn_item["time"], tz=_dt.timezone.utc)
    age_hours = max(0.0, (now - posted).total_seconds() / 3600)
    weighted = (config.HN_COMMENT_WEIGHT * hn_item.get("descendants", 0)
                + config.HN_POINT_WEIGHT * hn_item.get("score", 0))
    return round(weighted / (age_hours + config.HN_AGE_OFFSET_HOURS), 2)


def _feed_candidates() -> list[dict]:
    candidates = []
    for src in sources.all_sources():
        feed_url = src["feed_url"]
        if feed_url is None:
            continue  # allowlist-only entry (e.g. an image CDN), not a feed
        if not sources.is_allowed(feed_url):
            raise RuntimeError(
                f"feed for {src['domain']} fails its own allowlist: {feed_url}")
        parsed = feedparser.parse(feed_url)
        if not parsed.entries:
            log.warning("feed %s yielded no entries (bozo=%s)", src["domain"],
                        getattr(parsed, "bozo_exception", None))
            continue
        for entry in parsed.entries:
            title = (entry.get("title") or "").strip()
            url = entry.get("link")
            if not title or not url:
                continue
            published = _published_iso(entry)
            candidates.append({
                "title": title,
                "url": url,
                "published": published,
                "summary": _clean_summary(entry.get("summary", "")),
                "source": src["domain"],
                "eligible": published is not None,
                "ineligible_reason": None if published else "feed omitted published date",
                "hn": None,
                "signal_score": 0.0,
            })
        log.info("feed %s: %d entries", src["domain"], len(parsed.entries))
    return candidates


def run(run) -> None:
    now = _dt.datetime.now(_dt.timezone.utc)
    candidates = _feed_candidates()
    if not candidates:
        raise RuntimeError("no allowlisted feed yielded a single entry — "
                           "refusing to write an empty candidates artifact")

    hn_items = _fetch_hn_items()
    if hn_items is not None:
        by_url = {ledger.normalize_url(i["url"]): i for i in hn_items}
        matched = 0
        for cand in candidates:
            hn = by_url.get(ledger.normalize_url(cand["url"]))
            if hn:
                cand["hn"] = {
                    "score": hn.get("score", 0),
                    "descendants": hn.get("descendants", 0),
                    "time": _dt.datetime.fromtimestamp(
                        hn["time"], tz=_dt.timezone.utc).isoformat(),
                }
                cand["signal_score"] = _signal_score(hn, now)
                matched += 1
        log.info("HN: %d items, %d matched a feed candidate", len(hn_items), matched)

    run.write("candidates", {
        "fetched_at": now.isoformat(timespec="seconds"),
        "hn_available": hn_items is not None,
        "candidates": candidates,
    })
    log.info("wrote %d candidates (%d eligible)", len(candidates),
             sum(c["eligible"] for c in candidates))


if __name__ == "__main__":
    import argparse

    import runs

    parser = argparse.ArgumentParser(description="run the find stage standalone")
    parser.add_argument("--run-id", help="write into an existing run (default: new run)")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")

    runs.load_env()
    current = runs.Run.resume(args.run_id) if args.run_id else runs.Run.new()
    print(f"run {current.run_id}")
    run(current)
    found = current.read("candidates")["candidates"]
    for cand in sorted(found, key=lambda c: c["signal_score"], reverse=True)[:10]:
        print(f"  signal={cand['signal_score']:>7.2f}  [{cand['source']}] {cand['title'][:70]}")
