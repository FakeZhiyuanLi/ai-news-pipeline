"""Record of every story the pipeline has covered, in ledger.json.

Exists so the same story is never covered twice and so near-duplicate
follow-ups can be detected before a run wastes model calls. Also records how
each run turned out (final duration, beat count) — the raw material for
noticing that a source or slot consistently yields thin videos.
"""

import datetime as _dt
import re
from urllib.parse import urlsplit, urlunsplit

import config
import runs

_STOPWORDS = frozenset(
    "a an and as at by for from in is of on the to with".split())


def _load() -> dict:
    try:
        return runs.load_data_file(config.LEDGER_FILE)
    except runs.MissingDataFileError:
        return {"stories": []}


def _save(data: dict) -> None:
    runs.save_data_file(config.LEDGER_FILE, data)


def normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    host = host.removeprefix("www.")  # feeds and HN disagree on www.
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme.lower(), host, path, "", ""))


def normalize_title(title: str) -> str:
    tokens = re.findall(r"[a-z0-9]+", title.lower())
    return " ".join(t for t in tokens if t not in _STOPWORDS)


def is_covered(url: str) -> bool:
    norm = normalize_url(url)
    return any(normalize_url(s["url"]) == norm for s in _load()["stories"])


def find_related(title: str, min_jaccard: float = config.RELATED_TITLE_JACCARD) -> list[dict]:
    """Stories whose normalised titles overlap this one (Jaccard on token sets)."""
    tokens = set(normalize_title(title).split())
    if not tokens:
        return []
    related = []
    for story in _load()["stories"]:
        other = set(story["normalized_title"].split())
        union = tokens | other
        if union and len(tokens & other) / len(union) >= min_jaccard:
            related.append(story)
    return related


def record(*, url: str, title: str, run_id: str, published: str,
           slots: list[str], duration_seconds: float | None = None,
           beat_count: int | None = None) -> dict:
    """Add a covered story. duration/beats may be filled in later via
    set_final_stats once the render is done."""
    if is_covered(url):
        raise ValueError(f"story already covered: {url}")
    entry = {
        "url": normalize_url(url),
        "title": title,
        "normalized_title": normalize_title(title),
        "run_id": run_id,
        "published": published,
        "slots": slots,
        "duration_seconds": duration_seconds,
        "beat_count": beat_count,
        "recorded_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
    }
    data = _load()
    data["stories"].append(entry)
    _save(data)
    return entry


def set_final_stats(run_id: str, *, duration_seconds: float, beat_count: int) -> None:
    """Attach the rendered video's real duration and beat count to its story."""
    data = _load()
    for story in data["stories"]:
        if story["run_id"] == run_id:
            story["duration_seconds"] = duration_seconds
            story["beat_count"] = beat_count
            _save(data)
            return
    raise KeyError(f"no ledger entry for run {run_id!r}")


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        config.LEDGER_FILE = tmp + "/ledger.json"  # keep the demo off the real ledger

        entry = record(
            url="https://demo.test/2026/b200-inference/",
            title="NVIDIA B200 doubles inference speed for open models",
            run_id="20260725T120000Z-abcd",
            published="2026-07-24",
            slots=["headline", "number", "catch"],
        )
        print(f"recorded: {entry['normalized_title']!r}")
        assert is_covered("https://demo.test/2026/b200-inference")  # trailing slash normalised
        assert not is_covered("https://demo.test/other")
        hits = find_related("B200 inference speed doubled for open models")
        assert len(hits) == 1, hits
        print(f"find_related matched: {hits[0]['title']!r}")
        assert find_related("Totally unrelated quantum sensor news") == []
        set_final_stats("20260725T120000Z-abcd", duration_seconds=41.7, beat_count=8)
        assert _load()["stories"][0]["duration_seconds"] == 41.7
        try:
            record(url="https://demo.test/2026/b200-inference", title="dup",
                   run_id="x", published="2026-07-24", slots=[])
        except ValueError as e:
            print(f"duplicate rejected: {e}")
    print("ledger OK")
