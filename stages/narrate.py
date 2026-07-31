"""narrate: synthesize the script. Writes the narration artifact and an mp3.

Narration is ~97% of marginal cost at roughly one credit per character, and a
110-second script costs about four times a short one. So: cache on a hash of
everything that affects the audio, never synthesize a script that failed
checking, and log the character count and credit estimate BEFORE each call.

ONE REQUEST FOR THE WHOLE SCRIPT. Chunking per sentence and stitching would
break prosody across sentence boundaries, lose the cross-sentence timing that
makes subtitles land, and v3 is measurably less stable on very short inputs.

TAG OFFSETS. The alignment indexes against tts_text, which contains audio
tags, and the API aligns those tag characters as if they were text — a
[pause] occupies real seconds in the timeline while corresponding to nothing
in script_text. Word timings are therefore built by walking the alignment
with an index map from tts_text positions to script_text positions, skipping
tag spans while keeping the time they consume.
"""

import hashlib
import json
import logging
import re

import config
import runs
import tts
from checks import sentences as sentence_checks

log = logging.getLogger(__name__)

_BRACKET_RE = re.compile(r"\[[^\]]*\]")

# Running total for the session, so a bench or a retry loop shows cumulative
# spend rather than a series of unrelated line items.
_session_characters = 0
_session_credits = 0.0

NarrationError = tts.TTSError


# --- cache -----------------------------------------------------------------

def cache_key(tts_text: str, voice_id: str) -> str:
    """SHA-256 of everything that changes the audio. v3 is non-deterministic,
    so identical inputs must reuse the recorded take rather than re-rolling
    it."""
    payload = json.dumps({
        "text": tts_text,
        "voice_id": voice_id,
        "model_id": config.TTS_MODEL_ID,
        "output_format": config.TTS_OUTPUT_FORMAT,
        "settings": config.TTS_VOICE_SETTINGS,
    }, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


# --- alignment -------------------------------------------------------------

def tag_spans(tts_text: str) -> list[tuple[int, int]]:
    """(start, end) of every bracketed span in tts_text."""
    return [(m.start(), m.end()) for m in _BRACKET_RE.finditer(tts_text)]


def build_index_map(tts_text: str, script_text: str) -> list[int]:
    """Map each tts_text position to a script_text position, or -1 for
    characters that exist only in tts_text (tags and the whitespace they
    leave behind).

    Built by walking both strings rather than by arithmetic on tag lengths:
    stripping a tag collapses the spaces around it, so positional arithmetic
    drifts by one for every tag in the script.
    """
    index_map = [-1] * len(tts_text)
    spans = tag_spans(tts_text)
    in_tag = [False] * len(tts_text)
    for start, end in spans:
        for i in range(start, end):
            in_tag[i] = True

    script_pos = 0
    for i, char in enumerate(tts_text):
        if in_tag[i]:
            continue
        # Skip whitespace in script_text that the tag stripping collapsed.
        while script_pos < len(script_text) and \
                script_text[script_pos] != char and script_text[script_pos].isspace():
            script_pos += 1
        if script_pos < len(script_text) and script_text[script_pos] == char:
            index_map[i] = script_pos
            script_pos += 1
    return index_map


def words_from_alignment(alignment, tts_text: str, script_text: str) -> list[dict]:
    """Per-word timings, indexed against script_text.

    Real per-word timings come from the alignment. Never estimate them from
    character counts — that was a bug in the previous build.
    """
    characters, starts, ends = alignment.characters, alignment.starts, alignment.ends
    index_map = build_index_map(tts_text, script_text)
    words: list[dict] = []
    current: dict | None = None
    for i, char in enumerate(characters):
        if i >= len(index_map) or index_map[i] < 0:
            continue  # inside a tag: its time is real, but it is not a word
        if char.isspace():
            current = None
            continue
        if current is None:
            current = {"text": "", "start_seconds": round(starts[i], 3),
                       "end_seconds": round(ends[i], 3),
                       "script_start": index_map[i]}
            words.append(current)
        current["text"] += char
        current["end_seconds"] = round(ends[i], 3)
    return words


def _assign_to_lines(words: list[dict], script_text: str) -> list[dict]:
    """Group word timings onto the script's sentences."""
    lines = []
    cursor = 0
    for sentence in sentence_checks.split(script_text):
        start = script_text.find(sentence, cursor)
        if start < 0:
            start = cursor
        end = start + len(sentence)
        cursor = end
        owned = [w for w in words if start <= w["script_start"] < end]
        if not owned:
            continue
        lines.append({
            "text": sentence,
            "start_seconds": owned[0]["start_seconds"],
            "duration_seconds": round(owned[-1]["end_seconds"]
                                      - owned[0]["start_seconds"], 3),
            "words": [{"text": w["text"], "start_seconds": w["start_seconds"],
                       "end_seconds": w["end_seconds"]} for w in owned],
        })
    return lines


# --- synthesis -------------------------------------------------------------

def _synthesize(tts_text: str, voice_id: str):
    """One request for the entire script. Returns (audio, alignment, path)."""
    global _session_characters, _session_credits
    characters = len(tts_text)
    credits = characters * config.CREDITS_PER_CHARACTER
    _session_characters += characters
    _session_credits += credits
    log.info("synthesizing %d characters ≈ %.0f credits "
             "(session total: %d characters ≈ %.0f credits)",
             characters, credits, _session_characters, _session_credits)

    audio, alignment, path = tts.synthesize(tts_text, voice_id)
    log.info("path: %s", path)
    return audio, alignment, path


def narrate(script: dict, run, *, force: bool = False) -> dict:
    """Synthesize the script, reusing a cached take when nothing changed."""
    if not script.get("passed", False):
        raise NarrationError(
            "refusing to narrate a script that failed checking — narration is "
            "~97% of marginal cost and a failed script is not worth a credit")

    tts_text, script_text = script["tts_text"], script["script_text"]
    voice_id = tts.voice_id()
    key = cache_key(tts_text, voice_id)
    filename = f"{key}.mp3"

    audio = None if force else runs.cached_bytes(config.AUDIO_DIR, filename)
    if audio is not None and not force:
        cached_meta = runs.cached_bytes(config.AUDIO_DIR, f"{key}.json")
        if cached_meta:
            log.info("cache hit %s — no credits spent", key)
            meta = json.loads(cached_meta)
            meta["cached"] = True
            meta["audio_path"] = runs.write_cached_bytes(
                config.AUDIO_DIR, filename, audio)
            return meta
        audio = None  # audio without its alignment is not a usable hit

    if force:
        log.info("--force-tts: bypassing the cache for %s", key)

    audio, alignment, path = _synthesize(tts_text, voice_id)
    words = words_from_alignment(alignment, tts_text, script_text)
    lines = _assign_to_lines(words, script_text)
    if not lines:
        raise NarrationError(
            "alignment produced no word timings — subtitles and duration both "
            "depend on these, so this is a hard failure")

    # Duration comes from the REAL audio, never from an estimate: a 20.9s
    # video was once rendered over 24.5s of narration and silently truncated
    # the final sentence.
    duration = round(max(w["end_seconds"] for w in words), 3)
    meta = {
        "voice_id": voice_id,
        "voice_name": config.VOICE_NAME,
        "model_id": config.TTS_MODEL_ID,
        "output_format": config.TTS_OUTPUT_FORMAT,
        "settings": config.TTS_VOICE_SETTINGS,
        "cache_key": key,
        "alignment_path": path,
        "characters": len(tts_text),
        "estimated_credits": len(tts_text) * config.CREDITS_PER_CHARACTER,
        "duration_seconds": duration,
        "estimated_seconds": script.get("estimated_seconds"),
        "lines": lines,
        "cached": False,
    }
    audio_path = runs.write_cached_bytes(config.AUDIO_DIR, filename, audio)
    runs.write_cached_bytes(config.AUDIO_DIR, f"{key}.json",
                            json.dumps(meta, indent=2).encode("utf-8"))
    meta["audio_path"] = audio_path

    estimate = script.get("estimated_seconds")
    if estimate:
        log.info("duration %.1fs (word-count estimate was %.1fs, %+.0f%%)",
                 duration, estimate, 100 * (duration - estimate) / estimate)
    return meta


def run(run, *, force: bool = False) -> None:
    script = run.read("script")
    meta = narrate(script, run, force=force)
    run.write("narration", meta)
    log.info("%s: %.1fs, %d lines, %d words, %s",
             meta["audio_path"], meta["duration_seconds"], len(meta["lines"]),
             sum(len(line["words"]) for line in meta["lines"]),
             "cached" if meta["cached"] else
             f"{meta['estimated_credits']:.0f} credits")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="run the narrate stage standalone")
    parser.add_argument("--run-id", help="run to narrate (default: latest)")
    parser.add_argument("--force-tts", action="store_true",
                        help="bypass the audio cache and re-synthesize")
    parser.add_argument("--resolve-voice", action="store_true",
                        help="look up the configured voice by name and print its id")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")

    if args.resolve_voice:
        print(f"searching for {config.VOICE_NAME!r}")
        for voice_id, name in tts.find_voices():
            marker = "  <-- this one" if name == config.VOICE_NAME else ""
            print(f"  {voice_id}  {name}{marker}")
        print(f"\nPaste the id into .env as {config.VOICE_ID_ENV}=<id>")
        raise SystemExit(0)

    # The index map is the subtle part; exercise it without spending credits.
    tts = "[excited] It shipped. Prices fell 30%. [pause] But only in the U.S."
    clean = "It shipped. Prices fell 30%. But only in the U.S."
    mapping = build_index_map(tts, clean)
    recovered = "".join(tts[i] for i, p in enumerate(mapping) if p >= 0)
    assert recovered == clean, f"index map lost characters: {recovered!r}"
    positions = [p for p in mapping if p >= 0]
    assert positions == sorted(positions) and len(set(positions)) == len(positions)
    assert all(mapping[i] == -1 for i in range(0, 9)), "tag not skipped"
    print(f"index map OK: {len(tts)} tts chars -> {len(positions)} script chars")

    runs.load_env()
    current = runs.Run.resume(args.run_id) if args.run_id else runs.Run.latest()
    print(f"run {current.run_id}")
    if not current.exists("narration") or args.force_tts:
        run(current, force=args.force_tts)
    narration = current.read("narration")
    print(f"\nvoice {narration['voice_name']} ({narration['voice_id']})")
    print(f"{narration['duration_seconds']}s via {narration['alignment_path']}"
          f"{' [cached]' if narration['cached'] else ''}")
    for line in narration["lines"]:
        print(f"  {line['start_seconds']:6.2f} +{line['duration_seconds']:5.2f}  "
              f"{line['text'][:64]}")
