"""Build renderer props and drive the Remotion CLI.

Two rules that the rest of the file exists to enforce:

  DURATION COMES FROM REAL AUDIO. The props carry the measured
  duration_seconds from the narration artifact; the composition derives its
  frame count from that. estimated_seconds is a measurement for logs and is
  never sent to the renderer.

  NEVER RENDER A SCRIPT THAT FAILED CHECKING, or one with no audio. Both are
  real outcomes and should look like errors.
"""

import json
import re
import logging
import subprocess

import config
import runs

log = logging.getLogger(__name__)


class RenderError(RuntimeError):
    """Refused to render, or the renderer failed."""


# Safe-area geometry, mirrored from renderer/src/layout.js. Duplicated
# deliberately: the renderer cannot import Python and the tests must assert
# these numbers without running Node.
SAFE_MARGIN_SIDES = 0.10
SAFE_MARGIN_TOP = 0.08
SAFE_MARGIN_BOTTOM = 0.20

# Caption fitting, mirrored from renderer/src/Caption.jsx.
CAPTION_MAX_LINES = 2
CAPTION_MAX_FONT = 86
CAPTION_MIN_FONT = 54
CAPTION_ADVANCE = 0.56


def _wrap_words(text: str, chars_per_line: int) -> list[str]:
    """Greedy wrap on word boundaries. Never hyphenates."""
    lines, current = [], ""
    for word in str(text).split():
        candidate = f"{current} {word}" if current else word
        if len(candidate) > chars_per_line and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def fit_caption(caption: str, *, width: int = 1080) -> dict | None:
    """Largest size at which the caption fits two lines inside the safe box.

    None means it cannot fit, and the caller REFUSES TO RENDER. A clipped or
    ellipsised headline sits on screen for the whole video and is the part a
    viewer screenshots — shipping a truncated one is worse than not shipping.
    """
    text = str(caption or "").strip()
    if not text:
        return None
    usable = width - 2 * round(width * SAFE_MARGIN_SIDES)
    for size in range(CAPTION_MAX_FONT, CAPTION_MIN_FONT - 1, -2):
        chars = int(usable / (size * CAPTION_ADVANCE))
        if chars < 4:
            continue
        lines = _wrap_words(text, chars)
        if len(lines) <= CAPTION_MAX_LINES and max(map(len, lines)) <= chars:
            return {"size": size, "lines": lines}
    return None


_YEAR_RE = re.compile(r"/(20\d\d)/")


def _upload_year(url: str) -> int | None:
    """The year in a CMS upload path: /wp-content/uploads/2018/10/Dang.jpg."""
    match = _YEAR_RE.search(url)
    return int(match.group(1)) if match else None


def _story_year(report: dict) -> int | None:
    story = report.get("story") or {}
    for value in (story.get("published"), story.get("url", "")):
        match = re.search(r"(20\d\d)", str(value))
        if match:
            return int(match.group(1))
    return None


def beat_kinds_per_line(script: dict, lines: list[dict]) -> list[str]:
    """One kind per NARRATION LINE, not per beat.

    A beat may hold more than one sentence — "The catch: it only worked on a
    weakened version... Likely infeasible outside a lab." is one catch beat —
    and narrate splits on sentences, so lines outnumber beats. Indexing kinds
    by position then shifted every treatment after the multi-sentence beat: the
    closer treatment landed on a caveat, and the real closer fell through to a
    number card.

    Each line is matched to the beat whose sentence contains it, so a beat that
    produced two lines colours both.
    """
    beats = script.get("beats") or []
    kinds = script.get("beat_kinds") or [b.get("kind") for b in beats]
    if not beats:
        return ["proof"] * len(lines)

    normalised = [_normalise(b["sentence"]) for b in beats]
    out, cursor = [], 0
    for line in lines:
        text = _normalise(line["text"])
        # Walk forward from the last matched beat: beats are in script order,
        # so a line never belongs to an earlier one.
        found = None
        for index in range(cursor, len(beats)):
            if text and text in normalised[index]:
                found = index
                break
        if found is None:
            # Unmatched (punctuation drift): keep the previous line's kind
            # rather than silently defaulting to proof.
            found = cursor
        cursor = found
        out.append(kinds[found] if found < len(kinds) else "proof")
    return out


def _normalise(text: str) -> str:
    """Lowercase words separated by single spaces, so a substring test is not
    defeated by the punctuation narrate trims off a sentence boundary."""
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", str(text).lower()).split())


def _usable_images(report: dict, line_count: int) -> list[dict | None]:
    """One image slot per line, or None.

    An image is now only ever a DARKENED BACKDROP behind a treatment, never
    the subject — journalism sources carry one decorative hero image, so a
    photo-first design starves. Every treatment renders with None here, which
    is the common case rather than the fallback.

    Author headshots are dropped. Filename patterns alone did not do it — a
    byline portrait shipped as `Dang.jpg` at 600x400, indistinguishable from a
    story photo by name or shape. What gives it away is the UPLOAD YEAR: the
    portrait sits under /uploads/2018/ on a story published in 2026, because
    the CMS stores a staff photo once and reuses it for years. A story's own
    art is uploaded with the story.
    """
    story_year = _story_year(report)
    images = []
    seen = set()
    for image in report.get("images", []):
        if not (image.get("width") and image.get("height")):
            continue
        width, height = image["width"], image["height"]
        url = image["url"].lower()
        # Square and small: a byline portrait, not a story asset.
        if width == height and width < 600:
            continue
        if "headshot" in url or "author" in url or "avatar" in url:
            continue
        year = _upload_year(url)
        if story_year and year and year < story_year:
            log.info("skipping stale asset (uploaded %d, story %d): %s",
                     year, story_year, image["url"].rsplit("/", 1)[-1])
            continue
        # The same photo often appears at several widths.
        stem = url.rsplit("/", 1)[-1].split("-")[0]
        if stem in seen:
            continue
        seen.add(stem)
        images.append(image)

    slots: list[dict | None] = [None] * line_count
    if not images:
        return slots
    step = max(1, line_count // max(1, len(images)))
    for index, image in enumerate(images):
        position = min(index * step, line_count - 1)
        if slots[position] is None:
            slots[position] = image
    return slots


def build_props(run) -> dict:
    """Props for one run. Raises if the run is not renderable."""
    if not run.exists("script"):
        raise RenderError(
            f"run {run.run_id} has no script artifact; "
            f"produce it with: python run.py --run-id {run.run_id} --stage check")
    script = run.read("script")
    if not script.get("passed", False):
        raise RenderError(
            f"run {run.run_id} failed checking — refusing to render it. "
            f"{script.get('failure', '')}".strip())
    if not run.exists("narration"):
        raise RenderError(
            f"run {run.run_id} has no audio; "
            f"produce it with: python run.py --run-id {run.run_id} --stage narrate")

    narration = run.read("narration")
    audio_path = narration.get("audio_path")
    if not audio_path or not runs.file_exists(audio_path):
        raise RenderError(
            f"narration for {run.run_id} points at {audio_path!r}, which is not "
            "on disk — re-run the narrate stage")

    # The renderer serves from its own public directory, so the cached audio
    # is copied in under a stable name.
    audio_name = f"{narration['cache_key']}.mp3"
    runs.copy_into(config.RENDERER_PUBLIC_DIR, audio_name, audio_path)

    report = run.read("report") if run.exists("report") else {}
    lines = narration["lines"]

    caption = script.get("caption", "")
    if caption and not fit_caption(caption):
        raise RenderError(
            f"the caption does not fit two lines at the minimum size: "
            f"{caption!r}. Refusing to render rather than clipping or "
            "hyphenating a headline that sits on screen for the whole video.")

    # The quote treatment names the speaker beneath the pull-quote; it comes
    # from the report slot, not parsed back out of the sentence.
    quote_slot = (report.get("slots") or {}).get("quote") or {}

    return {
        "runId": run.run_id,
        "title": script["story"]["title"],
        # The persistent overlay: written by draft, verified by check, never
        # spoken. Distinct from `title`, which is the source badge.
        "caption": caption,
        "speaker": {"name": quote_slot.get("name", ""),
                    "role": quote_slot.get("role", "")} if quote_slot else None,
        # Real measured audio length. Not estimated_seconds.
        "durationSeconds": narration["duration_seconds"],
        "audioSrc": audio_name,
        "lines": lines,
        "images": _usable_images(report, len(lines)),
        # What each beat DOES, so a funnel can be treated differently from a
        # plain proof beat. Mapped PER LINE: a multi-sentence beat produces two
        # narration lines, and indexing by beat shifted every later treatment.
        "beatKinds": beat_kinds_per_line(script, lines),
    }


def latest_narrated_run():
    """The most recent run that actually has audio — what --update-props
    previews, so `npm start` always shows something real."""
    for run_id in reversed(runs.all_run_ids()):
        run = runs.Run.resume(run_id)
        if run.exists("narration") and run.exists("script"):
            return run
    raise RenderError("no run has been narrated yet; "
                      "run: python run.py --stage narrate")


def update_props(run=None) -> str:
    run = run or latest_narrated_run()
    props = build_props(run)
    path = runs.write_text_file(
        config.RENDERER_DIR, "props.json",
        json.dumps(props, indent=2, ensure_ascii=False) + "\n")
    log.info("props from run %s: %.1fs, %d lines, %d image(s)",
             run.run_id, props["durationSeconds"], len(props["lines"]),
             sum(1 for i in props["images"] if i))
    return path


def render(run=None, *, quality_preview: bool = False) -> str:
    """Shell out to the Remotion CLI, writing video.mp4 into the run dir."""
    run = run or latest_narrated_run()
    props = build_props(run)
    update_props(run)

    output = runs.absolute_path(
        f"{config.RUNS_DIR}/{run.run_id}/{config.VIDEO_FILENAME}")
    command = [
        "npx", "remotion", "render", "src/index.js", config.COMPOSITION_ID,
        output,
        "--props", runs.absolute_path(config.RENDERER_PROPS_FILE),
    ]
    if quality_preview:
        command += ["--scale", "0.5"]

    log.info("rendering %s -> %s (%.1fs of audio)",
             run.run_id, output, props["durationSeconds"])
    result = subprocess.run(
        command, cwd=runs.absolute_path(config.RENDERER_DIR),
        capture_output=True, text=True,
        timeout=config.RENDER_TIMEOUT_SECONDS)
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "").strip().splitlines()[-25:]
        raise RenderError("remotion render failed:\n" + "\n".join(tail))
    log.info("wrote %s", output)
    return output


# --- verification ----------------------------------------------------------

def probe_duration(path: str) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True, check=True)
    return float(result.stdout.strip())


def probe_mean_volume(path: str) -> float:
    """Mean volume in dB. -91 dB means a silent track — the classic symptom
    of audio that was mounted but never actually decoded."""
    result = subprocess.run(
        ["ffmpeg", "-i", path, "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True)
    for line in result.stderr.splitlines():
        if "mean_volume:" in line:
            return float(line.split("mean_volume:")[1].split("dB")[0].strip())
    raise RenderError(f"volumedetect reported no mean_volume for {path}")


def verify(video_path: str, run) -> list[str]:
    """The three checks the video must pass. Returns problems, empty if good."""
    narration = run.read("narration")
    problems = []

    volume = probe_mean_volume(video_path)
    if volume <= -90.0:
        problems.append(
            f"mean volume {volume} dB — the audio track is silent")

    video_seconds = probe_duration(video_path)
    audio_seconds = narration["duration_seconds"]
    drift = abs(video_seconds - audio_seconds)
    if drift > 1.0:
        problems.append(
            f"video is {video_seconds:.2f}s but the audio is {audio_seconds:.2f}s "
            f"— {drift:.2f}s apart")

    first = narration["lines"][0]
    if abs(first["start_seconds"] - first["words"][0]["start_seconds"]) > 0.001:
        problems.append("first line does not start at its first word's timing")

    log.info("verified: %.2f dB mean volume, video %.2fs vs audio %.2fs",
             volume, video_seconds, audio_seconds)
    return problems


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        run = latest_narrated_run()
        print(f"latest narrated run: {run.run_id}")
        props = build_props(run)
        print(f"  duration {props['durationSeconds']}s (from real audio)")
        print(f"  {len(props['lines'])} lines, "
              f"{sum(1 for i in props['images'] if i)} with images")
        for line, image in zip(props["lines"], props["images"]):
            treatment = "image" if image else "typographic"
            print(f"    {line['start_seconds']:6.2f}  {treatment:12} "
                  f"{line['text'][:48]}")
    except RenderError as e:
        print(f"not renderable: {e}")
