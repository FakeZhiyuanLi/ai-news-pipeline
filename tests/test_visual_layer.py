"""The visual layer: treatments per beat kind, safe areas, cut rate.

These render the committed fixture through the real Remotion CLI with ZERO
images, because zero images is the common case — journalism sources carry one
decorative hero photo, and after de-duplication and headshot filtering a story
often has none. Every beat kind must still produce a distinct, non-empty frame.

Marked `slow`: they shell out to npx and take ~30s. Run them with
    python -m pytest tests/test_visual_layer.py -m slow
"""

import json
import subprocess

import pytest

import render
import runs

pytestmark = pytest.mark.slow

FPS = 30
WIDTH, HEIGHT = 1080, 1920

# One line per beat kind, timed so every kind gets its own scene and at least
# one beat runs long enough to require a mid-beat change.
KINDS = ["hook", "proof", "funnel", "quote", "catch", "closer"]
TEXTS = [
    "5,000 labs asked Washington for AI money.",
    "The pot was $5 billion.",
    "5,000 applied and 278 were selected.",
    'One expert put it plainly: "This is not how science gets funded"',
    "The plan boosts foundational biology without saying where the line falls.",
    "Few won.",
]


def _line(index, text, start, duration):
    words = text.split()
    per = duration / max(1, len(words))
    return {
        "text": text,
        "start_seconds": start,
        "duration_seconds": duration,
        "words": [{"text": w,
                   "start_seconds": round(start + i * per, 3),
                   "end_seconds": round(start + (i + 1) * per, 3)}
                  for i, w in enumerate(words)],
    }


@pytest.fixture(scope="module")
def props(tmp_path_factory):
    """Props with NO images at all — the case the layer must survive."""
    start, lines = 0.0, []
    for i, text in enumerate(TEXTS):
        # 5s each: long enough that a static beat would violate the cut rate.
        lines.append(_line(i, text, start, 5.0))
        start += 5.0
    return {
        "runId": "test",
        "title": "A source badge",
        "caption": "Federal AI money mostly went unspent",
        "speaker": {"name": "Claes de Vreese", "role": "AI policy professor"},
        "durationSeconds": start,
        "audioSrc": None,
        "lines": lines,
        "images": [None] * len(lines),
        "beatKinds": KINDS,
    }


@pytest.fixture(scope="module")
def frames(tmp_path_factory, props):
    """One PNG per beat, rendered by the real compositor."""
    directory = tmp_path_factory.mktemp("frames")
    props_file = directory / "props.json"
    props_file.write_text(json.dumps(props))

    out = {}
    for index, kind in enumerate(KINDS):
        # Mid-beat, so the treatment is in a settled state rather than mid-entry.
        frame = int((index * 5.0 + 2.5) * FPS)
        target = directory / f"{kind}.png"
        result = subprocess.run(
            ["npx", "remotion", "still", "src/index.js", "NewsVideo",
             str(target), f"--props={props_file}", f"--frame={frame}"],
            cwd=runs.absolute_path("renderer"),
            capture_output=True, text=True, timeout=600)
        assert result.returncode == 0, result.stderr[-2000:]
        out[kind] = target
    return out


def _pixels(path):
    """Frame as a flat list of (r,g,b), via ffmpeg — no image library pin."""
    result = subprocess.run(
        ["ffmpeg", "-i", str(path), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, timeout=120)
    assert result.returncode == 0, result.stderr[-500:]
    raw = result.stdout
    return [tuple(raw[i:i + 3]) for i in range(0, len(raw), 3)]


def test_every_beat_kind_renders_a_non_empty_frame(frames):
    """With zero images, type and shape must carry all six treatments."""
    for kind, path in frames.items():
        pixels = _pixels(path)
        lit = sum(1 for r, g, b in pixels if r + g + b > 200)
        assert lit > 20_000, f"{kind} rendered an essentially empty frame ({lit} lit px)"


def test_beat_kinds_are_visually_distinct(frames):
    """A treatment per kind, not one treatment relabelled six times."""
    import hashlib
    digests = {}
    for kind, path in frames.items():
        pixels = _pixels(path)
        # Coarse signature: mean brightness per horizontal band.
        bands = []
        rows = HEIGHT // 12
        for b in range(12):
            chunk = pixels[b * rows * WIDTH:(b + 1) * rows * WIDTH]
            bands.append(sum(sum(p) for p in chunk) // max(1, len(chunk)) // 8)
        digests[kind] = tuple(bands)
    assert len(set(digests.values())) == len(KINDS), \
        f"treatments collapsed to the same layout: {digests}"


def test_no_text_outside_the_safe_area(frames):
    """Platform UI covers the edges; text outside the box is never read."""
    left = round(WIDTH * render.SAFE_MARGIN_SIDES)
    right = WIDTH - left
    top = round(HEIGHT * render.SAFE_MARGIN_TOP)
    bottom = HEIGHT - round(HEIGHT * render.SAFE_MARGIN_BOTTOM)

    for kind, path in frames.items():
        pixels = _pixels(path)

        def bright_in(x0, x1, y0, y1):
            count = 0
            for y in range(y0, y1):
                row = y * WIDTH
                for x in range(x0, x1):
                    r, g, b = pixels[row + x]
                    # Near-white: type, not a gradient or a bar fill.
                    if r > 210 and g > 210 and b > 210:
                        count += 1
            return count

        # Sample the margins. A handful of stray pixels is antialiasing; a
        # letterform is hundreds.
        assert bright_in(0, left, top, bottom) < 200, f"{kind}: text past the left margin"
        assert bright_in(right, WIDTH, top, bottom) < 200, f"{kind}: text past the right margin"
        assert bright_in(0, WIDTH, 0, top) < 200, f"{kind}: text above the top margin"


def test_the_visual_state_changes_at_least_every_three_seconds(tmp_path, props):
    """Attention resets every 3-4s in this format. A 20-word quote beat held
    one static card for ten seconds before this."""
    props_file = tmp_path / "props.json"
    props_file.write_text(json.dumps(props))

    step = 1.0
    total = props["durationSeconds"]
    signatures = []
    times = []
    t = 0.5
    while t < total:
        target = tmp_path / f"t{t}.png"
        result = subprocess.run(
            ["npx", "remotion", "still", "src/index.js", "NewsVideo",
             str(target), f"--props={props_file}", f"--frame={int(t * FPS)}"],
            cwd=runs.absolute_path("renderer"),
            capture_output=True, text=True, timeout=600)
        assert result.returncode == 0, result.stderr[-2000:]
        pixels = _pixels(target)
        rows = HEIGHT // 16
        signatures.append(tuple(
            sum(sum(p) for p in pixels[b * rows * WIDTH:(b + 1) * rows * WIDTH])
            // max(1, rows * WIDTH) // 4
            for b in range(16)))
        times.append(t)
        t += step

    # No run of samples spanning more than 3s may be identical.
    run_start = 0
    for i in range(1, len(signatures)):
        if signatures[i] != signatures[run_start]:
            run_start = i
            continue
        held = times[i] - times[run_start]
        assert held <= 3.0, (
            f"the frame was visually static for {held:.1f}s "
            f"(from {times[run_start]:.1f}s to {times[i]:.1f}s)")


def test_render_duration_stays_within_one_second_of_the_audio():
    """Guards the pitfall this project already shipped once: a 20.9s video
    rendered over 24.5s of narration, truncating the final sentence."""
    latest = render.latest_narrated_run()
    if latest is None or not latest.exists("narration"):
        pytest.skip("no narrated run to verify against")
    video_path = f"runs/{latest.run_id}/video.mp4"
    if not runs.file_exists(video_path):
        pytest.skip("no rendered video in the latest run")
    audio = latest.read("narration")["duration_seconds"]
    video = render.probe_duration(runs.absolute_path(video_path))
    assert abs(video - audio) < 1.0, f"video {video}s vs audio {audio}s"


@pytest.mark.parametrize("text,expect_pair", [
    # A real funnel: same unit, one a subset of the other.
    ("5,000 applied and 278 were selected.", True),
    ("Revenue fell from $50 billion to $12 billion.", True),
    # NOT a funnel: money against time. Drawing these as proportional bars
    # invented a relationship the script never claimed.
    ("60 hours and $100,000 bought the compute to break it.", False),
    ("It took 3 years and cost $5 million.", False),
])
def test_funnel_requires_compatible_units(text, expect_pair):
    """funnelPair took the two largest numbers regardless of unit and drew
    "$100,000" against "60 hours" as proportional bars — a chart comparing
    money to time. draft.py defines a funnel as two numbers SHARING a unit
    where one counts a subset of the other; the renderer must agree."""
    # Run from renderer/src so layout.js's own `./schema.js` import resolves;
    # an --input-type=module eval has no base path and cannot find it.
    harness = (f"import {{funnelPair}} from './layout.js';\n"
               f"console.log(JSON.stringify(funnelPair({text!r}) !== null));\n")
    probe = runs.absolute_path("renderer/src/_funnel_probe.mjs")
    runs.write_text_file("renderer/src", "_funnel_probe.mjs", harness)
    try:
        result = subprocess.run(["node", probe], capture_output=True,
                                text=True, timeout=60)
    finally:
        subprocess.run(["rm", "-f", probe], check=False)
    assert result.returncode == 0, result.stderr[-800:]
    assert json.loads(result.stdout.strip()) is expect_pair
