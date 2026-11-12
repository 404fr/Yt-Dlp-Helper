"""Subprocess execution, progress parsing, and environment diagnostics for yt-dlp."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
import shutil
import signal
import subprocess
import time
from typing import Callable


_PROGRESS_IN_PROGRESS_RE = re.compile(
    r"^\[download\]\s+(?P<pct>\d+(?:\.\d+)?)%\s+of\s+(?P<total>~?\s*\S+)"
    r"(?:\s+at\s+(?P<speed>\S+(?:\s*/\S+)?|\S+))?"
    r"(?:\s+ETA\s+(?P<eta>\S+))?"
    r"(?:\s+\((?P<frag>frag\s+\d+/\d+)\))?"
)

_PROGRESS_DONE_RE = re.compile(
    r"^\[download\]\s+(?P<pct>100(?:\.0)?)%\s+of\s+(?P<total>~?\s*\S+)"
    r"\s+in\s+(?P<elapsed>\S+)"
    r"(?:\s+at\s+(?P<speed>.+?))?$"
)

_DEST_RE = re.compile(r"^\[download\]\s+Destination:\s+(?P<path>.+)$")
_ALREADY_RE = re.compile(r"^\[download\]\s+(?P<path>.+?)\s+has already been downloaded$")
_MERGER_RE = re.compile(r'^\[Merger\]\s+Merging formats into\s+"(?P<path>.+)"$')
_EXTRACT_AUDIO_RE = re.compile(r"^\[ExtractAudio\]\s+Destination:\s+(?P<path>.+)$")
_PLAYLIST_ITEM_RE = re.compile(r"^\[download\]\s+Downloading item\s+\d+\s+of\s+\d+.*$")


@dataclass
class EnvironmentStatus:
    """Detected availability and versions of yt-dlp, ffmpeg, and JS runtimes."""

    yt_dlp_path: str | None = None
    yt_dlp_version: str = ""
    ffmpeg_path: str | None = None
    ffmpeg_version: str = ""
    ffprobe_path: str | None = None
    js_runtimes: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.yt_dlp_path)


def _probe_first_line(cmd: list[str], timeout: float = 4.0) -> str:
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = (proc.stdout or proc.stderr or "").strip()
        return output.splitlines()[0].strip() if output else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def check_environment() -> EnvironmentStatus:
    """Inspect PATH for yt-dlp, ffmpeg, ffprobe, and supported JavaScript runtimes."""
    yt_dlp_path = shutil.which("yt-dlp")
    ffmpeg_path = shutil.which("ffmpeg")
    ffprobe_path = shutil.which("ffprobe")

    yt_dlp_version = _probe_first_line([yt_dlp_path, "--version"]) if yt_dlp_path else ""
    ffmpeg_raw = _probe_first_line([ffmpeg_path, "-version"]) if ffmpeg_path else ""
    ffmpeg_version = ""
    if ffmpeg_raw:
        match = re.search(r"ffmpeg version\s+(\S+)", ffmpeg_raw)
        ffmpeg_version = match.group(1) if match else ffmpeg_raw

    js_runtimes: dict[str, str] = {}
    for runtime, candidates in (
        ("deno", ("deno",)),
        ("node", ("node",)),
        ("bun", ("bun",)),
        ("quickjs", ("qjs", "quickjs")),
    ):
        for exe in candidates:
            found = shutil.which(exe)
            if found:
                js_runtimes[runtime] = found
                break

    return EnvironmentStatus(
        yt_dlp_path=yt_dlp_path,
        yt_dlp_version=yt_dlp_version,
        ffmpeg_path=ffmpeg_path,
        ffmpeg_version=ffmpeg_version,
        ffprobe_path=ffprobe_path,
        js_runtimes=js_runtimes,
    )


@dataclass(frozen=True)
class ProgressEvent:
    """Structured download progress update."""

    percent: float
    total: str
    speed: str
    eta: str
    finished: bool = False
    extra: str = ""


@dataclass(frozen=True)
class StatusEvent:
    """Structured non-progress status or log line from yt-dlp."""

    kind: str  # "destination", "already", "playlist_item", "postprocess", "warning", "error", "info"
    message: str
    path: str = ""


def parse_ytdlp_line(line: str) -> ProgressEvent | StatusEvent | None:
    """Classify a single line of yt-dlp output into a ProgressEvent or StatusEvent."""
    stripped = line.strip()
    if not stripped:
        return None

    done_match = _PROGRESS_DONE_RE.match(stripped)
    if done_match:
        return ProgressEvent(
            percent=100.0,
            total=done_match.group("total").replace(" ", ""),
            speed=(done_match.group("speed") or "").strip(),
            eta=done_match.group("elapsed"),
            finished=True,
        )

    prog_match = _PROGRESS_IN_PROGRESS_RE.match(stripped)
    if prog_match:
        try:
            pct = float(prog_match.group("pct"))
        except ValueError:
            pct = 0.0
        return ProgressEvent(
            percent=min(max(pct, 0.0), 100.0),
            total=prog_match.group("total").replace(" ", ""),
            speed=(prog_match.group("speed") or "").strip(),
            eta=(prog_match.group("eta") or "").strip(),
            finished=False,
            extra=(prog_match.group("frag") or "").strip(),
        )

    dest_match = _DEST_RE.match(stripped)
    if dest_match:
        return StatusEvent(kind="destination", message=stripped, path=dest_match.group("path"))

    already_match = _ALREADY_RE.match(stripped)
    if already_match:
        return StatusEvent(kind="already", message=stripped, path=already_match.group("path"))

    merger_match = _MERGER_RE.match(stripped)
    if merger_match:
        return StatusEvent(kind="postprocess", message=stripped, path=merger_match.group("path"))

    extract_match = _EXTRACT_AUDIO_RE.match(stripped)
    if extract_match:
        return StatusEvent(kind="postprocess", message=stripped, path=extract_match.group("path"))

    if _PLAYLIST_ITEM_RE.match(stripped):
        return StatusEvent(kind="playlist_item", message=stripped)

    if stripped.startswith("ERROR:"):
        return StatusEvent(kind="error", message=stripped)

    if stripped.startswith("WARNING:"):
        return StatusEvent(kind="warning", message=stripped)

    if stripped.startswith("["):
        tag_end = stripped.find("]")
        tag = stripped[1:tag_end] if tag_end > 1 else ""
        if tag in {
            "Merger",
            "ExtractAudio",
            "EmbedThumbnail",
            "Metadata",
            "ModifyChapters",
            "SponsorBlock",
            "SplitChapters",
            "VideoRemuxer",
            "VideoConvertor",
            "EmbedSubtitle",
            "FixupM4a",
            "FixupM3u8",
            "FixupTimestamp",
        }:
            return StatusEvent(kind="postprocess", message=stripped)

    return StatusEvent(kind="info", message=stripped)


@dataclass
class DownloadResult:
    """Result summary of a yt-dlp subprocess invocation."""

    exit_code: int
    cancelled: bool = False
    elapsed_seconds: float = 0.0
    destinations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _with_newline_flag(cmd: list[str]) -> list[str]:
    if "--newline" in cmd:
        return list(cmd)
    if not cmd:
        return ["--newline"]
    return [cmd[0], "--newline", *cmd[1:]]


def run_ytdlp(
    cmd: list[str],
    *,
    on_progress: Callable[[ProgressEvent], None] | None = None,
    on_status: Callable[[StatusEvent], None] | None = None,
) -> DownloadResult:
    """Run a yt-dlp command, streaming parsed progress and status events."""
    if not cmd:
        raise ValueError("Command cannot be empty.")

    resolved_cmd = _with_newline_flag(cmd)
    if resolved_cmd[0] == "yt-dlp":
        found = shutil.which("yt-dlp")
        if not found:
            return DownloadResult(
                exit_code=127,
                errors=["ERROR: yt-dlp executable was not found in PATH."],
            )
        resolved_cmd[0] = found

    start_time = time.monotonic()
    destinations: list[str] = []
    warnings: list[str] = []
    errors: list[str] = []
    cancelled = False

    try:
        proc = subprocess.Popen(
            resolved_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            errors="replace",
        )
    except OSError as exc:
        return DownloadResult(
            exit_code=1,
            errors=[f"ERROR: Failed to launch yt-dlp: {exc}"],
        )

    assert proc.stdout is not None
    try:
        for raw_line in proc.stdout:
            event = parse_ytdlp_line(raw_line)
            if event is None:
                continue
            if isinstance(event, ProgressEvent):
                if on_progress:
                    on_progress(event)
            else:
                if event.path and event.path not in destinations:
                    destinations.append(event.path)
                if event.kind == "warning":
                    warnings.append(event.message)
                elif event.kind == "error":
                    errors.append(event.message)
                if on_status:
                    on_status(event)
        exit_code = proc.wait()
    except KeyboardInterrupt:
        cancelled = True
        try:
            proc.send_signal(signal.SIGINT)
            proc.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        except OSError:
            pass
        exit_code = 130
    finally:
        try:
            proc.stdout.close()
        except OSError:
            pass

    elapsed = time.monotonic() - start_time
    return DownloadResult(
        exit_code=exit_code,
        cancelled=cancelled,
        elapsed_seconds=elapsed,
        destinations=destinations,
        warnings=warnings,
        errors=errors,
    )
