"""The layer rules, with teeth.

CLAUDE.md is context, not enforcement. The previous build eroded exactly these
boundaries one convenient import at a time, so each rule here AST-walks the
source and fails naming the offending file and line.

  1. Only llm.py imports a model SDK (openai, elevenlabs).
  2. Only runs.py touches the filesystem: imports pathlib / os.path, calls open().
  3. Stages never import each other, except check.py -> draft.py.
  4. Only sources.py (and sources.json, which is data) contains a domain literal.
  5. Only config.py contains a base_url or model-name literal.
  6. No stage passes a model name to json_call — it takes a purpose.
"""

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SDK_ROOTS = {"openai", "google", "anthropic", "elevenlabs"}
SDK_ALLOWED = {"llm.py"}

MODEL_LITERAL_ALLOWED = {"config.py"}
# A provider path ("accounts/fireworks/models/..."), a vendor-versioned slug
# (kimi-k2p6, gpt-4o, claude-opus-5), or an inference base URL.
MODEL_LITERAL_RE = re.compile(
    r"(accounts/[\w-]+/(?:models|routers)/[\w.-]+"
    r"|\b(?:kimi|deepseek|glm|gemini|gpt|claude|llama|qwen|mistral|minimax)[\w.]*-[\w.]+"
    r"|https?://[\w.-]*(?:api|inference|openai|anthropic|fireworks)[\w.-]*/[\w/.-]*)",
    re.IGNORECASE)

FS_ALLOWED = {"runs.py"}

STAGE_IMPORT_EXCEPTIONS = {("check", "draft")}

# The domain rule protects the FETCH allowlist: a URL a stage might scrape
# belongs in sources.json and nowhere else. The inference endpoint is not a
# content source, and rule 5 requires it to live in config.py, so config's
# model/base_url literals are exempt here — and only here.
DOMAIN_ALLOWED = {"sources.py"}
DOMAIN_EXEMPT_LINES = {"config.py": MODEL_LITERAL_RE}
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


def test_only_config_contains_model_literals():
    """Model slugs and base URLs live in config.py. A stage that names a model
    is choosing one, which is llm.py's job via config.MODELS."""
    violations = []
    for path in project_files():
        if path.name in MODEL_LITERAL_ALLOWED:
            continue
        for node in ast.walk(parse(path)):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                match = MODEL_LITERAL_RE.search(node.value)
                if match:
                    violations.append(
                        f"{path.relative_to(ROOT)}:{node.lineno} "
                        f"contains model/base_url literal {match.group(0)!r}")
    assert not violations, format_violations(
        "model or base_url literals outside config.py", violations)


def test_no_stage_passes_a_model_name_to_json_call():
    """json_call's first argument is a purpose key from config.MODELS. A
    stage reaching for config.KIMI or config.MODELS has taken model selection
    back out of llm.py."""
    purposes = {"rank", "report", "draft", "check"}
    violations = []
    for path in project_files():
        if path.name == "llm.py":
            continue
        rel = path.relative_to(ROOT)
        for node in ast.walk(parse(path)):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "json_call"):
                continue
            if not node.args:
                violations.append(f"{rel}:{node.lineno} json_call with no purpose")
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                if first.value not in purposes:
                    violations.append(
                        f"{rel}:{node.lineno} json_call purpose {first.value!r} "
                        f"is not one of {sorted(purposes)}")
            else:
                violations.append(
                    f"{rel}:{node.lineno} json_call purpose is not a literal "
                    "string (a model name may be reaching llm.py)")
    assert not violations, format_violations(
        "json_call must take a purpose, never a model name", violations)


def test_no_module_references_model_constants_outside_config():
    """Nothing outside config.py and llm.py should read config.MODELS or a
    slug constant: that is model selection leaking back into callers."""
    banned = {"MODELS", "KIMI", "DEEPSEEK_PRO", "GLM", "BASE_URL", "PRICES"}
    allowed = {"config.py", "llm.py", "bench.py"}  # bench reports which model ran
    violations = []
    for path in project_files():
        if path.name in allowed:
            continue
        for node in ast.walk(parse(path)):
            if (isinstance(node, ast.Attribute) and node.attr in banned
                    and isinstance(node.value, ast.Name) and node.value.id == "config"):
                violations.append(f"{path.relative_to(ROOT)}:{node.lineno} "
                                  f"reads config.{node.attr}")
    assert not violations, format_violations(
        "model selection must stay in llm.py", violations)


def test_only_sources_contains_domain_literals():
    violations = []
    for path in project_files():
        if path.name in DOMAIN_ALLOWED:
            continue
        exempt = DOMAIN_EXEMPT_LINES.get(path.name)
        for node in ast.walk(parse(path)):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if exempt and exempt.search(node.value):
                    continue  # a model slug or inference URL, not a fetch target
                match = DOMAIN_RE.search(node.value)
                if match:
                    violations.append(
                        f"{path.relative_to(ROOT)}:{node.lineno} "
                        f"contains domain literal {match.group(0)!r}")
    assert not violations, format_violations(
        "domain literals outside sources.py (the allowlist is sources.json)",
        violations)
