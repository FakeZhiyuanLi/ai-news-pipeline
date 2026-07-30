"""Build renderer props and drive the Remotion CLI.

Two rules that the rest of the file exists to enforce:

  DURATION COMES FROM REAL AUDIO. The props carry the measured
  duration_seconds from the narration artifact; the composition derives its
  frame count from that. estimated_seconds is a measurement for logs and is
  never sent to the renderer.

  NEVER RENDER A SCRIPT THAT FAILED CHECKING, or one with no audio. Both are
  real outcomes and should look like errors.
"""

import json
import logging
import subprocess

import config
import runs

log = logging.getLogger(__name__)


class RenderError(RuntimeError):
    """Refused to render, or the renderer failed."""


def _usable_images(report: dict, line_count: int) -> list[dict | None]:
    """One image slot per line, or None for a typographic treatment.

    Images are spread across the lines rather than repeated: a line either
    gets a real image from the article or it is typographic. There is no
    stock library and no selection logic beyond this.
    """
    images = [im for im in report.get("images", [])
              if im.get("width") and im.get("height")]
    slots: list[dict | None] = [None] * line_count
    if not images:
        return slots
    # Lede first, then spread the rest evenly over the remaining lines.
    step = max(1, line_count // max(1, len(images)))
    for index, image in enumerate(images):
        position = min(index * step, line_count - 1)
        if slots[position] is None:
            slots[position] = image
    return slots


def build_props(run) -> dict:
    """Props for one run. Raises if the run is not renderable."""
    if not run.exists("script"):
        raise RenderError(
            f"run {run.run_id} has no script artifact; "
            f"produce it with: python run.py --run-id {run.run_id} --stage check")
    script = run.read("script")
    if not script.get("passed", False):
        raise RenderError(
            f"run {run.run_id} failed checking — refusing to render it. "
            f"{script.get('failure', '')}".strip())
    if not run.exists("narration"):
        raise RenderError(
            f"run {run.run_id} has no audio; "
            f"produce it with: python run.py --run-id {run.run_id} --stage narrate")

    narration = run.read("narration")
    audio_path = narration.get("audio_path")
    if not audio_path or not runs.file_exists(audio_path):
        raise RenderError(
            f"narration for {run.run_id} points at {audio_path!r}, which is not "
            "on disk — re-run the narrate stage")

    # The renderer serves from its own public directory, so the cached audio
    # is copied in under a stable name.
    audio_name = f"{narration['cache_key']}.mp3"
    runs.copy_into(config.RENDERER_PUBLIC_DIR, audio_name, audio_path)

    report = run.read("report") if run.exists("report") else {}
    lines = narration["lines"]
    return {
        "runId": run.run_id,
        "title": script["story"]["title"],
        # Real measured audio length. Not estimated_seconds.
        "durationSeconds": narration["duration_seconds"],
        "audioSrc": audio_name,
        "lines": lines,
        "images": _usable_images(report, len(lines)),
    }


def latest_narrated_run():
    """The most recent run that actually has audio — what --update-props
    previews, so `npm start` always shows something real."""
    for run_id in reversed(runs.all_run_ids()):
        run = runs.Run.resume(run_id)
        if run.exists("narration") and run.exists("script"):
            return run
    raise RenderError("no run has been narrated yet; "
                      "run: python run.py --stage narrate")


def update_props(run=None) -> str:
    run = run or latest_narrated_run()
    props = build_props(run)
    path = runs.write_text_file(
        config.RENDERER_DIR, "props.json",
        json.dumps(props, indent=2, ensure_ascii=False) + "\n")
    log.info("props from run %s: %.1fs, %d lines, %d image(s)",
             run.run_id, props["durationSeconds"], len(props["lines"]),
             sum(1 for i in props["images"] if i))
    return path


def render(run=None, *, quality_preview: bool = False) -> str:
    """Shell out to the Remotion CLI, writing video.mp4 into the run dir."""
    run = run or latest_narrated_run()
    props = build_props(run)
    update_props(run)

    output = runs.absolute_path(
        f"{config.RUNS_DIR}/{run.run_id}/{config.VIDEO_FILENAME}")
    command = [
        "npx", "remotion", "render", "src/index.js", config.COMPOSITION_ID,
        output,
        "--props", runs.absolute_path(config.RENDERER_PROPS_FILE),
    ]
    if quality_preview:
        command += ["--scale", "0.5"]

    log.info("rendering %s -> %s (%.1fs of audio)",
             run.run_id, output, props["durationSeconds"])
    result = subprocess.run(
        command, cwd=runs.absolute_path(config.RENDERER_DIR),
        capture_output=True, text=True,
        timeout=config.RENDER_TIMEOUT_SECONDS)
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "").strip().splitlines()[-25:]
        raise RenderError("remotion render failed:\n" + "\n".join(tail))
    log.info("wrote %s", output)
    return output


# --- verification ----------------------------------------------------------

def probe_duration(path: str) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True, check=True)
    return float(result.stdout.strip())


def probe_mean_volume(path: str) -> float:
    """Mean volume in dB. -91 dB means a silent track — the classic symptom
    of audio that was mounted but never actually decoded."""
    result = subprocess.run(
        ["ffmpeg", "-i", path, "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True)
    for line in result.stderr.splitlines():
        if "mean_volume:" in line:
            return float(line.split("mean_volume:")[1].split("dB")[0].strip())
    raise RenderError(f"volumedetect reported no mean_volume for {path}")


def verify(video_path: str, run) -> list[str]:
    """The three checks the video must pass. Returns problems, empty if good."""
    narration = run.read("narration")
    problems = []

    volume = probe_mean_volume(video_path)
    if volume <= -90.0:
        problems.append(
            f"mean volume {volume} dB — the audio track is silent")

    video_seconds = probe_duration(video_path)
    audio_seconds = narration["duration_seconds"]
    drift = abs(video_seconds - audio_seconds)
    if drift > 1.0:
        problems.append(
            f"video is {video_seconds:.2f}s but the audio is {audio_seconds:.2f}s "
            f"— {drift:.2f}s apart")

    first = narration["lines"][0]
    if abs(first["start_seconds"] - first["words"][0]["start_seconds"]) > 0.001:
        problems.append("first line does not start at its first word's timing")

    log.info("verified: %.2f dB mean volume, video %.2fs vs audio %.2fs",
             volume, video_seconds, audio_seconds)
    return problems


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        run = latest_narrated_run()
        print(f"latest narrated run: {run.run_id}")
        props = build_props(run)
        print(f"  duration {props['durationSeconds']}s (from real audio)")
        print(f"  {len(props['lines'])} lines, "
              f"{sum(1 for i in props['images'] if i)} with images")
        for line, image in zip(props["lines"], props["images"]):
            treatment = "image" if image else "typographic"
            print(f"    {line['start_seconds']:6.2f}  {treatment:12} "
                  f"{line['text'][:48]}")
    except RenderError as e:
        print(f"not renderable: {e}")
