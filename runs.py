"""Run directories, artifact IO, and every other touch of the filesystem.

This is the ONLY module in the pipeline that imports pathlib or calls open()
— enforced by tests/test_architecture.py. Stages receive a Run and call
run.read(name) / run.write(name, obj); they never construct a path or know a
filename. Modules with their own data files (ledger.py, sources.py) go through
the module-level helpers below.

Artifacts are immutable: write() raises if the artifact already exists.
"""

import json
import os
import re
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
    "narration": "narrate",
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


# --- .env ------------------------------------------------------------------
# Reading .env is filesystem access, so it lives here rather than in llm.py or
# tts.py. Without this the documented setup does not work: README says "cp
# .env.example .env, then fill in the keys", but nothing read the file, so the
# keys only resolved when they happened to be exported in the shell — and the
# error message told you to add them to a file that was never opened.
#
# Deliberately not python-dotenv: this is twelve lines and one fewer pin.


def load_env(name: str = ".env") -> int:
    """Load KEY=VALUE lines into os.environ WITHOUT overwriting anything
    already exported — a real environment variable must always win over a
    checked-out file. Returns how many keys were set."""
    path = _resolve(name)
    if not path.exists():
        return 0
    loaded = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if value and key not in os.environ:
            os.environ[key] = value
            loaded += 1
    return loaded


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


def cached_bytes(directory: str, filename: str) -> bytes | None:
    """Read a cached binary blob, or None if it is not there. Used for
    narration audio, which is ~97% of marginal cost and must never be
    re-synthesised for a script that has not changed."""
    path = _resolve(directory) / filename
    return path.read_bytes() if path.exists() else None


def write_cached_bytes(directory: str, filename: str, data: bytes) -> str:
    target = _resolve(directory)
    target.mkdir(parents=True, exist_ok=True)
    path = target / filename
    path.write_bytes(data)
    return str(path.relative_to(PROJECT_ROOT))


def copy_into(directory: str, filename: str, source_path: str) -> str:
    """Copy an existing file (cached narration audio) into a directory the
    renderer can serve from. Returns the path relative to the project root."""
    import shutil
    target = _resolve(directory)
    target.mkdir(parents=True, exist_ok=True)
    destination = target / filename
    shutil.copyfile(_resolve(source_path), destination)
    return str(destination.relative_to(PROJECT_ROOT))


def run_dir_path(run_id: str) -> str:
    """Absolute path of a run directory, for handing to an external tool."""
    return str(_runs_root() / run_id)


def absolute_path(name: str) -> str:
    return str(_resolve(name))


def file_exists(name: str) -> bool:
    return _resolve(name).exists()


def read_text_file(name: str) -> str:
    """A project-relative text file, verbatim. Used by tests that assert the
    renderer's JS constants still match their Python mirrors."""
    return _resolve(name).read_text(encoding="utf-8")


def next_numbered_name(directory: str, prefix: str, suffix: str) -> str:
    """`bench1.md`, `bench2.md`, ... one higher than the highest present.

    Sorted NUMERICALLY like run ids, for the same reason: these used to be UTC
    timestamps, where string order equalled creation order. `bench10` sorts
    before `bench2` as a string, so "the most recent bench" read off a plain
    listing would be wrong once a tenth exists.
    """
    target = _resolve(directory)
    if not target.is_dir():
        return f"{prefix}1{suffix}"
    pattern = re.compile(rf"^{re.escape(prefix)}(\d+){re.escape(suffix)}$")
    highest = 0
    for entry in target.glob("*"):
        match = pattern.match(entry.name)
        if match:
            highest = max(highest, int(match.group(1)))
    return f"{prefix}{highest + 1}{suffix}"


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


_RUN_ID_RE = re.compile(r"^run(\d+)$")


def run_number(run_id: str) -> int | None:
    """The ordinal in a `runN` id, or None if it is not one."""
    match = _RUN_ID_RE.match(run_id)
    return int(match.group(1)) if match else None


def all_run_ids() -> list[str]:
    """Every run, OLDEST FIRST — `latest()` takes the last element.

    Sorted NUMERICALLY, not lexicographically. The ids used to be UTC
    timestamps, where string order happened to equal time order; `run2` and
    `run10` do not have that property, and a plain sort would make run10 the
    second-oldest and hand `--resume` the wrong directory.
    """
    root = _runs_root()
    if not root.is_dir():
        return []
    names = [d.name for d in root.glob("*") if d.is_dir()]
    numbered = sorted((n for n in names if run_number(n) is not None),
                      key=run_number)
    # Anything not matching runN (an older timestamped directory) sorts before
    # the numbered ones, so it is never picked as "latest".
    return sorted(n for n in names if run_number(n) is None) + numbered


class Run:
    """One pipeline run: runs/<run_id>/ and everything inside it."""

    def __init__(self, run_id: str, root: Path):
        self.run_id = run_id
        self._dir = root / run_id

    def __repr__(self) -> str:
        return f"Run({self.run_id})"

    @classmethod
    def new(cls) -> "Run":
        """The next `runN`, one higher than the highest that exists.

        mkdir is exclusive, so two runs starting at once cannot claim the same
        number — the loser retries against the now-higher maximum rather than
        writing into a directory that already holds another run's artifacts.
        """
        root = _runs_root()
        root.mkdir(parents=True, exist_ok=True)
        highest = max((run_number(n) or 0 for n in all_run_ids()), default=0)
        for candidate in range(highest + 1, highest + 100):
            run = cls(f"run{candidate}", root)
            try:
                run._dir.mkdir(parents=True)
            except FileExistsError:
                continue
            return run
        raise RuntimeError("could not allocate a run number")

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
