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

# --- Models ----------------------------------------------------------------

CHEAP_MODEL = "gemini-3.5-flash-lite"
STRONG_MODEL = "gemini-3.6"
MODEL_RPM = {
    CHEAP_MODEL: 15,
    STRONG_MODEL: 5,
}
DEFAULT_TEMPERATURE = 0.2
LLM_MAX_RETRIES = 5  # attempts on 429 before giving up
LLM_BACKOFF_BASE_SECONDS = 2.0
LLM_BACKOFF_CAP_SECONDS = 60.0

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
    print(f"models: cheap={CHEAP_MODEL} ({MODEL_RPM[CHEAP_MODEL]} RPM), "
          f"strong={STRONG_MODEL} ({MODEL_RPM[STRONG_MODEL]} RPM)")
    for key, scale in REFERENCE_SCALES.items():
        missing = [f for f in ("value", "unit", "source", "as_of") if f not in scale]
        assert not missing, f"REFERENCE_SCALES[{key!r}] missing {missing}"
        print(f"scale {key}: {scale['value']} {scale['unit']} "
              f"({scale['source']}, as of {scale['as_of']})")
    print("config OK")
