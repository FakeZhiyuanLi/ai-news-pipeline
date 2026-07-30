"""Narration timing and caching. No network: the alignment is synthetic.

The subtle part is that ElevenLabs aligns audio-tag characters as if they
were spoken text — a [pause] gets real start and end times — so the index map
must skip those characters while keeping the seconds they consume.
"""

import pytest

import tts
from stages import narrate


def _alignment(text, step=0.1):
    """A synthetic per-character alignment, one step per character."""
    return tts.Alignment(list(text),
                         [i * step for i in range(len(text))],
                         [(i + 1) * step for i in range(len(text))])


@pytest.mark.parametrize("tts_text,script_text", [
    ("[excited] It shipped.", "It shipped."),
    ("It shipped. [pause] Prices fell 30%.", "It shipped. Prices fell 30%."),
    ("No tags at all here.", "No tags at all here."),
    ("[a] [b] Double leading tags.", "Double leading tags."),
    ("Ends with a tag. [dry]", "Ends with a tag."),
    ("Mid[emphatic]word is odd.", "Midword is odd."),
])
def test_index_map_recovers_script_text(tts_text, script_text):
    mapping = narrate.build_index_map(tts_text, script_text)
    recovered = "".join(tts_text[i] for i, p in enumerate(mapping) if p >= 0)
    assert recovered == script_text
    positions = [p for p in mapping if p >= 0]
    assert positions == sorted(positions), "positions must be monotonic"
    assert len(set(positions)) == len(positions), "no script position reused"


def test_tag_characters_never_become_words():
    tts_text = "It shipped. [pause] Prices fell."
    script_text = "It shipped. Prices fell."
    words = narrate.words_from_alignment(
        _alignment(tts_text), tts_text, script_text)
    assert [w["text"] for w in words] == ["It", "shipped.", "Prices", "fell."]
    assert not any("[" in w["text"] or "]" in w["text"] for w in words)


def test_tag_elapsed_time_is_preserved():
    """The pause is performed: it occupies real seconds, so the word after it
    must start later, not immediately."""
    tts_text = "It shipped. [pause] Prices fell."
    script_text = "It shipped. Prices fell."
    words = narrate.words_from_alignment(
        _alignment(tts_text), tts_text, script_text)
    after_tag = tts_text.index("]") * 0.1
    prices = next(w for w in words if w["text"] == "Prices")
    assert prices["start_seconds"] >= after_tag


def test_words_are_never_estimated_from_character_counts():
    """Timings must come from the alignment. A non-uniform alignment must
    produce non-uniform word timings."""
    tts_text = script_text = "Fast slow."
    alignment = tts.Alignment(
        list(tts_text),
        [0.0, 0.01, 0.02, 0.03, 0.04, 5.0, 6.0, 7.0, 8.0, 9.0],
        [0.01, 0.02, 0.03, 0.04, 0.05, 6.0, 7.0, 8.0, 9.0, 10.0])
    words = narrate.words_from_alignment(alignment, tts_text, script_text)
    assert words[0]["text"] == "Fast" and words[1]["text"] == "slow."
    assert words[0]["end_seconds"] < 1.0
    assert words[1]["start_seconds"] >= 5.0


def test_lines_carry_start_duration_and_words():
    tts_text = script_text = "It shipped. Prices fell."
    words = narrate.words_from_alignment(
        _alignment(tts_text), tts_text, script_text)
    lines = narrate._assign_to_lines(words, script_text)
    assert [line["text"] for line in lines] == ["It shipped.", "Prices fell."]
    for line in lines:
        assert "start_seconds" in line and "duration_seconds" in line
        assert line["words"] and all(
            {"text", "start_seconds", "end_seconds"} <= set(w) for w in line["words"])
    assert lines[1]["start_seconds"] > lines[0]["start_seconds"]


def test_alignment_length_mismatch_raises():
    with pytest.raises(tts.TTSError):
        tts.Alignment(list("abc"), [0.0], [0.1])


# --- caching ---------------------------------------------------------------

def test_cache_key_changes_with_every_input_that_changes_audio():
    base = narrate.cache_key("hello", "voice-a")
    assert base == narrate.cache_key("hello", "voice-a")
    assert base != narrate.cache_key("hello ", "voice-a")   # text
    assert base != narrate.cache_key("hello", "voice-b")    # voice


def test_cache_key_changes_with_model_and_settings(monkeypatch):
    import config
    base = narrate.cache_key("hello", "voice-a")
    monkeypatch.setattr(config, "TTS_MODEL_ID", "some_other_model")
    assert narrate.cache_key("hello", "voice-a") != base
    monkeypatch.undo()
    monkeypatch.setattr(config, "TTS_VOICE_SETTINGS",
                        {**config.TTS_VOICE_SETTINGS, "speed": 1.5})
    assert narrate.cache_key("hello", "voice-a") != base


def test_failed_script_is_never_narrated():
    with pytest.raises(tts.TTSError, match="failed checking"):
        narrate.narrate({"passed": False, "tts_text": "x", "script_text": "x"},
                        run=None)


def test_missing_voice_id_raises_rather_than_defaulting(monkeypatch):
    import config
    monkeypatch.delenv(config.VOICE_ID_ENV, raising=False)
    with pytest.raises(tts.TTSError, match=config.VOICE_ID_ENV):
        tts.voice_id()
