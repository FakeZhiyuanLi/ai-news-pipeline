"""Deterministic prose checks: is this copy, or is it the notes read aloud?

"Rewrite in your own words" is unenforceable as an instruction, so it is
verified here the way quotes are — in Python, on the text that came back.

Both gates are backstops. If they fire constantly the prompt is not framing
the task generatively, and the fix is the prompt, not the threshold.
"""

import re
import statistics

# Reject at this many consecutive words shared with the notes. Deliberately
# generous: a script legitimately reuses a technical phrase, and this should
# catch passthrough, not paraphrase.
MAX_VERBATIM_RUN = 8

# A run must ALSO carry this many words the writer could have chosen
# differently — not stopwords, not figures, not names. Measured against the
# real failure: "38.3% on the ARC-AGI-3 public set" is 8 tokens with 2
# authorial words and no paraphrase, while genuine passthrough runs 8+ words
# of ordinary prose. 5 rejects the notes read aloud and passes an unavoidable
# identifier.
MIN_CONTENT_RUN = 5

MAX_SENTENCE_WORDS = 20
SHORT_SENTENCE_WORDS = 8   # at least one sentence must come in under this

# Variation is measured as spread, not as distance from the mean. The
# "everything within 5 words of the mean" formulation rejected [6, 13, 4, 10]
# — a 9-word range with a 4-word line, which reads fine — while passing the
# 29-word passthrough it was written to catch. Real copy measured sd >= 3.0
# and a range >= 7; genuinely uniform output is sd 0.
MIN_LENGTH_STDEV = 2.5
MIN_LENGTH_RANGE = 6


_SMART_PUNCTUATION = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"',
                                    "–": "-", "—": "-", " ": " "})


def unify_punctuation(text: str) -> str:
    """Curly quotes to straight. Publishers type ’ and writers type ' — a
    verbatim comparison that does not account for it demands text no writer
    would produce."""
    return text.translate(_SMART_PUNCTUATION)


def quote_matches(quote: str, script: str) -> bool:
    """Is the quote reproduced in the script, allowing only for typography
    and trailing punctuation? This is the same check checks/quotes.py will
    apply, so drafting must not pass anything it would fail."""
    needle = " ".join(normalise(quote))
    hay = " ".join(normalise(script))
    return bool(needle) and needle in hay


def normalise(text: str) -> list[str]:
    """Words for comparison. Interior periods survive ("3.5", "torch.compile")
    but a trailing one does not, so "package" and "package." are one word."""
    words = re.sub(r"[^a-z0-9%$. ]", " ", unify_punctuation(text).lower()).split()
    return [w.strip(".") or w for w in words]


def _is_inert(words: list[str]) -> bool:
    """A run made only of names, numbers and units is not passthrough — there
    is no other way to say "$5 billion" or "Claes de Vreese"."""
    return all(re.fullmatch(r"[\d.,%$]+|[a-z]{1,3}", w) or w in _NAMEY
               for w in words)


_NAMEY: set[str] = set()

# Function words only. These carry no authorial choice, so a run made of
# figures, names and these is unavoidable rather than copied. Deliberately
# short: adding content words ("scored", "reduced", "tripled") would start
# excusing real passthrough, which is the whole thing this gate catches.
_STOPWORDS = frozenset("""
a an the of on in at to for from with by and or but its it is was were be been
that this these those as into over under after before than then so
""".split())


def longest_shared_run(sentence: str, notes: str, *,
                       proper_nouns: set[str] | None = None,
                       min_content: int = 0) -> tuple[int, str]:
    """Longest run of consecutive words shared between a sentence and the
    notes. Returns (length, the shared text).

    min_content skips runs carrying fewer than that many AUTHORIAL words —
    words that are not stopwords, figures or names. Only the passthrough gate
    wants this: it asks "did the writer copy the notes", and a benchmark's
    name and score have no paraphrase. The caption check asks the opposite
    question — "is this the same text twice" — where a repeated run of names
    and figures is exactly the duplication it must catch, so it leaves this
    at 0.
    """
    a, b = normalise(sentence), normalise(notes)
    if not a or not b:
        return 0, ""
    names = {n.lower() for n in (proper_nouns or set())}
    best, best_text = 0, ""
    # positions in b for each word, so we only extend from real matches
    index: dict[str, list[int]] = {}
    for j, word in enumerate(b):
        index.setdefault(word, []).append(j)
    for i in range(len(a)):
        for j in index.get(a[i], ()):
            k = 0
            while i + k < len(a) and j + k < len(b) and a[i + k] == b[j + k]:
                k += 1
            if k > best:
                run = a[i:i + k]
                # A run of only numbers, units and proper nouns is unavoidable.
                #
                # Stopwords do not count toward "avoidable" either. "38.3% on
                # the ARC-AGI-3 public set" is 8 words and has no paraphrase —
                # every content word is a figure or a product name, and the
                # only other words are `on`, `the`, `public`, `set`. Requiring
                # the run to be ENTIRELY names failed it and made the story
                # undraftable. Judge on the words the writer could have chosen
                # differently.
                content = [w for w in run if w not in _STOPWORDS
                           and not re.fullmatch(r"[\d.,%$]+", w)
                           and w not in names]
                # Score the run by the words the writer COULD have chosen
                # differently. "38.3% on the ARC-AGI-3 public set" is 8 tokens
                # but only `public` and `set` are authorial — there is no
                # paraphrase of a benchmark's name and score, and counting raw
                # tokens made that story undraftable across three redrafts.
                # An 8-word run of ordinary prose still has 8 content words.
                if len(content) < min_content:
                    continue
                best, best_text = k, " ".join(run)
    return best, best_text


def find_passthrough(sentences: list[str], notes: str, *,
                     exempt: list[str] | None = None,
                     proper_nouns: set[str] | None = None,
                     limit: int = MAX_VERBATIM_RUN) -> list[dict]:
    """Sentences that reuse `limit`+ consecutive words from the notes.

    exempt holds text that MUST be verbatim — the quote slot. A sentence
    carrying an exempt span has that span removed before comparison, so a
    correctly quoted sentence is judged on its own words only.
    """
    findings = []
    for sentence in sentences:
        stripped = sentence
        for span in (exempt or []):
            if span:
                stripped = _remove_span(stripped, span)
        run, text = longest_shared_run(stripped, notes, proper_nouns=proper_nouns,
                                       min_content=MIN_CONTENT_RUN)
        if run >= limit:
            findings.append({"sentence": sentence, "run_words": run, "shared": text})
    return findings


def _remove_span(sentence: str, span: str) -> str:
    """Remove an exempt span (a verbatim quote) from a sentence, tolerating
    the requoting and punctuation the writer may add around it."""
    needle = normalise(span)
    hay = normalise(sentence)
    if not needle:
        return sentence
    for i in range(len(hay) - len(needle) + 1):
        if hay[i:i + len(needle)] == needle:
            return " ".join(hay[:i] + hay[i + len(needle):])
    # Not found whole: fall back to dropping the words it contains.
    return " ".join(w for w in hay if w not in set(needle))


def rhythm_problems(sentences: list[str], *,
                    exempt_long: list[str] | None = None) -> list[str]:
    """Uniform pacing is the strongest tell that text is machine-generated —
    more than word choice is. Returns human-readable problems, empty if the
    rhythm is fine.

    exempt_long holds text that cannot be shortened — the verbatim quote. A
    34-word quotation is not a pacing failure; requiring it under the ceiling
    would make every story with a long quote unwritable.
    """
    lengths = [len(s.split()) for s in sentences]
    if not lengths:
        return ["no sentences"]

    def carries_exempt(sentence: str) -> bool:
        """Does this sentence carry quoted material?

        A quote of more than one sentence is SPLIT by the sentence splitter
        before it gets here, so looking for the whole span inside one sentence
        never matches and the exemption silently disappears — which made every
        story with a two-sentence quote structurally undraftable. Match a
        substantial contiguous run of the quote instead of all of it.
        """
        hay = normalise(sentence)
        if not hay:
            return False
        for span in (exempt_long or []):
            needle = normalise(span)
            if not needle:
                continue
            # The whole quote, when it fits in one sentence.
            if any(hay[i:i + len(needle)] == needle
                   for i in range(len(hay) - len(needle) + 1)):
                return True
            # Otherwise: most of THIS sentence is words taken from the quote.
            # A passing attribution phrase shares a word or two; a sentence
            # carrying half a quotation shares nearly all of them.
            run, _ = longest_shared_run(sentence, span)
            if run >= max(MAX_VERBATIM_RUN, len(hay) * 0.6):
                return True
        return False

    problems = []
    long_ones = [(n, s) for n, s in zip(lengths, sentences)
                 if n > MAX_SENTENCE_WORDS and not carries_exempt(s)]
    if long_ones:
        problems.append(
            f"{len(long_ones)} sentence(s) over {MAX_SENTENCE_WORDS} words: "
            + "; ".join(f"{n}w \"{s[:60]}...\"" for n, s in long_ones[:3]))
    # Judge pacing on the sentences the writer controls; a verbatim quote is
    # given, not written, and skews both the mean and the spread.
    own = [n for n, s in zip(lengths, sentences) if not carries_exempt(s)] or lengths
    if len(own) > 1:
        # "under 8" means 7 or fewer; a script of all-8s was being failed for
        # a sentence that satisfies the stated rule.
        if min(own) > SHORT_SENTENCE_WORDS:
            problems.append(
                f"no sentence under {SHORT_SENTENCE_WORDS} words "
                f"(shortest is {min(own)}) — a short line after a long one is "
                "the cheapest way to sound human")
        # Uniformity is only audible when the sentences are long enough to
        # have a cadence to repeat. [8, 4, 4, 6, 4, 5] is a run of short,
        # punchy lines — the goal, not the fault — and rejecting it sends the
        # writer back to pad. Judge only when the copy is running long.
        spread = statistics.pstdev(own)
        if (sum(own) / len(own) > SHORT_SENTENCE_WORDS
                and spread < MIN_LENGTH_STDEV
                and (max(own) - min(own)) < MIN_LENGTH_RANGE):
            problems.append(
                f"sentence lengths barely vary ({own}, spread {spread:.1f}) — "
                "uniform pacing is the strongest tell that a machine wrote it")
    return problems


if __name__ == "__main__":
    notes = ("The Trump administration allocated $5 billion to AI-focused research "
             "grants while proposing a policy plan that shifts federal science "
             "funding away from universities toward individual researchers and "
             "private companies.")

    passthrough = ("The Trump administration allocated $5 billion to AI-focused "
                   "research grants while proposing a policy plan that shifts "
                   "federal science funding away from universities.")
    run, shared = longest_shared_run(passthrough, notes)
    print(f"passthrough sentence: {run}-word shared run")
    assert run >= MAX_VERBATIM_RUN

    written = "Washington just put $5 billion into AI research."
    run, shared = longest_shared_run(written, notes)
    print(f"written sentence:     {run}-word shared run ({shared!r})")
    assert run < MAX_VERBATIM_RUN, shared

    # A verbatim quote must not count as passthrough.
    quote = "You cannot use venture capital to force breakthroughs while selling the patent"
    quoted_notes = notes + " " + quote
    sentence = f'AI professor Claes de Vreese: "{quote}."'
    assert find_passthrough([sentence], quoted_notes, exempt=[quote]) == []
    assert find_passthrough([sentence], quoted_notes) != []
    print("verbatim quote exempted correctly")

    good = ["Washington just put $5 billion into AI research.", "5,000 labs applied.",
            "278 got it.", "And the money skips universities entirely.",
            "Science, funded like a startup portfolio."]
    assert rhythm_problems(good) == [], rhythm_problems(good)
    print(f"good rhythm passes: {[len(s.split()) for s in good]}")

    uniform = ["This sentence has exactly nine words in it here.",
               "This other sentence also has about nine words here.",
               "And a third sentence with roughly nine words too."]
    problems = rhythm_problems(uniform)
    assert problems, "uniform pacing must be caught"
    print(f"uniform rhythm caught: {problems[0][:80]}")

    too_long = ["A sentence that runs well past the twenty word ceiling because it "
                "keeps adding clauses and never stops for breath at all.", "Short."]
    assert any("over 20 words" in p for p in rhythm_problems(too_long))
    print("over-long sentence caught")
    print("checks.prose OK")
