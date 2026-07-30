"""All narration calls. The ONLY module that imports the ElevenLabs SDK.

The same rule llm.py follows for text models: swapping narration provider
must touch one file. stages/narrate.py deals in text, timings and caching and
never sees a vendor type.

Everything here was verified against the installed SDK rather than assumed —
v3 timestamp support is recent and its shapes differ from v2:
  - text_to_speech.convert_with_timestamps returns audio_base_64 plus a
    CharacterAlignmentResponseModel of characters / character_start_times_
    seconds / character_end_times_seconds.
  - VoiceSettings for v3 carries stability, similarity_boost, style,
    use_speaker_boost and speed.
"""

import base64
import io
import logging
import os

from elevenlabs.client import ElevenLabs
from elevenlabs.types import VoiceSettings

import config

log = logging.getLogger(__name__)

_client_instance: ElevenLabs | None = None


class TTSError(RuntimeError):
    """Synthesis failed, or was refused."""


class Alignment:
    """Per-character timings, vendor-free. One shape regardless of which path
    produced it."""

    def __init__(self, characters, starts, ends):
        self.characters = list(characters)
        self.starts = list(starts)
        self.ends = list(ends)
        if not (len(self.characters) == len(self.starts) == len(self.ends)):
            raise TTSError(
                f"alignment arrays disagree: {len(self.characters)} characters, "
                f"{len(self.starts)} starts, {len(self.ends)} ends")

    def __len__(self):
        return len(self.characters)


def _client() -> ElevenLabs:
    global _client_instance
    if _client_instance is None:
        key = os.environ.get(config.ELEVENLABS_KEY_ENV)
        if not key:
            raise TTSError(f"{config.ELEVENLABS_KEY_ENV} is not set; add it to .env")
        _client_instance = ElevenLabs(api_key=key)
    return _client_instance


def voice_id() -> str:
    """Read the voice from the environment. NEVER fall back to a default: a
    silent fallback ships a video in the wrong voice."""
    found = os.environ.get(config.VOICE_ID_ENV)
    if not found:
        raise TTSError(
            f"{config.VOICE_ID_ENV} is not set. Resolve it once with:\n"
            f"    python -m stages.narrate --resolve-voice\n"
            "then paste the id into .env. There is no default voice.")
    return found


def find_voices(name: str = config.VOICE_NAME) -> list[tuple[str, str]]:
    """Voices matching a name, for pasting an id into .env. Run by hand."""
    query = name.split(" - ")[0].split(",")[0].strip()
    return [(v.voice_id, v.name)
            for v in _client().voices.search(search=query, page_size=50).voices]


def _settings() -> VoiceSettings:
    return VoiceSettings(**config.TTS_VOICE_SETTINGS)


def synthesize(text: str, voice: str) -> tuple[bytes, Alignment, str]:
    """ONE request for the entire script. Returns (audio, alignment, path).

    Chunking per sentence would break prosody across boundaries and v3 is
    less stable on very short inputs, so the whole script goes in one call.
    """
    try:
        response = _client().text_to_speech.convert_with_timestamps(
            voice_id=voice, text=text,
            model_id=config.TTS_MODEL_ID,
            output_format=config.TTS_OUTPUT_FORMAT,
            voice_settings=_settings())
        raw = response.alignment or response.normalized_alignment
        if raw is None:
            raise TTSError("timestamps endpoint returned no alignment")
        return (base64.b64decode(response.audio_base_64),
                Alignment(raw.characters, raw.character_start_times_seconds,
                          raw.character_end_times_seconds),
                "convert_with_timestamps")
    except TTSError:
        raise
    except Exception as e:
        log.warning("convert_with_timestamps unavailable (%s: %s); falling back "
                    "to convert + forced alignment", type(e).__name__, e)

    audio = b"".join(_client().text_to_speech.convert(
        voice_id=voice, text=text, model_id=config.TTS_MODEL_ID,
        output_format=config.TTS_OUTPUT_FORMAT, voice_settings=_settings()))
    aligned = _client().forced_alignment.create(file=io.BytesIO(audio), text=text)
    return audio, _spread_to_characters(aligned), "forced_alignment"


def _spread_to_characters(aligned) -> Alignment:
    """Forced Alignment returns words or characters with start/end; normalise
    to per-character so both paths yield the same Alignment."""
    characters, starts, ends = [], [], []
    items = getattr(aligned, "characters", None) or getattr(aligned, "words", [])
    for item in items:
        text = getattr(item, "text", "") or ""
        start = float(getattr(item, "start", 0.0))
        end = float(getattr(item, "end", start))
        if len(text) <= 1:
            characters.append(text)
            starts.append(start)
            ends.append(end)
            continue
        step = (end - start) / len(text)
        for k, char in enumerate(text):
            characters.append(char)
            starts.append(start + k * step)
            ends.append(start + (k + 1) * step)
        characters.append(" ")  # word boundary the word-level API implies
        starts.append(end)
        ends.append(end)
    return Alignment(characters, starts, ends)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    print(f"model: {config.TTS_MODEL_ID}  format: {config.TTS_OUTPUT_FORMAT}")
    print(f"settings: {config.TTS_VOICE_SETTINGS}")
    print(f"voice name: {config.VOICE_NAME}")
    try:
        print(f"voice id from env: {voice_id()}")
    except TTSError as e:
        print(f"voice id: {e}")

    alignment = Alignment(list("ab"), [0.0, 0.1], [0.1, 0.2])
    assert len(alignment) == 2
    try:
        Alignment(list("abc"), [0.0], [0.1])
        raise AssertionError("mismatched arrays must raise")
    except TTSError as e:
        print(f"mismatched alignment rejected: {e}")
    print("tts OK (no synthesis performed)")
