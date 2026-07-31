# HyperFeed

This file carries the rules and the scar tissue. README.md describes the structure;
read the code for the rest.

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

Every item below is a bug that actually shipped.

**Sentence splitting uses whitespace, never capitalisation.** A period followed by
whitespace is a boundary; one without is inside an identifier or number. Capitalisation
merged `"B200. torch.compile lifts speed"` into one sentence — scripts here legitimately
open with `torch.compile`, `npm`, `pip`. Keep a short abbreviation list (U.S., e.g.,
i.e., Inc., vs.) for the genuine exceptions.

**Video duration comes from real audio length, never an estimate.** A 20.9s video over
24.5s of narration silently truncated the final sentence. `calculateMetadata` derives
frames from the measured `duration_seconds`; scripts run 15–110s, so any fixed
`durationInFrames` is wrong.

**Word count is a ceiling, never a target.** `TARGET_WORDS = 112` against ~70 words of
facts made the model pad — first restating, then inventing "without extra fees", a
pricing claim absent from the source. Never give the writer a length to reach for.

**Story age is a gate, not a score.** Filter by age *before* ranking. A "daily
briefing" was generated from seven-month-old CES coverage because age was one input to
a score rather than a hard filter.

**Verify claims, not sentences.** Per-sentence `SUPPORTED`/`UNSUPPORTED` conflates
"asserts something false" with "isn't in the bullet list". Framing and judgment are
never entailed by a fact list, so it makes a voice structurally impossible.

**A pronoun subject is claim-bearing until resolved.** `"That is a research demo"`
reads as framing but asserted a fact contradicting the prior sentence, which said "now
in full production". Pass the prior sentence and resolve before classifying. Judgment
must survive resolution: `"This is the part that should worry NVIDIA"` passes.

**Strip vendor marketing during extraction.** NVIDIA's coinage "extreme-codesigned"
was promoted to a verified fact and built into a script. A claim that is only an
adjective with no number behind it gets dropped.

**Filter site chrome from scraped images aggressively.** `OpenAI_Presence_1x1.png` and
`Frame.png` passed a substring filter into a rendered video. Reject anything under 200px
on either axis; where markup omits dimensions, fetch headers rather than trust it.

**Never estimate word timing from character counts.** Real per-word timings come from
the ElevenLabs alignment and are in the payload.

**The alignment times audio tags as if they were spoken.** `[pause]` comes back as
seven aligned characters occupying 1.6s of real audio — the pause being performed. The
elapsed time is genuine and must stay in the timeline, but those characters are not
words, so map `tts_text` positions to `script_text` positions by walking both strings.
Arithmetic on tag lengths drifts, because stripping a tag also collapses a space.

**Pass `response_format` through unchanged.** Rewriting
`{"type": "json_schema", ...}` into `{"type": "json_object", ...}` means "any valid
JSON" and silently drops schema enforcement — `finish_reason` still reads `stop`, so the
request looks healthy and only downstream validation catches the violation. A test
asserting an enum field is respected is the only way to know enforcement is live.

**`finish_reason == "length"` means truncated, invalid JSON.** Detect it explicitly and
raise. Do not attempt to parse.

**Reasoning tokens are billed as output and dwarf the answer.** Kimi at
`reasoning_effort="low"` billed 8,376 output tokens to return 250 tokens of JSON — 95%
invisible, since `reasoning_content` comes back empty. Measure `"none"` per purpose
rather than assuming: it produced byte-comparable drafts at 1/27th the cost, but
reordered the ranking, so `rank` keeps its reasoning and `draft` does not. GLM and
DeepSeek accept the same efforts; `"minimal"` is rejected by all three.

**Higher reasoning effort made the checker worse, not just dearer.** GLM at `high` spent
all 32,768 output tokens deliberating one judgment claim and truncated mid-JSON; at
`medium` it returned identical verdicts in ~340 tokens. Entailment is bounded — extra
reasoning only finds more to say. Nor is a "trivial" call a small one: rank truncated a
4096-token ceiling at 34 candidates, because reasoning is spent before the first output
token. Size `MAX_TOKENS` for the reasoning, not the answer.

**Deterministic checks run in Python, never through a model.** Quote matching is a
substring search, anchor arithmetic is division, date filtering is a comparison. These
are the only checks immune to shared model priors — even with three families in play, a
deterministic check is the only one that cannot be talked out of a verdict.

**A worked example teaches its instances, not its technique.** One example closer
("4,722 got nothing") came back reworded in 24 of 24 drafts and pushed variant overlap
70% → 85%. Show the SHAPE with contrasting cases and say outright that the instance is
not the move.

**A multi-sentence quote loses its length exemption.** The splitter halves the quote
before `rhythm_problems` looks for it whole, so nothing matched and every story with a
two-sentence quote was undraftable. Match a substantial contiguous run — and check the
quote BEFORE pacing, or a trimmed quote reports as "over 20 words" and the writer trims
it further.

**"Start over" is the wrong retry once gates are independent.** With five gates at ~60%
clean per draft, three attempts should fail ~6% of the time; every story failed instead,
each attempt dying on a DIFFERENT gate, because the retry discarded writing that had
already passed. Show the writer its own draft, name the one defect, keep the rest — and
repair a self-contained field (the caption) in place rather than spending a draft on it.

**The erosion floor asks for the opening BEAT, not the lede SLOT.** A hook is routinely
built from a number ("5,000 labs asked Washington for AI money"), so a fully verified
script with 0 refuted claims was failed for "no lede". Once beats carry a kind, every
structural question is about kinds; slots only say where the material came from.

**A gate overrun by exactly one word is a gate set too low.** The 8-word caption cap
was the largest single cause of draft failure across a 15-draft bench, and every
overrun was nine words. Discarding a good script for the ninth buys no legibility.

**A verbatim run is only passthrough if the writer could have said it differently.**
"38.3% on the ARC-AGI-3 public set" is 8 tokens with no paraphrase — six are a figure or
the benchmark's name — and counting raw tokens rejected three redrafts running, making
the story undraftable. Score the run by CONTENT words: not stopwords, not figures, not
names. `[A-Z][a-zA-Z]+` also misses `ARC-AGI-3`, so collect hyphenated identifiers too.
The caption gate must NOT inherit this: it asks whether the same text appears twice,
where a repeated run of names and figures is exactly the duplication it exists to catch.

**A caption is written short, never cut short.** Trimming an over-long caption to the
word ceiling passes every check and ships "How OpenAI got 38% from a model that scored"
on screen for the whole video. Cutting at a clause boundary instead fails whenever the
first N words contain no boundary, which is the common case. Reject the draft and name
the failure in the retry prompt — the overrun is always a tacked-on trailing qualifier.

**Numbered ids do not sort like timestamped ones.** Run directories and bench output
were UTC timestamps, where string order happened to equal creation order — `latest()` is
just the last element of a sort. `run10` sorts before `run2` as a string, so the moment a
tenth run existed, `--resume` and `--render` would have silently targeted the wrong
directory. Sort on the parsed integer, and allocate the next id from a numeric max.

**Redirected logs buffer, so a running pipeline looks hung.** `python run.py > log` in
the background wrote ZERO bytes for minutes — logging's StreamHandler wraps stderr, which
is block-buffered off a terminal. A full run takes minutes and its log is the only sign
it is alive; reconfigure the handler stream to line-buffered at startup.

**Nothing loaded `.env`.** `llm.py` read `os.environ` and said "add it to .env"; no
module ever opened that file, so live runs worked only when the key happened to be
exported. Reading it is filesystem access, so it lives in `runs.py`, and a real
environment variable always wins over the file.

---

## Conventions that differ from defaults

**`WORDS_PER_SECOND = 2.1`** — measured across three real narrations, not guessed. The
obvious 2.5 was consistently 20% optimistic for this voice.

**Script length is dynamic, driven by beats.** Each populated report slot is at most
one beat; one sentence per beat; cover once and stop. A three-beat story is a
three-sentence script and that is a correct result. Hard cap `MAX_SECONDS = 110`. The
writer is never shown a target duration — `estimated_seconds` is a measurement.

**The first line is a hook, not a lede; the last is a closer.** A lede ANSWERS, a hook
opens a gap — test it by asking whether the line could CLOSE the script, and rewrite it
if it could. "Washington put $5 billion into AI research" is inert next to "5,000 labs
asked Washington for AI money". The closer bookends that gap, is under 10 words, and is
OMITTED when the notes cannot support one. It may not be required to beat every other
sentence strictly: next to a 3-word funnel beat that demands a 2-word closer.

**Two numbers sharing a unit run larger-first when one is a subset.** "278 selected.
Over 5,000 applied" throws the attrition away by giving the outcome before the pool.

**Beats carry a `kind`, not just a `slot`.** `funnel` and `proof` are both slot
`number`, so neither is inferable — a null kind leaves nothing downstream able to tell
a paired-number beat from a plain one.

**The caption is claim-bearing and goes through `check` like a sentence.** On screen for
the whole video and screenshottable, so an unverified one is worse than an unverified
spoken line. Assert it reaches neither `script_text` nor `tts_text`, and shares no
4-word run with the first sentence — otherwise it just repeats the subtitles.

**Subjectivity is permitted in exactly three places:** which fact leads, what gets
framed as the catch, and sentence rhythm. Never about the world.

**Two text fields, always.** `script_text` is clean and is the source of truth for
subtitles, checking, and visual matching. `tts_text` carries ElevenLabs v3 audio tags.
Stripping bracketed spans from `tts_text` must yield `script_text` exactly — assert it.

**Models are chosen per purpose, never per call site.** All on Fireworks,
OpenAI-compatible. `config.MODELS` maps a purpose to a (model, reasoning effort) pair:
`rank` and `draft` on Kimi K2.6, `report` on DeepSeek V4 Pro, `check` on GLM 5.2.
Stages pass a purpose string to `llm.json_call` and never a model name. Fireworks is
paid serverless with no free-tier RPM ceiling, so there is no request spacing; keep
429/5xx backoff only.

**The three families are load-bearing, not incidental.** `check` judges `draft`'s output
and re-queries the source when a claim fails, so it must share priors with neither. Do
not consolidate onto one model to save money — the separation is the verification.

**Schema enforcement is per-model.** The enum test runs against all three. Never assume
a `response_format` behaviour observed on one model holds on another.

**`draft`'s model is a judgment call, not a measured one.** GPQA and coding benchmarks
do not predict whether a 25-second script sounds human. Settle it with `--draft-model`
and bench output, not an index.

**LLM cost is ~$0.02–0.03 per run against ~$0.13 of narration.** Anything multiplying
calls stays on the cheaper model unless measured otherwise. GLM is the priciest output
in the set, so keep the check schema tight.

**Narration is ~97% of marginal cost** at ~one credit per character; everything else is
rounding. Cache on a hash of the exact input; never synthesize a script that failed
checking.

**Remotion `<Sequence>` resets `useCurrentFrame()` to zero.** Word timings are absolute
seconds from the audio start, so subtitles inside a sequence must subtract the
sequence's own start or every word lights up early by its offset.

**Scraping source images was a strategy for a source list we no longer have.** It was
designed for Hugging Face posts carrying diagrams and benchmark charts; Ars Technica and
The Verge carry ONE decorative hero image, and after de-duplication and headshot
filtering a story often has none. The fix was not an image API — it was to render the
numbers, ratios and quote already sitting in report.json. Every treatment must work with
zero images, because zero is the common case, not the fallback.

**A byline portrait is identified by its UPLOAD YEAR, not its filename or shape.** The
headshot that reached a rendered frame was `Dang.jpg` at 600x400 — no telltale name, not
square, past the 200px floor. What gave it away: `/uploads/2018/` on a story published in
2026. A CMS stores a staff photo once and reuses it for years; a story's own art is
uploaded with the story.

**Animate in FRAMES, not in fractions of the beat.** A funnel drop scheduled over 50-90%
of its beat never finished, because "278 got it." runs 2.5 seconds — the attrition, the
entire point of the treatment, was never on screen. The same fractions stretch a quote
reveal across ten seconds on a long beat. Start at a fixed frame, cap at a proportion.

**Beat kinds are indexed per BEAT; the renderer consumes them per LINE.** A beat may
hold two sentences and narrate splits on sentences, so lines outnumber beats — every
treatment after the multi-sentence beat shifted by one, putting the closer treatment on a
caveat and dropping the real closer to a number card. Match each line back to its beat.

**A chart must check that its two numbers share a unit.** Taking the two largest figures
in the sentence drew "$100,000" against "60 hours" as proportional bars — money against
time, a relationship the script never claimed. `draft.py` defines a funnel as two numbers
SHARING a unit where one counts a subset of the other; the renderer must apply the same
test or it invents the comparison.

**A ratio can be carried by a verb with no second number.** "The attack halved its key
strength" is a funnel whose numbers are absent; falling through to a number card rendered
the word "strength." at display size. Derive the implied pair, or fall back to a
proportional before/after — the bars carry the ratio, the labels carry the words.

**A persistent overlay must clear the badge at its WRAPPED height.** The caption sat on
top of the source badge whenever the headline wrapped to two lines; a fixed offset
computed from the one-line case is wrong for the story that actually needs the room.

**`height: auto` on a wide image does not fill a vertical frame.** A 16:9 photo covered
~56% of the 9:16 frame with black below it, and `objectFit: cover` cannot crop without
a constrained height. Set BOTH axes and move the framing to `objectPosition`.

**Size display type to the frame, not a constant.** A fixed 150px key phrase fit "278"
and ran "breakthroughs" off both edges; long subtitles then grew up into it.

**`REFERENCE_SCALES` is maintained by hand** in `config.py`, each value with a `source`
and `as_of`. An anchor computed from a model-supplied reference is a hallucination
wearing a division sign.

---

## Out of scope

Do not add these. Each was considered and rejected for this build.

- Orchestration frameworks (Airflow, Prefect), LangChain, LlamaIndex
- Vector databases — this is daily news, not retrieval
- Docker for local development; image or video generation APIs
- A stock B-roll library — the fallback is typographic
- Any human-in-the-loop recording path; a script is just text
- Multi-story videos — one story per video, `STORIES_PER_VIDEO` is not a thing here

---

## Working agreements

- **This file is context, not enforcement.** The layer rules are enforced by
  `tests/test_architecture.py`, which AST-walks the source. If a boundary matters, it
  belongs in that test as well as here.
- **This file does not update itself.** Add a line when a pitfall is discovered — the
  concrete failure, not a general principle. Directory layouts and dependency lists go
  in README.md; they go stale here.
- **The budget is ~45 entries, not 200 lines.** The line cap was the proxy; the real
  constraint is how many distinct failures a reader can hold. Entries are already about
  six lines each, so squeezing prose buys nothing — when this file is over budget the
  question is which pitfall has been ENGINEERED AWAY (a gate now enforces it, a test now
  catches it) and can therefore be deleted, not which paragraph can be shortened. Delete
  those; never delete a live failure to hit a number.
- Every module gets a `__main__` block that exercises it standalone. Pin dependencies.
- `run.py --selftest` runs the pipeline against committed fixtures with all model calls
  stubbed. Use it to verify changes without spending money.
- Prefer failing loudly to degrading quietly. No fresh story, a script that fails
  checking, or a run with no audio are all real outcomes and should look like errors.
- README.md is generated last from what the code actually does, not from what it is
  planned to do.