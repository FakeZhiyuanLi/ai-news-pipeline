"""The layer rules, with teeth.

CLAUDE.md is context, not enforcement. The previous build eroded exactly these
boundaries one convenient import at a time, so each rule here AST-walks the
source and fails naming the offending file and line.

  1. Only llm.py imports a model SDK (google-genai, elevenlabs).
  2. Only runs.py touches the filesystem: imports pathlib / os.path, calls open().
  3. Stages never import each other, except check.py -> draft.py.
  4. Only sources.py (and sources.json, which is data) contains a domain literal.
"""

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SDK_ROOTS = {"google", "elevenlabs"}
SDK_ALLOWED = {"llm.py"}

FS_ALLOWED = {"runs.py"}

STAGE_IMPORT_EXCEPTIONS = {("check", "draft")}

DOMAIN_ALLOWED = {"sources.py"}
DOMAIN_RE = re.compile(
    r"\b[a-z0-9][a-z0-9-]*\.(?:com|org|net|io|ai|co|dev|app|edu|gov|google)(?=[/\s\"']|$)")


def project_files() -> list[Path]:
    files = sorted(ROOT.glob("*.py"))
    for pkg in ("stages", "checks"):
        files += sorted((ROOT / pkg).glob("*.py"))
    assert files, "found no source files — is the test running from the wrong place?"
    return files


def parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def imported_modules(tree: ast.Module):
    """Yield (module_name, lineno, level) for every import statement."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, node.lineno, 0
        elif isinstance(node, ast.ImportFrom):
            yield node.module or "", node.lineno, node.level


def format_violations(rule: str, violations: list[str]) -> str:
    return f"{rule}:\n" + "\n".join(f"  {v}" for v in violations)


def test_only_llm_imports_model_sdks():
    violations = []
    for path in project_files():
        if path.name in SDK_ALLOWED:
            continue
        for module, lineno, _ in imported_modules(parse(path)):
            if module.split(".")[0] in SDK_ROOTS:
                violations.append(f"{path.relative_to(ROOT)}:{lineno} imports {module}")
    assert not violations, format_violations(
        "model SDK imports outside llm.py", violations)


def test_only_runs_touches_the_filesystem():
    violations = []
    for path in project_files():
        if path.name in FS_ALLOWED:
            continue
        rel = path.relative_to(ROOT)
        tree = parse(path)
        for module, lineno, level in imported_modules(tree):
            root_name = module.split(".")[0]
            if level == 0 and root_name == "pathlib":
                violations.append(f"{rel}:{lineno} imports pathlib")
            if module in ("os.path", "posixpath", "ntpath"):
                violations.append(f"{rel}:{lineno} imports {module}")
        for node in ast.walk(tree):
            if (isinstance(node, ast.ImportFrom) and node.module == "os"
                    and any(a.name == "path" for a in node.names)):
                violations.append(f"{rel}:{node.lineno} imports os.path")
            if (isinstance(node, ast.Attribute) and node.attr == "path"
                    and isinstance(node.value, ast.Name) and node.value.id == "os"):
                violations.append(f"{rel}:{node.lineno} uses os.path")
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "open"):
                violations.append(f"{rel}:{node.lineno} calls open()")
    assert not violations, format_violations(
        "filesystem access outside runs.py", violations)


def test_stages_never_import_each_other():
    violations = []
    for path in sorted((ROOT / "stages").glob("*.py")):
        if path.name == "__init__.py":
            continue
        this_stage = path.stem

        def stage_names(node):
            # Every spelling: import stages.x / from stages import x /
            # from stages.x import y / from .x import y / from . import x
            if isinstance(node, ast.Import):
                for alias in node.names:
                    parts = alias.name.split(".")
                    if parts[0] == "stages" and len(parts) > 1:
                        yield parts[1]
            elif isinstance(node, ast.ImportFrom):
                if node.level > 0:
                    if node.module:
                        yield node.module.split(".")[0]
                    else:
                        yield from (a.name for a in node.names)
                elif node.module == "stages":
                    yield from (a.name for a in node.names)
                elif node.module and node.module.startswith("stages."):
                    yield node.module.split(".")[1]

        for node in ast.walk(parse(path)):
            for imported in stage_names(node):
                if imported != this_stage \
                        and (this_stage, imported) not in STAGE_IMPORT_EXCEPTIONS:
                    violations.append(
                        f"stages/{path.name}:{node.lineno} imports stage {imported!r}")
    assert not violations, format_violations(
        "cross-stage imports (only check -> draft is allowed)", violations)


def test_only_sources_contains_domain_literals():
    violations = []
    for path in project_files():
        if path.name in DOMAIN_ALLOWED:
            continue
        for node in ast.walk(parse(path)):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                match = DOMAIN_RE.search(node.value)
                if match:
                    violations.append(
                        f"{path.relative_to(ROOT)}:{node.lineno} "
                        f"contains domain literal {match.group(0)!r}")
    assert not violations, format_violations(
        "domain literals outside sources.py (the allowlist is sources.json)",
        violations)
