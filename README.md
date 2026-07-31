# HyperFeed

Turns one fresh AI news story per day into a short vertical video: finds the story,
extracts reporting notes from the source article, writes a script, verifies every
claim in it, narrates it, and renders a 1080×1920 MP4.

About 5,600 lines of Python and 360 lines of JSX. `CLAUDE.md` holds the rules and the
accumulated pitfalls; this file describes what the code does.

## Requirements

- Python 3.11+ (developed on 3.14)
- Node 21+ and `npx`, for the Remotion renderer
- `ffmpeg` and `ffprobe` on `PATH`, for render verification
- API keys for Fireworks (all LLM calls) and ElevenLabs (narration)

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cd renderer && npm install && cd ..
cp .env.example .env      # then fill in the keys
```

`ELEVENLABS_VOICE_ID` is resolved once by name:

```sh
python -m stages.narrate --resolve-voice     # prints the id to paste into .env
```

## Running it

```sh
python run.py                    # every stage in order, on a new run
python run.py --stage report     # one stage
python run.py --resume           # continue the most recent run
python run.py --run-id run7 --stage draft
python run.py --render           # render video.mp4 into the run directory
```

Other flags:

| flag | what it does |
| --- | --- |
| `--costs` | spend per purpose per run, so the model split is visible without arithmetic |
| `--selftest` | runs draft + check + both pure checkers against committed fixtures with every model call replayed from recordings. Zero API calls, under a second |
| `--update-props` | writes `renderer/props.json` from the most recent narrated run |
| `--force-tts` | bypasses the narration cache (narration is ~97% of marginal cost, so this spends real money) |
| `--verbose` | debug logging |

## The pipeline

Each stage reads and writes artifacts in `runs/run1/`, `runs/run2/`, ... — numbered
in creation order and sorted numerically, so `run10` follows `run9`. Artifacts are
immutable — writing one twice raises.

| stage | reads | writes | what it does |
| --- | --- | --- | --- |
| `find` | — | `candidates` | parses the allowlisted RSS feeds; fetches Hacker News as a ranking signal only, never as a source |
| `pick` | `candidates` | `story` | age gate, then ledger gate, then one ranking call over all survivors |
| `report` | `story` | `report` | fetches the article, extracts six reporting slots, keeps the full source text |
| `draft` | `report` | `draft` | writes the script from the notes, with deterministic prose gates |
| `check` | `report`, `draft` | `script` | extracts and verifies every claim; redrafts on refutation |
| `narrate` | `script` | `narration` + mp3 | one ElevenLabs request for the whole script, with per-word timings |
| render | `script`, `narration` | `video.mp4` | Remotion, via `run.py --render` |

### find

Reads `sources.json` — the allowlist is data, not code. Entries with `feed_url: null`
are fetch-only (an image CDN, say). Hacker News contributes `signal_score` to feed
candidates it matches by URL; HN-linked pages are never scraped.

### pick

Three gates in order, the first two free:

1. **age** — anything older than `MAX_STORY_AGE_HOURS` is dropped before ranking. If
   nothing survives, the run fails naming the newest candidate and its age.
2. **covered** — URL matches in `ledger.json` drop; a related title survives flagged
   `is_update` with the prior run's slots attached.
3. **rank** — one model call scoring all survivors together.

### report

Scores every container in the page on paragraph-text length and takes the deepest one
still holding ~all of it, so extraction does not depend on a site's class names.
Images come from within the article body, filtered on filename patterns and a 200px
floor — dimensions absent from the markup are read from the image's own header bytes.

Six slots, each nullable: `lede`, `so_what`, `numbers` (ranked), `scale_anchor`,
`quote`, `tension`. Null is a correct answer.

### draft

Writes copy from the notes rather than editing them, enforced in Python by
`checks/prose.py`: a sentence sharing 8+ consecutive words with the notes is
passthrough and rejected, as is uniform pacing. Rejection retries once with the
offending sentences quoted back, then raises.

### check

Cheapest checks first. `checks/quotes.py` and `checks/numbers.py` are pure functions —
a quote is a substring search, an anchor is recomputed arithmetic. Framing and
judgment pass on type. Only factual claims reach a model, batched into one call.

`SUPPORTED` claims are absorbed into the report slots and the sentence stands;
`REFUTED` claims go on a forbidden list and the script is redrafted, up to
`MAX_DRAFT_ATTEMPTS`. An erosion floor requires at least half the first attempt's
beats to survive, plus the lede and one proof beat.

### narrate

One request for the whole script against the with-timestamps endpoint, so prosody is
preserved and the character alignment gives exact per-word timings. Cached on a
SHA-256 of text, voice, model and settings.

### render

`renderer/` holds one Remotion composition. Duration is derived from the narration's
measured length, never from an estimate.

**One treatment per beat kind**, selected by `beats[].kind` rather than by whether a
photo exists — journalism sources carry one decorative hero image, so a photo-first
design starved. All six work with zero images, which is the common case:

| kind | treatment |
| --- | --- |
| `hook` | caption-dominant, largest type in the video |
| `proof` | number card: the figure counting up, unit resolving beneath |
| `funnel` | two bars, larger first, animated so the drop is what the eye follows |
| `quote` | pull-quote card with speaker and compressed role; never over a photo |
| `catch` | the caveat struck through, the consequence beneath it |
| `closer` | the hook's composition, resolved |

The visual state changes at least every 3 seconds, including within a long beat.
Subtitles are word-by-word from the alignment. All text sits inside a safe box — 10% at
the sides, 8% top, 20% bottom — because platform UI covers the frame edges. A caption
that cannot fit two lines at the minimum size fails the render rather than shipping
clipped.

`run.py --render` refuses a script that failed checking or has no audio, then verifies
the output: mean volume must not be ~-91 dB (silence), video duration must be within
one second of the audio, and the first line's subtitle must sit at its `start_seconds`.

## Layout

```
run.py          CLI entry point and stage registry
config.py       every constant
runs.py         run directories and ALL filesystem access
llm.py          all text-model calls (the only module importing the OpenAI SDK)
tts.py          all narration calls (the only module importing the ElevenLabs SDK)
render.py       renderer props and the Remotion CLI
sources.py      the fetch allowlist
ledger.py       record of covered stories
bench.py        regenerate drafts over existing reports and read them side by side
stages/         find, pick, report, draft, check, narrate
checks/         claims, quotes, numbers, prose, sentences — pure where possible
renderer/       one Remotion composition
tests/          139 tests, including tests/test_architecture.py
```

Four layer rules — one module per SDK, all filesystem access in `runs.py`, one
allowlist, no cross-stage imports except `check` → `draft` — are enforced by
`tests/test_architecture.py`, which AST-walks the source and fails with file and line.

## Models

Four purposes, three model families, chosen so the checker shares priors with neither
the writer nor the extractor. `llm.py` takes a purpose, never a model name, and
`config.MODELS` maps it:

| purpose | model | why |
| --- | --- | --- |
| `rank` | Kimi K2.6 | trivial task, lowest time-to-first-token |
| `report` | DeepSeek V4 Pro | 1M context, cached-input discount, judgment |
| `draft` | Kimi K2.6 | prose quality; reasoning off, which measured identical at 1/27th the cost |
| `check` | GLM 5.2 | a third family, so it does not share the priors that produced a bad claim |

The endpoint is OpenAI-compatible, so pointing `config.BASE_URL` at a self-hosted vLLM
or SGLang deployment needs no code change.

## Iterating on the writing

```sh
python bench.py --variants 3 --bench 5
python bench.py --run-ids run3 run5 --draft-model accounts/fireworks/models/kimi-k3
```

Regenerates drafts over existing reports — `find`, `pick`, `report`, `narrate` and
render do not run. Prints each script with its sentence lengths, which report slots it
used, and the pairwise word overlap between variants; above ~90% the prompt is
over-constrained. Importing the narration SDK during a bench raises.

Output goes to `data/bench/bench1.md`, `bench2.md`, ... and stdout. Total spend is
asserted under `BENCH_MAX_SPEND_USD`.

## Testing

```sh
python -m pytest tests/ -q          # 139 tests, no network
python -m pytest tests/ -m live     # hits the API: schema enforcement per model
python -m pytest tests/ -m slow     # renders frames through Remotion; ~2min
python run.py --selftest            # full draft + check on fixtures, zero API calls
python -m checks.prose              # every module runs standalone
```
