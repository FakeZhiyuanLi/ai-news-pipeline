"""Deterministic arithmetic checks. Pure functions, no model calls, no IO.

A scale anchor exists to make a big number graspable. "$5 billion is about
75,075 average annual US salaries" fails at that: 75,075 is a second
incomprehensible number, and the writer correctly refused to use it in all
four drafts. An anchor only helps when the result lands in a range a person
can hold in their head.
"""

import ast
import operator

# An anchor result must land in this range of its reference unit. Below 0.1
# the comparison is a rounding error ("0.03 phones"); above 1,000 the result
# is just another big number.
MIN_ANCHOR_RATIO = 0.1
MAX_ANCHOR_RATIO = 1000.0

_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.USub: operator.neg,
}


def evaluate(expression: str) -> float:
    """Evaluate a simple arithmetic expression. Literals and + - * / only —
    never eval(), which would execute whatever a model returned."""
    def walk(node):
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.Constant):
            if not isinstance(node.value, (int, float)):
                raise ValueError(f"non-numeric literal {node.value!r}")
            return float(node.value)
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
    print("checks.numbers OK")
