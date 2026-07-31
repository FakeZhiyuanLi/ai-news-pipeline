"""Props building and render refusal. No renderer, no network.

Duration must come from the measured audio length, never from
estimated_seconds — a 20.9s video was once rendered over 24.5s of narration
and silently truncated the final sentence.
"""

import re

import pytest

import render
import runs


class _FakeRun:
    def __init__(self, artifacts, run_id="run1"):
        self.run_id = run_id
        self._artifacts = artifacts

    def exists(self, name):
        return name in self._artifacts

    def read(self, name):
        return self._artifacts[name]


PASSED_SCRIPT = {
    "passed": True,
    "story": {"title": "A headline"},
    "estimated_seconds": 99.0,  # deliberately wrong: must never reach props
}
NARRATION = {
    "duration_seconds": 23.12,
    "cache_key": "abc123",
    "audio_path": "audio/abc123.mp3",
    "lines": [
        {"text": "One.", "start_seconds": 0.0, "duration_seconds": 1.0,
         "words": [{"text": "One.", "start_seconds": 0.0, "end_seconds": 1.0}]},
        {"text": "Two.", "start_seconds": 1.5, "duration_seconds": 1.0,
         "words": [{"text": "Two.", "start_seconds": 1.5, "end_seconds": 2.5}]},
    ],
}


@pytest.fixture
def stub_fs(monkeypatch):
    monkeypatch.setattr(render.runs, "file_exists", lambda path: True)
    monkeypatch.setattr(render.runs, "copy_into",
                        lambda directory, filename, source: f"{directory}/{filename}")


def test_duration_comes_from_real_audio_not_the_estimate(stub_fs):
    run = _FakeRun({"script": PASSED_SCRIPT, "narration": NARRATION,
                    "report": {"images": []}})
    props = render.build_props(run)
    assert props["durationSeconds"] == 23.12
    assert props["durationSeconds"] != PASSED_SCRIPT["estimated_seconds"]
    assert "estimated_seconds" not in props
    assert "estimatedSeconds" not in props


def test_refuses_a_script_that_failed_checking(stub_fs):
    run = _FakeRun({"script": {**PASSED_SCRIPT, "passed": False,
                               "failure": "still refuting claims"},
                    "narration": NARRATION, "report": {"images": []}})
    with pytest.raises(render.RenderError, match="failed checking"):
        render.build_props(run)


def test_refuses_a_run_with_no_audio(stub_fs):
    run = _FakeRun({"script": PASSED_SCRIPT, "report": {"images": []}})
    with pytest.raises(render.RenderError, match="no audio"):
        render.build_props(run)


def test_refuses_a_run_with_no_script(stub_fs):
    run = _FakeRun({"narration": NARRATION})
    with pytest.raises(render.RenderError, match="no script"):
        render.build_props(run)


def test_refuses_when_the_audio_file_is_missing(monkeypatch):
    monkeypatch.setattr(render.runs, "file_exists", lambda path: False)
    run = _FakeRun({"script": PASSED_SCRIPT, "narration": NARRATION,
                    "report": {"images": []}})
    with pytest.raises(render.RenderError, match="not on disk|re-run the narrate"):
        render.build_props(run)


def test_word_timings_are_passed_through_verbatim(stub_fs):
    run = _FakeRun({"script": PASSED_SCRIPT, "narration": NARRATION,
                    "report": {"images": []}})
    props = render.build_props(run)
    assert props["lines"][0]["words"][0]["start_seconds"] == 0.0
    assert props["lines"][1]["words"][0]["start_seconds"] == 1.5


def test_one_image_slot_per_line(stub_fs):
    report = {"images": [{"url": "a.jpg", "width": 1600, "height": 900}]}
    run = _FakeRun({"script": PASSED_SCRIPT, "narration": NARRATION,
                    "report": report})
    props = render.build_props(run)
    assert len(props["images"]) == len(props["lines"])
    assert props["images"][0]["url"] == "a.jpg"
    assert props["images"][1] is None  # typographic


def test_lines_without_images_are_typographic(stub_fs):
    run = _FakeRun({"script": PASSED_SCRIPT, "narration": NARRATION,
                    "report": {"images": []}})
    props = render.build_props(run)
    assert all(image is None for image in props["images"])


def test_images_missing_dimensions_are_skipped(stub_fs):
    report = {"images": [{"url": "a.jpg"}, {"url": "b.jpg", "width": 0, "height": 0}]}
    run = _FakeRun({"script": PASSED_SCRIPT, "narration": NARRATION,
                    "report": report})
    props = render.build_props(run)
    assert all(image is None for image in props["images"])


def test_safe_area_and_caption_constants_match_the_renderer():
    """render.py mirrors renderer/src/layout.js and Caption.jsx because the
    renderer cannot import Python and these tests must not need Node. Mirrored
    constants drift silently, so the duplication is asserted rather than
    trusted — only the render-time refusal would otherwise catch it, and only
    on the one caption that happened to be too long."""
    layout = runs.read_text_file("renderer/src/layout.js")
    caption = runs.read_text_file("renderer/src/Caption.jsx")

    def js_number(source, pattern):
        found = re.search(pattern, source)
        assert found, f"could not find {pattern!r} in the renderer source"
        return float(found.group(1))

    assert js_number(layout, r"WIDTH \* ([\d.]+)\)") == render.SAFE_MARGIN_SIDES
    assert js_number(layout, r"top: Math\.round\(HEIGHT \* ([\d.]+)\)") == \
        render.SAFE_MARGIN_TOP
    assert js_number(layout, r"bottom: Math\.round\(HEIGHT \* ([\d.]+)\)") == \
        render.SAFE_MARGIN_BOTTOM
    assert js_number(layout, r"export const ADVANCE = ([\d.]+)") == \
        render.CAPTION_ADVANCE
    assert js_number(caption, r"const MAX_SIZE = (\d+)") == render.CAPTION_MAX_FONT
    assert js_number(caption, r"const MIN_SIZE = (\d+)") == render.CAPTION_MIN_FONT
    assert js_number(layout, r"CAPTION_MAX_LINES = (\d+)") == render.CAPTION_MAX_LINES


def test_beat_kinds_map_to_lines_not_beat_positions():
    """A beat may hold more than one sentence and narrate splits on sentences,
    so lines outnumber beats. Indexing kinds by position shifted every
    treatment after the multi-sentence beat: the closer treatment landed on a
    caveat and the real closer fell through to a number card."""
    script = {
        "beats": [
            {"kind": "hook", "sentence": "A code lasted 60 hours."},
            {"kind": "catch",
             "sentence": "It only worked on a weakened version. "
                         "Likely infeasible outside a lab."},
            {"kind": "closer", "sentence": "HAWK was withdrawn."},
        ],
        "beat_kinds": ["hook", "catch", "closer"],
    }
    lines = [{"text": "A code lasted 60 hours."},
             {"text": "It only worked on a weakened version."},
             {"text": "Likely infeasible outside a lab."},
             {"text": "HAWK was withdrawn."}]
    assert render.beat_kinds_per_line(script, lines) == \
        ["hook", "catch", "catch", "closer"]


def test_beat_kinds_survive_punctuation_drift():
    script = {"beats": [{"kind": "hook", "sentence": 'He said "no" today.'}],
              "beat_kinds": ["hook"]}
    assert render.beat_kinds_per_line(script, [{"text": 'He said "no" today'}]) \
        == ["hook"]


def test_run_ids_sort_numerically_not_lexicographically():
    """Run ids used to be UTC timestamps, where string order happened to equal
    time order — `latest()` just takes the last element of a sort. `run2` and
    `run10` do not have that property: a plain sort makes run10 the
    second-oldest, so --resume and --render would silently target the wrong
    run once a tenth exists."""
    with runs.temporary_root():
        created = [runs.Run.new().run_id for _ in range(12)]
        assert created[-1] == "run12"
        assert runs.Run.latest().run_id == "run12"
        assert runs.all_run_ids()[-3:] == ["run10", "run11", "run12"]


def test_run_numbering_starts_at_one_and_skips_nothing():
    with runs.temporary_root():
        assert runs.Run.new().run_id == "run1"
        assert runs.Run.new().run_id == "run2"


def test_a_legacy_timestamped_run_never_outranks_a_numbered_one():
    """Old directories may still be lying around; they must not be picked as
    `latest()` and hand --resume a stale run."""
    with runs.temporary_root() as root:
        (root / "runs").mkdir(parents=True, exist_ok=True)
        (root / "runs" / "20260730T095944Z-c078").mkdir()
        runs.Run.new()
        assert runs.Run.latest().run_id == "run1"


def test_bench_filenames_are_numbered_and_sort_numerically():
    """Bench output used to be UTC-timestamped, where string order equalled
    creation order. `bench10.md` sorts before `bench2.md` as a string, so the
    next name must come from a numeric max, not from the tail of a listing."""
    with runs.temporary_root():
        for _ in range(12):
            name = runs.next_numbered_name("data/bench", "bench", ".md")
            runs.write_text_file("data/bench", name, "x")
        assert name == "bench12.md"
        assert runs.next_numbered_name("data/bench", "bench", ".md") == "bench13.md"


def test_numbered_name_starts_at_one_in_an_empty_or_absent_directory():
    with runs.temporary_root():
        assert runs.next_numbered_name("data/bench", "bench", ".md") == "bench1.md"
