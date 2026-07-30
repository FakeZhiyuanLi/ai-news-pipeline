"""Deterministic arithmetic checks. PURE FUNCTIONS — no network, no model, no
filesystem.

Two jobs:

  anchor_is_useful — is this anchor worth saying? "$5 billion is about 75,075
  average annual US salaries" fails: 75,075 is a second incomprehensible
  number, and the writer correctly refused to use it in all four drafts.

  verify — is the arithmetic right? Recomputed in Python against the
  hand-maintained REFERENCE_SCALES. A scale anchor computed from a
  model-supplied reference value is a hallucination wearing a division sign,
  so an expression referencing a scale that is not in config is rejected
  outright rather than checked.
"""

import ast
import operator
import re

import config

# An anchor result must land in this range of its reference unit. Below 0.1
# the comparison is a rounding error ("0.03 phones"); above 1,000 the result
# is just another big number.
MIN_ANCHOR_RATIO = 0.1
MAX_ANCHOR_RATIO = 1000.0

# The claimed result may be rounded for speech ("about 27" for 27.27), so the
# recomputation allows this much drift — and no more. An anchor off by 3x is
# not a rounding difference.
ANCHOR_TOLERANCE = 0.10

_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.USub: operator.neg,
}


def evaluate(expression: str, names: dict[str, float] | None = None) -> float:
    """Evaluate a simple arithmetic expression. Literals, the supplied names,
    and + - * / only — never eval(), which would execute whatever a model
    returned."""
    allowed = names or {}

    def walk(node):
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.Constant):
            if not isinstance(node.value, (int, float)):
                raise ValueError(f"non-numeric literal {node.value!r}")
            return float(node.value)
        if isinstance(node, ast.Name):
            if node.id not in allowed:
                raise ValueError(f"unknown reference {node.id!r}")
            return float(allowed[node.id])
        if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](walk(node.operand))
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](walk(node.left), walk(node.right))
        raise ValueError(f"unsupported expression element: {ast.dump(node)}")

    cleaned = expression.replace(",", "").replace("$", "").replace("×", "*")
    return walk(ast.parse(cleaned, mode="eval"))


def anchor_is_useful(anchor: dict | None) -> tuple[bool, str]:
    """Is this scale anchor worth saying out loud?

    Returns (ok, reason). An anchor whose arithmetic does not parse, or whose
    result lands outside the graspable range, is not a usable anchor — the
    correct response is to emit none.
    """
    if not anchor:
        return False, "no anchor"
    expression = anchor.get("anchor_math", "")
    try:
        result = evaluate(expression)
    except (ValueError, SyntaxError, ZeroDivisionError, TypeError) as e:
        return False, f"anchor_math {expression!r} does not evaluate: {e}"
    magnitude = abs(result)
    if magnitude < MIN_ANCHOR_RATIO:
        return False, (f"{expression} = {result:.4g}, below {MIN_ANCHOR_RATIO} — "
                       "too small a fraction to picture")
    if magnitude > MAX_ANCHOR_RATIO:
        return False, (f"{expression} = {result:,.4g}, above {MAX_ANCHOR_RATIO:,.0f} — "
                       "that is a second incomprehensible number, not a human scale")
    return True, f"{expression} = {result:,.4g}"


def reference_values() -> dict[str, float]:
    """The hand-maintained scales, by key. Nothing else may be referenced."""
    return {key: float(scale["value"])
            for key, scale in config.REFERENCE_SCALES.items()}


def _referenced_scales(expression: str) -> set[str]:
    """Scale keys named in an expression, whether or not they are known."""
    return set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", expression))


def verify(anchor_math: str, claimed_result: float) -> bool:
    """Does anchor_math actually produce claimed_result, within tolerance?

    The expression may name a REFERENCE_SCALES key in place of its value
    ("5000000000 / average_us_salary_usd") or use the literal. A key that is
    not in config.REFERENCE_SCALES rejects the anchor: the whole point of
    maintaining those values by hand is that a model does not get to supply
    them.
    """
    ok, _result, _reason = verify_detail(anchor_math, claimed_result)
    return ok


def uses_known_reference(anchor_math: str) -> bool:
    """Does the expression's divisor match a REFERENCE_SCALES value?

    Separate from verify(): "15000 / 400" is correct arithmetic but 400 is a
    reference value nobody maintained. An anchor must divide by a hand-kept
    scale, whether written as its key or as its literal value.
    """
    values = set(reference_values().values())
    named = _referenced_scales(anchor_math or "") & set(reference_values())
    if named:
        return True
    literals = {float(n) for n in re.findall(r"\d+(?:\.\d+)?",
                                             (anchor_math or "").replace(",", ""))}
    return bool(literals & values)


def verify_detail(anchor_math: str, claimed_result: float | None
                  ) -> tuple[bool, float | None, str]:
    """verify(), with the computed value and a reason for the error message."""
    scales = reference_values()
    unknown = _referenced_scales(anchor_math or "") - set(scales)
    if unknown:
        return False, None, (
            f"references {sorted(unknown)}, which is not in REFERENCE_SCALES — "
            "a reference value supplied by a model is not a reference value")
    try:
        computed = evaluate(anchor_math, names=scales)
    except (ValueError, SyntaxError, ZeroDivisionError, TypeError) as e:
        return False, None, f"{anchor_math!r} does not evaluate: {e}"
    if claimed_result is None:
        return False, computed, "no claimed result to compare against"
    if computed == 0:
        return (abs(claimed_result) < 1e-9, computed,
                "computed zero")
    drift = abs(computed - claimed_result) / abs(computed)
    if drift > ANCHOR_TOLERANCE:
        return False, computed, (
            f"{anchor_math} = {computed:,.4g}, but the script says "
            f"{claimed_result:,.4g} — off by {drift:.0%}")
    return True, computed, f"{anchor_math} = {computed:,.4g}"


if __name__ == "__main__":
    cases = [
        ({"anchor_math": "5000000000 / 66600"}, False),   # 75,075 salaries — the bug
        ({"anchor_math": "490 / 500"}, True),             # ~1 phone
        ({"anchor_math": "15000 / 550"}, True),           # ~27 GPUs
        ({"anchor_math": "5 / 66600"}, False),            # 0.00008 salaries
        ({"anchor_math": "1,200 / 550"}, True),           # commas tolerated
        ({"anchor_math": "$550 / $550"}, True),           # currency symbols tolerated
        ({"anchor_math": "550 / 0"}, False),              # division by zero
        ({"anchor_math": "__import__('os').system('x')"}, False),  # never eval()
        ({"anchor_math": "not math at all"}, False),
        (None, False),
    ]
    for anchor, expected in cases:
        ok, reason = anchor_is_useful(anchor)
        status = "ok  " if ok == expected else "FAIL"
        expr = (anchor or {}).get("anchor_math", "—")
        print(f"{status} {str(expr)[:34]:36} useful={ok!s:5} {reason}")
        assert ok == expected, expr

    print()
    gpu = config.REFERENCE_SCALES["midrange_gpu_price_usd"]["value"]
    verify_cases = [
        (f"15000 / {gpu}", 27.3, True, "correct anchor passes"),
        (f"15000 / {gpu}", 27.0, True, "rounded for speech passes"),
        (f"15000 / {gpu}", 81.8, False, "off by 3x drops"),
        ("15000 / midrange_gpu_price_usd", 27.3, True, "scale by name passes"),
        ("15000 / average_uk_salary_gbp", 27.3, False, "unknown scale rejected"),
        ("15000 / 0", 1.0, False, "division by zero"),
    ]
    for expression, claimed, expected, label in verify_cases:
        got = verify(expression, claimed)
        _ok, _computed, reason = verify_detail(expression, claimed)
        status = "ok  " if got == expected else "FAIL"
        print(f"{status} {label:38} verify={got!s:5} {reason[:60]}")
        assert got == expected, (expression, claimed)

    print()
    for expression, expected, label in [
        (f"15000 / {gpu}", True, "divides by a maintained scale literal"),
        ("15000 / midrange_gpu_price_usd", True, "divides by a scale key"),
        ("15000 / 400", False, "400 is a reference value nobody maintained"),
    ]:
        got = uses_known_reference(expression)
        status = "ok  " if got == expected else "FAIL"
        print(f"{status} {label:44} known_reference={got}")
        assert got == expected, expression
    print("checks.numbers OK")
