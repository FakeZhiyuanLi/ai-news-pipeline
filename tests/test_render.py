"""Props building and render refusal. No renderer, no network.

Duration must come from the measured audio length, never from
estimated_seconds — a 20.9s video was once rendered over 24.5s of narration
and silently truncated the final sentence.
"""

import pytest

import render


class _FakeRun:
    def __init__(self, artifacts, run_id="20260101T000000Z-test"):
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
