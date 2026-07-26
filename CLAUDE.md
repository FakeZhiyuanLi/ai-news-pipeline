# HyperFeed

Turns one fresh AI news story per day into a short vertical video. Python pipeline,
Gemini for all model calls, ElevenLabs for narration, Remotion for rendering.

This file carries the rules and the scar tissue. Read the code for structure.

---

## Layer rules — non-negotiable

These exist because the previous build violated all four and reached 10,775 lines
doing what should take about 2,000.

1. **Only `llm.py` imports a model SDK.** Stages call `llm.json_call(...)`. Swapping a
   model or provider must touch one file.
2. **Only `runs.py` touches the filesystem for artifacts.** Stages receive a `Run` and
   call `run.read(name)` / `run.write(name, obj)`. No stage constructs a path or knows
   a filename. There is no `latest_*.json` — the run directory is the unit.
3. **Only `sources.py` decides whether a URL may be fetched.** One allowlist, one
   place, deny by default.
4. **Stages never import each other.** One exception: `check.py` calls `draft.py` for
   the retry loop. That is deliberate — the retry is an implementation detail of
   checking, not a pipeline edge.

Artifacts are immutable. `run.write` raises if the artifact already exists.

---

## Pitfalls

Every item below is a bug that actually shipped in the previous build.

**Sentence splitting uses whitespace, never capitalisation.** A period followed by
whitespace is a boundary; a period with no whitespace after it is inside an identifier
or number. `"B200. torch.compile lifts speed"` was silently merged into one sentence
because the splitter used next-word capitalisation, and scripts in this niche
legitimately begin sentences with `torch.compile`, `npm`, `pip`. Keep a short
abbreviation list (U.S., e.g., i.e., Inc., vs.) for the genuine exceptions.

**Video duration comes from real audio length, never from an estimate.** A 20.9s video
was rendered over 24.5s of narration and silently truncated the final sentence.

**Word count is a ceiling, never a target.** `TARGET_WORDS = 112` against five facts
worth ~70 words caused the model to pad — first by restating, then by inventing
"without extra fees", a pricing claim about a real product that appeared nowhere in
the source. Never give the writer a length to reach for.

**Story age is a gate, not a score.** Filter by age *before* ranking. A "daily
briefing" was generated from seven-month-old CES coverage because age was one input to
a score rather than a hard filter.

**Verify claims, not sentences.** `SUPPORTED`/`UNSUPPORTED` per sentence conflates
"asserts something false" with "isn't in the bullet list". Framing and judgment are
never entailed by a fact list, so per-sentence verification makes it structurally
impossible for a script to have a voice.

**A pronoun subject is claim-bearing until resolved.** `"That is a research demo"`
reads as framing but asserts a fact about the preceding sentence — and directly
contradicted it, since that sentence said "now in full production". Pass the prior
sentence as context and resolve before classifying. Judgment must survive resolution:
`"This is the part that should worry NVIDIA"` resolves to a claim about significance
and passes.

**Strip vendor marketing during extraction.** NVIDIA's coinage "extreme-codesigned"
was promoted to a verified fact and built into a script. A claim that is only an
adjective with no number behind it gets dropped.

**Filter site chrome from scraped images aggressively.** `OpenAI_Presence_1x1.png` and
`Frame.png` passed a substring filter and appeared in a rendered video. Reject anything
under 200px on either axis; where dimensions are absent from markup, fetch headers
rather than trusting the HTML.

**Never estimate word timing from character counts.** Real per-word timings come from
the ElevenLabs alignment and are in the payload.

**Deterministic checks run in Python, never through a model.** Verbatim quote matching
is a substring search. Scale-anchor arithmetic is division. Date filtering is a
comparison. These are also the only checks immune to shared model priors, which
matters because the writer and the checker are now the same model family.

---

## Conventions that differ from defaults

**`WORDS_PER_SECOND = 2.1`** — measured across three real narrations, not guessed. The
obvious 2.5 was consistently 20% optimistic for this voice.

**Script length is dynamic, driven by beats.** Each populated report slot is at most
one beat; one sentence per beat; cover once and stop. A three-beat story is a
three-sentence script and that is a correct result. Hard cap `MAX_SECONDS = 110`. The
writer is never shown a target duration — `estimated_seconds` is a measurement.

**Subjectivity is permitted in exactly three places:** which fact leads, what gets
framed as the catch, and sentence rhythm. Never about the world.

**Two text fields, always.** `script_text` is clean and is the source of truth for
subtitles, checking, and visual matching. `tts_text` carries ElevenLabs v3 audio tags.
Stripping bracketed spans from `tts_text` must yield `script_text` exactly — assert it.

**Rate limits are the main failure mode.** Single provider: `gemini-3.5-flash-lite` at
15 RPM (cheap work), `gemini-3.6` at 5 RPM (extraction and checking). A run makes 4–11
calls. `llm.py` enforces a token bucket per model with 429 backoff, and logs every
wait so stalls are visible rather than mysterious.

**Narration is ~97% of marginal cost** at roughly one credit per character. Everything
else is rounding. Cache narration on a hash of the exact input; never synthesize a
script that failed checking.

**`REFERENCE_SCALES` is maintained by hand** in `config.py`, each value with a `source`
and `as_of`. A scale anchor computed from a model-supplied reference is a
hallucination wearing a division sign.

---

## Out of scope

Do not add these. Each was considered and rejected for this build.

- Orchestration frameworks (Airflow, Prefect), LangChain, LlamaIndex
- Vector databases — this is daily news, not retrieval
- Docker for local development
- Image or video generation APIs
- A stock B-roll library — the fallback is typographic
- Any human-in-the-loop recording path; a script is just text
- Multi-story videos — one story per video, `STORIES_PER_VIDEO` is not a thing here

---

## Working agreements

- **This file is context, not enforcement.** The layer rules above are enforced by
  `tests/test_architecture.py`, which AST-walks the source and fails on violations.
  If a boundary matters, it belongs in that test as well as here.
- **This file does not update itself.** Auto memory writes elsewhere. Add a line here
  when a pitfall is discovered — the concrete failure, not a general principle — and
  keep the file under 200 lines. Directory layouts, dependency lists, and architecture
  overviews go in README.md; they go stale here.
- Every module gets a `__main__` block that exercises it standalone.
- Pin dependencies. The previous build had zero version pins.
- `run.py --selftest` runs the pipeline against committed fixtures with all model
  calls stubbed. Use it to verify changes without consuming rate limit.
- Prefer failing loudly to degrading quietly. No fresh story, a script that fails
  checking, or a run with no audio are all real outcomes and should look like errors.
- README.md is generated last from what the code actually does, not from what it is
  planned to do.