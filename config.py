"""Every constant in the pipeline. No magic numbers anywhere else.

Values with a comment saying "measured" were measured; do not round them back
to the obvious guess.
"""

# --- Story selection -------------------------------------------------------

MAX_STORY_AGE_HOURS = 72  # a gate, not a score: filter by age BEFORE ranking

# --- Script shape ----------------------------------------------------------

MIN_SECONDS = 10
MAX_SECONDS = 110  # hard cap on video length
SECONDS_PER_BEAT = 5  # estimation only, never a target shown to the writer
MAX_BEATS = MAX_SECONDS // SECONDS_PER_BEAT
WORDS_PER_SECOND = 2.1  # measured across three real narrations; 2.5 is 20% optimistic
MAX_DRAFT_ATTEMPTS = 3

# --- Candidate finding -----------------------------------------------------

HN_TOP_STORIES = 60  # how deep into /v0/topstories.json to look
HN_FETCH_TIMEOUT_SECONDS = 10
HN_FETCH_THREADS = 8
# signal_score = (COMMENT_W * descendants + POINT_W * points) / (age_h + OFFSET).
# Comments weigh above points: comments proxy for "people are arguing about this".
HN_COMMENT_WEIGHT = 2.0
HN_POINT_WEIGHT = 1.0
HN_AGE_OFFSET_HOURS = 2.0  # damps the near-zero-age spike for brand-new posts

SUMMARY_MAX_CHARS = 1000  # stored per candidate, HTML stripped
RANK_SUMMARY_CHARS = 400  # how much of the summary the ranking model sees
RANK_MAX_SCORE = 10

# --- Reporting (article fetch + slot extraction) ---------------------------

FETCH_TIMEOUT_SECONDS = 20
HTTP_USER_AGENT = "Mozilla/5.0 (compatible; HyperFeed/0.1)"
MIN_ARTICLE_CHARS = 400  # less than this means extraction failed, not a short article

MIN_IMAGE_DIM_PX = 200  # reject anything smaller on either axis
# Site-chrome filename patterns, matched against the lowercased URL path.
IMAGE_REJECT_PATTERNS = ("presence", "frame", "og-image", "social", "thumbnail", "_1x1")
MAX_IMAGE_DIM_FETCHES = 12  # budget for fetching image bytes when markup omits dims
IMAGE_DIM_FETCH_BYTES = 32768  # enough for PNG/GIF/WebP headers and JPEG SOF

REPORT_SLOTS = ("lede", "so_what", "numbers", "scale_anchor", "quote", "tension")

# --- Drafting --------------------------------------------------------------

# ElevenLabs v3 audio tags permitted in tts_text. Anything bracketed that is
# not on this list is stripped: stripping bracketed spans from tts_text must
# yield script_text exactly.
AUDIO_TAGS = ("[pause]", "[short pause]", "[excited]", "[curious]",
              "[serious]", "[dry]", "[emphatic]", "[thoughtful]")
MAX_TAGS_PER_SENTENCE = 1

# Ceiling on proof beats (numbers, quote, anchor) between the lede/so_what and
# the catch. The report ranks its numbers, so the writer should take the ones
# that earn a place and stop; without a ceiling it recites the whole list and
# a six-number report became an eight-beat lab-notebook recital.
MAX_PROOF_BEATS = 3

# There is no hook pool. A canned opener chosen before the story is known
# cannot tease it, and "Here's what's new." is dead air in short-form. The
# first sentence IS the lede — the most surprising concrete fact, under 12
# words — so it varies by construction, which defeats templated-opening
# detection better than rotating ten fixed strings ever did.

# Drafting is a writing task: at 0.7 two variants of one story came back
# byte-identical, which means the constraints left exactly one output.
DRAFT_TEMPERATURE = 0.9

# Attempts at the deterministic prose gates before giving up. Failing twice
# means the prompt has stopped framing the task generatively — worth seeing,
# not worth papering over.
MAX_DRAFT_ATTEMPTS_PROSE = 2

# Wire-copy constructions the writer must not produce. Each entry is a regex
# checked against script_text, with a human-readable label for the failure.
BANNED_PATTERNS = (
    (r"\b[A-Z][\w.&-]*(?:\s+[A-Z][\w.&-]*)?\s+(?:announced|unveiled|released|launched)\b",
     "company name followed by announced/unveiled/released/launched"),
    (r"\bdesigned to\b", '"designed to"'),
    (r"\blet's dive in\b", '"let\'s dive in"'),
    # "like" and "share" are ordinary words — "reads like a legislative speech"
    # is not a call to action. Match them only in imperative position.
    (r"(?:^|[.!?]\s+)(?:please\s+)?(?:like|share|subscribe)\b"
     r"|\b(?:like,? comment,?|and subscribe|hit (?:that )?(?:like|subscribe)"
     r"|follow (?:us|me)\b|comment below|link in bio)",
     "call to action"),
    (r"\b(?:thanks for watching|see you tomorrow|that's all for today|"
     r"stay tuned|until next time)\b", "sign-off"),
    (r",\s*quote,|\bunquote\b", '"quote"/"unquote" spoken as narration'),
)

# Superlatives are permitted ONLY inside a quotation attributed to a named
# speaker; checked outside quoted spans.
#
# "cutting-edge" and "state-of-the-art" are deliberately absent: in this niche
# they are terms of art, not marketing. "omitted cutting-edge training on
# cybersecurity tasks" is the article's own characterisation carried through
# the tension slot, and flagging it trained the eye to ignore the flag. The
# marketing-stripping that matters happens at extraction, where the claim is
# still next to its source.
SUPERLATIVE_PATTERN = (
    r"\b(?:best|worst|biggest|largest|smallest|fastest|slowest|greatest|"
    r"first-ever|unprecedented|revolutionary|groundbreaking|industry-leading|"
    r"game-changing|world-class)\b")

# --- Models ----------------------------------------------------------------

# An OpenAI-compatible endpoint. Swapping to a self-hosted vLLM or SGLang
# deployment must be a change to this URL and the slugs below, and nothing
# else — llm.py is built around base_url + model + key, never around one
# vendor.
BASE_URL = "https://api.fireworks.ai/inference/v1"
API_KEY_ENV = "FIREWORKS_API_KEY"

# Slugs verified against the live model list on 2026-07-29. The catalogue
# spells versions with 'p' for the decimal point: Kimi K2.6 is kimi-k2p6.
KIMI = "accounts/fireworks/models/kimi-k2p6"
DEEPSEEK_PRO = "accounts/fireworks/models/deepseek-v4-pro"
GLM = "accounts/fireworks/models/glm-5p2"
# Candidate writers, priced so `bench.py --draft-model` reports real spend
# rather than a silent $0.
KIMI_K3 = "accounts/fireworks/models/kimi-k3"
DEEPSEEK_FLASH = "accounts/fireworks/models/deepseek-v4-flash"

# purpose -> (model, reasoning_effort). Three families, deliberately: check
# judges draft's output and re-queries report's extraction, so it must not
# share priors with either.
# Reasoning tokens are billed as output and are NOT free: at effort "low" the
# draft call billed 8,376 output tokens to return 250 tokens of JSON. Measured
# per purpose — "none" produced identical drafts at 1/27th the cost, but
# reordered the rank results, so rank keeps its reasoning.
MODELS = {
    "rank": (KIMI, "low"),             # judgment measurably changes the ordering
    "report": (DEEPSEEK_PRO, "high"),  # 1M context, cached-input discount, judgment
    "draft": (KIMI, "none"),           # prose from ranked notes; reasoning bought nothing
    # "high" made GLM spend all 32,768 output tokens deliberating a single
    # judgment claim and truncate mid-JSON. "medium" returns identical
    # verdicts in ~340 tokens — entailment is a bounded question and extra
    # reasoning finds nothing, it only finds more to say.
    "check": (GLM, "medium"),          # highest intelligence, a third family
}

# USD per million tokens. Maintained by hand like REFERENCE_SCALES: a price
# guessed by a model is not a price.
PRICES = {
    KIMI: {"input": 0.60, "output": 2.50, "cached_input": 0.06,
           "as_of": "2026-07-29", "source": "Fireworks published pricing page"},
    DEEPSEEK_PRO: {"input": 1.25, "output": 5.00, "cached_input": 0.10,
                   "as_of": "2026-07-29", "source": "Fireworks published pricing page"},
    GLM: {"input": 0.60, "output": 2.20, "cached_input": 0.06,
          "as_of": "2026-07-29", "source": "Fireworks published pricing page"},
    KIMI_K3: {"input": 0.60, "output": 2.50, "cached_input": 0.06,
              "as_of": "2026-07-30", "source": "Fireworks published pricing page"},
    DEEPSEEK_FLASH: {"input": 0.27, "output": 1.10, "cached_input": 0.03,
                     "as_of": "2026-07-30", "source": "Fireworks published pricing page"},
}

DEFAULT_TEMPERATURE = 0.2
# Reasoning tokens count against this budget and are billed as output. "rank"
# looks trivial but scales with candidate count — 34 fresh candidates
# truncated a 4096-token ceiling mid-JSON — so every ceiling here is generous.
MAX_TOKENS = {"rank": 32768, "report": 32768, "draft": 16384, "check": 65536}
DEFAULT_MAX_TOKENS = 16384

LLM_MAX_RETRIES = 5
LLM_BACKOFF_BASE_SECONDS = 2.0
LLM_BACKOFF_CAP_SECONDS = 60.0

# --- Narration -------------------------------------------------------------
# ~97% of marginal cost at roughly one credit per character. A 110-second
# script costs about 4x a short one, so caching is not an optimisation here.

VOICE_NAME = "Liam - Energetic, Social Media Creator"
VOICE_ID_ENV = "ELEVENLABS_VOICE_ID"
ELEVENLABS_KEY_ENV = "ELEVENLABS_API_KEY"
TTS_MODEL_ID = "eleven_v3"
TTS_OUTPUT_FORMAT = "mp3_44100_128"
# v3 settings, read from the SDK's VoiceSettings type — not a v2 shape.
TTS_VOICE_SETTINGS = {
    "stability": 0.5,        # v3 is less stable at the extremes
    "similarity_boost": 0.75,
    "style": 0.0,
    "use_speaker_boost": True,
    "speed": 1.0,
}
CREDITS_PER_CHARACTER = 1.0
AUDIO_DIR = "audio"  # cache, keyed by hash of (text, voice, model, settings)

# --- Rendering -------------------------------------------------------------

RENDERER_DIR = "renderer"
RENDERER_PUBLIC_DIR = "renderer/public"
RENDERER_PROPS_FILE = "renderer/props.json"
COMPOSITION_ID = "NewsVideo"
VIDEO_FILENAME = "video.mp4"
RENDER_TIMEOUT_SECONDS = 1800
# Duration = real audio length + this. Never estimated_seconds.
VIDEO_TAIL_SECONDS = 0.6

# --- Bench -----------------------------------------------------------------

BENCH_DIR = "data/bench"
BENCH_MAX_SPEND_USD = 0.10

# --- Files and directories (relative to the project root) ------------------

RUNS_DIR = "runs"
COSTS_FILE = "costs.jsonl"
LEDGER_FILE = "ledger.json"
SOURCES_FILE = "sources.json"
RUN_TIMESTAMP_FORMAT = "%Y%m%dT%H%M%SZ"  # UTC; lexicographic order == time order

# --- Ledger ----------------------------------------------------------------

RELATED_TITLE_JACCARD = 0.5  # token-set overlap at or above this counts as related

# --- Reference scales ------------------------------------------------------
# Human-scale anchors for making big numbers legible. Maintained BY HAND: a
# scale anchor computed from a model-supplied reference is a hallucination
# wearing a division sign. Every entry must carry `source` and `as_of`.

REFERENCE_SCALES = {
    "midrange_phone_price_usd": {
        "value": 500,
        "unit": "USD",
        "source": "Launch price of a Pixel 9a-class mid-range phone",
        "as_of": "2026-07",
    },
    "average_us_salary_usd": {
        "value": 66600,
        "unit": "USD/year",
        "source": "US Social Security Administration national average wage index, 2024",
        "as_of": "2026-07",
    },
    "midrange_gpu_price_usd": {
        "value": 550,
        "unit": "USD",
        "source": "NVIDIA RTX 5070 MSRP at launch",
        "as_of": "2026-07",
    },
}


if __name__ == "__main__":
    print(f"MAX_STORY_AGE_HOURS = {MAX_STORY_AGE_HOURS}")
    print(f"MIN_SECONDS..MAX_SECONDS = {MIN_SECONDS}..{MAX_SECONDS}")
    print(f"MAX_BEATS = {MAX_BEATS} ({SECONDS_PER_BEAT}s per beat)")
    print(f"WORDS_PER_SECOND = {WORDS_PER_SECOND}")
    print(f"base_url: {BASE_URL}")
    for _purpose, (_model, _effort) in MODELS.items():
        assert _model in PRICES, f"no price for {_model}"
        print(f"  {_purpose:7} -> {_model.split('/')[-1]:16} effort={_effort}")
    for key, scale in REFERENCE_SCALES.items():
        missing = [f for f in ("value", "unit", "source", "as_of") if f not in scale]
        assert not missing, f"REFERENCE_SCALES[{key!r}] missing {missing}"
        print(f"scale {key}: {scale['value']} {scale['unit']} "
              f"({scale['source']}, as of {scale['as_of']})")
    print("config OK")
