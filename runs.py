"""Run directories, artifact IO, and every other touch of the filesystem.

This is the ONLY module in the pipeline that imports pathlib or calls open()
— enforced by tests/test_architecture.py. Stages receive a Run and call
run.read(name) / run.write(name, obj); they never construct a path or know a
filename. Modules with their own data files (ledger.py, sources.py) go through
the module-level helpers below.

Artifacts are immutable: write() raises if the artifact already exists.
"""

import datetime as _dt
import json
import re
import secrets
from pathlib import Path

import config

PROJECT_ROOT = Path(__file__).resolve().parent

_ARTIFACT_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

# Which stage produces which artifact, for actionable read() errors. Extend
# this as stages are implemented; stages themselves never see these names.
ARTIFACT_PRODUCERS: dict[str, str] = {
    "candidates": "find",
    "story": "pick",
    "report": "report",
    "draft": "draft",
    "script": "check",
}


class ArtifactExistsError(RuntimeError):
    """Attempted to overwrite an immutable artifact."""


class MissingArtifactError(FileNotFoundError):
    """Artifact not present in the run; message names the producing command."""


class MissingDataFileError(FileNotFoundError):
    """A project-level data file (ledger, sources) is absent."""


class UnknownRunError(RuntimeError):
    """No run directory with the given id."""


def _runs_root() -> Path:
    return PROJECT_ROOT / config.RUNS_DIR


def _resolve(name: str) -> Path:
    p = Path(name)
    return p if p.is_absolute() else PROJECT_ROOT / p


def _write_json(path: Path, obj, exclusive: bool) -> None:
    text = json.dumps(obj, indent=2, ensure_ascii=False) + "\n"
    with path.open("x" if exclusive else "w", encoding="utf-8") as f:
        f.write(text)


# --- Project-level data files (ledger.json, sources.json, costs.jsonl) -----

def load_data_file(name: str):
    path = _resolve(name)
    if not path.exists():
        raise MissingDataFileError(f"data file not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def save_data_file(name: str, obj) -> None:
    """Overwriting save for mutable project state. Not for run artifacts."""
    path = _resolve(name)
    tmp = path.with_name(path.name + ".tmp")
    _write_json(tmp, obj, exclusive=False)
    tmp.replace(path)


def append_jsonl(name: str, entry: dict) -> None:
    path = _resolve(name)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read_jsonl(name: str) -> list[dict]:
    path = _resolve(name)
    if not path.exists():
        raise MissingDataFileError(f"data file not found: {path}")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def write_text_file(directory: str, filename: str, text: str) -> str:
    """Write a human-readable report (bench output, README) and return its
    path. Not a run artifact — those go through Run.write."""
    target = _resolve(directory)
    target.mkdir(parents=True, exist_ok=True)
    path = target / filename
    path.write_text(text, encoding="utf-8")
    return str(path.relative_to(PROJECT_ROOT))


# --- Runs ------------------------------------------------------------------

class temporary_root:
    """Point the runs directory at a throwaway location. For --selftest and
    module demos, so they never write into the real runs/ tree."""

    def __enter__(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        global PROJECT_ROOT
        self._saved = PROJECT_ROOT
        PROJECT_ROOT = Path(self._tmp.name)
        return PROJECT_ROOT

    def __exit__(self, *exc):
        global PROJECT_ROOT
        PROJECT_ROOT = self._saved
        self._tmp.cleanup()


def fixture_text(name: str) -> str:
    """Read a committed test fixture. Fixtures are inputs, not artifacts."""
    path = PROJECT_ROOT / "tests" / "fixtures" / name
    if not path.exists():
        raise MissingDataFileError(f"fixture not found: {path}")
    return path.read_text(encoding="utf-8")


def all_run_ids() -> list[str]:
    root = _runs_root()
    if not root.is_dir():
        return []
    return sorted(d.name for d in root.glob("*") if d.is_dir())


class Run:
    """One pipeline run: runs/<run_id>/ and everything inside it."""

    def __init__(self, run_id: str, root: Path):
        self.run_id = run_id
        self._dir = root / run_id

    def __repr__(self) -> str:
        return f"Run({self.run_id})"

    @classmethod
    def new(cls) -> "Run":
        stamp = _dt.datetime.now(_dt.timezone.utc).strftime(config.RUN_TIMESTAMP_FORMAT)
        run_id = f"{stamp}-{secrets.token_hex(2)}"
        run = cls(run_id, _runs_root())
        run._dir.mkdir(parents=True)
        return run

    @classmethod
    def resume(cls, run_id: str) -> "Run":
        run = cls(run_id, _runs_root())
        if not run._dir.is_dir():
            known = ", ".join(r.name for r in sorted(_runs_root().glob("*"))[-5:]) or "none"
            raise UnknownRunError(
                f"no run directory for id {run_id!r} (most recent: {known})")
        return run

    @classmethod
    def latest(cls) -> "Run":
        candidates = all_run_ids()
        if not candidates:
            raise UnknownRunError("no runs exist yet; start one with: python run.py")
        return cls(candidates[-1], _runs_root())

    # --- artifacts ---------------------------------------------------------

    def _artifact_path(self, name: str) -> Path:
        if not _ARTIFACT_NAME_RE.match(name):
            raise ValueError(f"bad artifact name {name!r}: lowercase slug, no extension")
        return self._dir / f"{name}.json"

    def write(self, name: str, obj) -> None:
        path = self._artifact_path(name)
        try:
            _write_json(path, obj, exclusive=True)
        except FileExistsError:
            raise ArtifactExistsError(
                f"artifact {name!r} already exists in run {self.run_id}; "
                "artifacts are immutable — start a new run instead") from None

    def read(self, name: str):
        path = self._artifact_path(name)
        if not path.exists():
            stage = ARTIFACT_PRODUCERS.get(name, "<stage>")
            raise MissingArtifactError(
                f"artifact {name!r} does not exist in run {self.run_id}. "
                f"Produce it with: python run.py --run-id {self.run_id} --stage {stage}")
        return json.loads(path.read_text(encoding="utf-8"))

    def exists(self, name: str) -> bool:
        return self._artifact_path(name).exists()

    def list_artifacts(self) -> list[str]:
        return sorted(p.stem for p in self._dir.glob("*.json"))

    # --- LLM transcripts ---------------------------------------------------

    def write_llm_log(self, purpose: str, payload: dict) -> str:
        """Store the full prompt/response of one model call as
        runs/<run_id>/llm/<NN>-<purpose>.json. Returns the path (for error
        messages only — nothing should parse it)."""
        llm_dir = self._dir / "llm"
        llm_dir.mkdir(exist_ok=True)
        slug = re.sub(r"[^a-z0-9_-]+", "-", purpose.lower()).strip("-") or "call"
        seq = len(list(llm_dir.glob("*.json"))) + 1
        path = llm_dir / f"{seq:02d}-{slug}.json"
        _write_json(path, payload, exclusive=True)
        return str(path)


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        PROJECT_ROOT = Path(tmp)  # keep the demo out of the real runs/

        run = Run.new()
        print(f"created {run}")
        run.write("story", {"title": "demo", "url": "https://demo.test/x"})
        assert run.read("story")["title"] == "demo"
        try:
            run.write("story", {"title": "overwrite"})
        except ArtifactExistsError as e:
            print(f"immutability holds: {e}")
        try:
            run.read("script")
        except MissingArtifactError as e:
            print(f"missing artifact names the fix: {e}")
        log_path = run.write_llm_log("extract facts", {"prompt": "p", "response": "r"})
        print(f"llm transcript at {log_path}")
        assert Run.latest().run_id == run.run_id
        assert Run.resume(run.run_id).list_artifacts() == ["story"]

        append_jsonl("demo.jsonl", {"n": 1})
        append_jsonl("demo.jsonl", {"n": 2})
        save_data_file("demo.json", {"ok": True})
        assert load_data_file("demo.json") == {"ok": True}
        try:
            load_data_file("absent.json")
        except MissingDataFileError as e:
            print(f"missing data file: {e}")
    print("runs OK")
