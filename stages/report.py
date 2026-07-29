"""report: turn the picked story's page into reporting notes. Writes report.json.

This is the stage that decides whether any of this is worth watching;
everything downstream is plumbing. One STRONG_MODEL call extracts REPORTING
notes, not a spec sheet: every slot is drawn only from the source text, null
where unsupported — and null is a correct answer. There is no fixed item
count; numbers may hold one entry or twelve, ranked by consequence because
the script truncates from the bottom at MAX_SECONDS.

The full source text is retained in report.json — check.py re-queries it
later. The quote slot is verified verbatim (substring search, in Python)
right here, since this is the moment we hold both the quote and the text.

Every fetch — article and image alike — is gated by sources.is_allowed.
"""

import datetime as _dt
import logging
import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

import config
import llm
import sources

log = logging.getLogger(__name__)

_REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "lede": {"type": ["string", "null"]},
        "so_what": {"type": ["string", "null"]},
        "numbers": {"type": "array", "items": {"type": "string"}},
        "scale_anchor": {
            "type": ["object", "null"],
            "properties": {
                "statement": {"type": "string"},
                "anchor_math": {"type": "string"},
                "reference_key": {"type": "string",
                                  "enum": sorted(config.REFERENCE_SCALES)},
            },
            "required": ["statement", "anchor_math", "reference_key"],
        },
        "quote": {
            "type": ["object", "null"],
            "properties": {
                "text": {"type": "string"},
                "name": {"type": "string"},
                "role": {"type": "string"},
            },
            "required": ["text", "name", "role"],
        },
        "tension": {"type": ["string", "null"]},
    },
    "required": ["lede", "so_what", "numbers", "scale_anchor", "quote", "tension"],
}

_PROMPT = """\
You are a reporter compiling notes from ONE article for a short news video.
These are reporting notes, not a spec sheet. Every slot must be drawn only
from the article text below. Where the article does not support a slot,
return null — null is a correct answer, not a failure.

Slots:
- lede: the single most consequential or surprising thing in the article, one
  sentence, plain language. Not the headline restated.
- so_what: why this matters, AS THE ARTICLE STATES IT. Press releases always
  explain this somewhere — find that passage and strip the adjectives.
- numbers: metrics, prices, benchmarks, dates. Ranked by consequence, most
  consequential first. Include a number only if it changes what someone can
  do, or is surprising. Ten routine version bumps are one entry, not ten.
  There is no target count — one entry and twelve are both correct answers
  when that is what the article supports.
- scale_anchor: ONE of the numbers restated in human terms using one of the
  reference values listed below, and nothing else. anchor_math must be a
  checkable arithmetic expression over the article's number and the reference
  value, e.g. "490 / 500". Null if nothing anchors cleanly.
- quote: a verbatim quote from a NAMED person, copied character-for-character
  from the article text, with their name and role. Null if no named person is
  quoted.
- tension: a caveat, limitation, unshipped date, or unsupported platform the
  article itself states. Null if none.

Strip vendor marketing everywhere: superlatives, coined compound adjectives,
"industry-leading", "first-ever". A claim that is only an adjective with no
number behind it gets dropped.

Reference values for scale_anchor (maintained by hand — use ONLY these):
{scales}
{update_context}
Title: {title}

Article text:
{text}
"""

_UPDATE_CONTEXT = """
This story UPDATES prior coverage. Slots already covered before: {prior_slots}.
A contrast the article itself supports ("the third such commitment this
year") is reporting, not opinion — and it is the one thing a single press
release cannot contain. Prefer such angles; do not re-lead with what was
already covered.
"""


# --- article body ----------------------------------------------------------

def _fetch(url: str) -> requests.Response:
    if not sources.is_allowed(url):
        raise RuntimeError(f"refusing to fetch non-allowlisted URL: {url}")
    resp = requests.get(url, timeout=config.FETCH_TIMEOUT_SECONDS,
                        headers={"User-Agent": config.HTTP_USER_AGENT})
    resp.raise_for_status()
    return resp


def _article_body(soup: BeautifulSoup):
    """The tightest container that still holds essentially all paragraph text.

    Scored on text length, never 'first match': parents outscore their
    children only by the junk they add, so among containers within 95% of the
    top score the deepest one is the article body."""
    candidates = soup.find_all(["article", "main", "section", "div"]) or [soup]
    scores = [(sum(len(p.get_text(" ", strip=True)) for p in el.find_all("p")), el)
              for el in candidates]
    best = max(score for score, _ in scores)
    if best == 0:
        return soup.body or soup
    finalists = [el for score, el in scores if score >= 0.95 * best]
    return max(finalists, key=lambda el: len(list(el.parents)))


def _body_text(body) -> str:
    for tag in body.find_all(["script", "style", "noscript", "nav",
                              "footer", "form", "aside"]):
        tag.decompose()
    blocks = []
    for el in body.find_all(["p", "h2", "h3", "li"]):
        if el.name == "li" and el.find("p"):
            continue  # its paragraphs are captured on their own
        text = el.get_text(" ", strip=True)
        if text:
            blocks.append(text)
    return "\n\n".join(blocks)


# --- images ----------------------------------------------------------------

def _parse_image_dimensions(data: bytes) -> tuple[int, int] | None:
    """Pixel dimensions from the file's own header bytes (PNG/GIF/JPEG/WebP).
    HTTP headers do not carry dimensions; the image header does."""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        return (int.from_bytes(data[16:20]), int.from_bytes(data[20:24]))
    if data[:6] in (b"GIF87a", b"GIF89a") and len(data) >= 10:
        return (int.from_bytes(data[6:8], "little"),
                int.from_bytes(data[8:10], "little"))
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP" and len(data) >= 30:
        chunk = data[12:16]
        if chunk == b"VP8X":
            return (int.from_bytes(data[24:27], "little") + 1,
                    int.from_bytes(data[27:30], "little") + 1)
        if chunk == b"VP8 ":
            return (int.from_bytes(data[26:28], "little") & 0x3FFF,
                    int.from_bytes(data[28:30], "little") & 0x3FFF)
        if chunk == b"VP8L":
            bits = int.from_bytes(data[21:25], "little")
            return ((bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1)
    if data[:2] == b"\xff\xd8":  # JPEG: scan for a start-of-frame marker
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                break
            marker = data[i + 1]
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                return (int.from_bytes(data[i + 7:i + 9]),
                        int.from_bytes(data[i + 5:i + 7]))
            i += 2 + int.from_bytes(data[i + 2:i + 4])
    return None


def _int_attr(el, name: str) -> int | None:
    value = el.get(name) or ""
    return int(value) if re.fullmatch(r"\d+", value.strip()) else None


def _largest_srcset_url(el) -> str | None:
    entries = []
    for part in (el.get("srcset") or "").split(","):
        tokens = part.split()
        if tokens:
            width = int(tokens[1][:-1]) if len(tokens) > 1 \
                and re.fullmatch(r"\d+w", tokens[1]) else 0
            entries.append((width, tokens[0]))
    return max(entries)[1] if entries else None


def _fetched_dimensions(url: str) -> tuple[int, int] | None:
    resp = requests.get(url, timeout=config.FETCH_TIMEOUT_SECONDS,
                        headers={"User-Agent": config.HTTP_USER_AGENT,
                                 "Range": f"bytes=0-{config.IMAGE_DIM_FETCH_BYTES - 1}"},
                        stream=True)
    resp.raise_for_status()
    data = resp.raw.read(config.IMAGE_DIM_FETCH_BYTES)
    resp.close()
    return _parse_image_dimensions(data)


def _collect_images(body, base_url: str) -> tuple[list[dict], list[dict]]:
    kept, rejected = [], []
    seen = set()
    fetches = 0
    for img in body.find_all("img"):
        src = _largest_srcset_url(img) or img.get("src") or img.get("data-src")
        if not src or src.startswith("data:"):
            continue
        url = urljoin(base_url, src)
        if url in seen:
            continue
        seen.add(url)

        def reject(reason):
            rejected.append({"url": url, "reason": reason})

        path = url.split("?")[0].lower()
        pattern = next((p for p in config.IMAGE_REJECT_PATTERNS if p in path), None)
        if pattern:
            reject(f"chrome pattern {pattern!r}")
            continue

        width, height = _int_attr(img, "width"), _int_attr(img, "height")
        if width is None or height is None:
            # dimensions absent from markup: fetch the image's own header
            if not sources.is_allowed(url):
                reject("dimensions absent and host not allowlisted")
                continue
            if fetches >= config.MAX_IMAGE_DIM_FETCHES:
                reject("dimensions absent and fetch budget exhausted")
                continue
            fetches += 1
            try:
                dims = _fetched_dimensions(url)
            except requests.RequestException as e:
                reject(f"dimension fetch failed: {e.__class__.__name__}")
                continue
            if dims is None:
                reject("dimensions undeterminable from image header")
                continue
            width, height = dims
        if min(width, height) < config.MIN_IMAGE_DIM_PX:
            reject(f"too small: {width}x{height}")
            continue
        kept.append({"url": url, "width": width, "height": height,
                     "alt": (img.get("alt") or "").strip()})
    return kept, rejected


# --- slots -----------------------------------------------------------------

def _extract_slots(story: dict, source_text: str, run) -> dict:
    scales = "\n".join(
        f"- {key}: {s['value']} {s['unit']} ({s['source']}, as of {s['as_of']})"
        for key, s in config.REFERENCE_SCALES.items())
    update_context = ""
    if story.get("is_update"):
        update_context = _UPDATE_CONTEXT.format(
            prior_slots=", ".join(story["prior_slots"]) or "unknown")
    prompt = _PROMPT.format(scales=scales, update_context=update_context,
                            title=story["title"], text=source_text)
    slots = llm.json_call(config.STRONG_MODEL, prompt, _REPORT_SCHEMA,
                          run=run, purpose="extract-report")

    quote = slots.get("quote")
    if quote and quote["text"] not in source_text:
        log.warning("quote is not verbatim in the source; dropping it: %r",
                    quote["text"])
        slots["quote"] = None
    return slots


def run(run) -> None:
    story = run.read("story")
    log.info("reporting on: %s", story["url"])
    resp = _fetch(story["url"])
    soup = BeautifulSoup(resp.text, "html.parser")
    body = _article_body(soup)
    images, rejected_images = _collect_images(body, base_url=story["url"])
    source_text = _body_text(body)
    if len(source_text) < config.MIN_ARTICLE_CHARS:
        raise RuntimeError(
            f"extracted only {len(source_text)} chars from {story['url']} — "
            "that is an extraction failure, not a short article")
    log.info("extracted %d chars, %d images kept, %d rejected",
             len(source_text), len(images), len(rejected_images))

    slots = _extract_slots(story, source_text, run)
    populated = [s for s in config.REPORT_SLOTS if slots.get(s)]
    log.info("populated slots: %s", ", ".join(populated) or "none")

    run.write("report", {
        "story": {"title": story["title"], "url": story["url"],
                  "is_update": story.get("is_update", False),
                  "prior_slots": story.get("prior_slots", [])},
        "fetched_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "source_text": source_text,
        "images": images,
        "rejected_images": rejected_images,
        "slots": slots,
    })


# --- standalone ------------------------------------------------------------

def _print_report(report: dict) -> None:
    slots = report["slots"]
    print(f"story: {report['story']['title']}")
    if report["story"]["is_update"]:
        print(f"  update — prior slots: {', '.join(report['story']['prior_slots'])}")
    for name in ("lede", "so_what"):
        print(f"{name}: {slots[name] or '— null'}")
    numbers = slots["numbers"]
    print(f"numbers ({len(numbers)}):" if numbers else "numbers: — none")
    for i, n in enumerate(numbers, 1):
        print(f"  {i}. {n}")
    anchor = slots["scale_anchor"]
    print(f"scale_anchor: {anchor['statement']}  [{anchor['anchor_math']}, "
          f"{anchor['reference_key']}]" if anchor else "scale_anchor: — null")
    quote = slots["quote"]
    print(f'quote: "{quote["text"]}" — {quote["name"]}, {quote["role"]}'
          if quote else "quote: — null (no named person survived verification)")
    print(f"tension: {slots['tension'] or '— null'}")
    print(f"images: {len(report['images'])} kept, "
          f"{len(report['rejected_images'])} rejected")
    for img in report["rejected_images"]:
        print(f"  rejected ({img['reason']}): {img['url'][:90]}")


def _print_null_slot_trend() -> None:
    """quote/tension coming back null across most sources is a finding about
    the source list, and it should be impossible to miss."""
    import runs
    totals = {"quote": 0, "tension": 0}
    reports = 0
    for run_id in runs.all_run_ids():
        prior = runs.Run.resume(run_id)
        if prior.exists("report"):
            reports += 1
            slots = prior.read("report")["slots"]
            for name in totals:
                totals[name] += slots[name] is None
    if reports >= 3:
        for name, nulls in totals.items():
            if nulls / reports > 0.5:
                print(f"\nFINDING: {name} was null in {nulls}/{reports} reports "
                      "so far — the source list may not carry this slot")


if __name__ == "__main__":
    import argparse

    import runs

    parser = argparse.ArgumentParser(description="run the report stage standalone")
    parser.add_argument("--run-id", help="run to report within (default: latest)")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")

    current = runs.Run.resume(args.run_id) if args.run_id else runs.Run.latest()
    print(f"run {current.run_id}")
    if not current.exists("report"):
        run(current)
    _print_report(current.read("report"))
    _print_null_slot_trend()
