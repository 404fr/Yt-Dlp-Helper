"""Metadata and format inspection via yt-dlp JSON output."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import shutil
import subprocess
from typing import Any

from .command_builder import build_inspect_command
from .config import DownloadConfig


def format_bytes(size: int | float | None) -> str:
    """Format a byte count in human-readable IEC units."""
    if size is None or size <= 0:
        return "~"
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    val = float(size)
    for unit in units:
        if val < 1024.0 or unit == units[-1]:
            if unit == "B":
                return f"{int(val)} B"
            return f"{val:.1f} {unit}"
        val /= 1024.0
    return f"{val:.1f} TiB"


def format_duration(seconds: int | float | None) -> str:
    """Format duration in seconds as HH:MM:SS or MM:SS."""
    if seconds is None or seconds < 0:
        return "Live/Unknown"
    total = int(round(seconds))
    hrs, rem = divmod(total, 3600)
    mins, secs = divmod(rem, 60)
    if hrs > 0:
        return f"{hrs}:{mins:02d}:{secs:02d}"
    return f"{mins}:{secs:02d}"


def shorten_codec(codec: str | None) -> str:
    """Return a compact, readable codec name."""
    if not codec or codec == "none":
        return "-"
    lower = codec.lower()
    if lower.startswith("avc1") or lower == "h264":
        return "h264"
    if lower.startswith(("hev1", "hvc1")) or lower == "hevc":
        return "hevc"
    if lower.startswith("vp09") or lower == "vp9":
        return "vp9"
    if lower.startswith("av01") or lower == "av1":
        return "av1"
    if lower.startswith("mp4a"):
        return "aac"
    if "." in codec:
        return codec.split(".", 1)[0]
    return codec


@dataclass
class FormatEntry:
    """Single media stream format offered by yt-dlp."""

    format_id: str
    ext: str
    resolution: str
    width: int | None
    height: int | None
    fps: float | None
    vcodec: str
    acodec: str
    tbr: float | None
    vbr: float | None
    abr: float | None
    filesize: int | None
    filesize_approx: int | None
    note: str
    dynamic_range: str
    protocol: str

    @property
    def has_video(self) -> bool:
        return bool(self.vcodec and self.vcodec != "none")

    @property
    def has_audio(self) -> bool:
        return bool(self.acodec and self.acodec != "none")

    @property
    def kind(self) -> str:
        if self.has_video and self.has_audio:
            return "video+audio"
        if self.has_video:
            return "video"
        if self.has_audio:
            return "audio"
        return "other"

    @property
    def effective_size(self) -> int | None:
        return self.filesize or self.filesize_approx

    @property
    def size_label(self) -> str:
        if self.filesize:
            return format_bytes(self.filesize)
        if self.filesize_approx:
            return f"~{format_bytes(self.filesize_approx)}"
        return "-"

    @property
    def bitrate_label(self) -> str:
        val = self.tbr or self.vbr or self.abr
        if val is None or val <= 0:
            return "-"
        return f"{int(round(val))}k"

    @property
    def res_label(self) -> str:
        if not self.has_video:
            return "audio only"
        if self.height:
            fps_part = f"@{int(round(self.fps))}" if self.fps and self.fps > 0 else ""
            if self.width:
                return f"{self.width}x{self.height}{fps_part}"
            return f"{self.height}p{fps_part}"
        return self.resolution or "video"

    def label(self) -> str:
        """One-line formatted summary for interactive menus."""
        v_str = shorten_codec(self.vcodec) if self.has_video else "-"
        a_str = shorten_codec(self.acodec) if self.has_audio else "-"
        codecs = f"{v_str}+{a_str}" if (self.has_video and self.has_audio) else (
            v_str if self.has_video else a_str
        )
        note_parts = [p for p in (self.note, self.dynamic_range if self.dynamic_range not in ("", "SDR") else "") if p]
        note_str = " ".join(note_parts)
        return (
            f"{self.format_id:<8} {self.ext:<5} {self.res_label:<14} "
            f"{codecs:<11} {self.bitrate_label:>6} {self.size_label:>10}"
            + (f"  {note_str}" if note_str else "")
        )


@dataclass
class PlaylistEntry:
    """Single item in an inspected playlist."""

    index: int
    id: str
    title: str
    duration: float | None
    uploader: str
    url: str


@dataclass
class MediaInfo:
    """Parsed metadata and format listing for a URL."""

    is_playlist: bool
    id: str
    title: str
    uploader: str
    duration: float | None
    upload_date: str
    view_count: int | None
    webpage_url: str
    extractor: str
    chapters_count: int = 0
    subtitles: list[str] = field(default_factory=list)
    automatic_captions: list[str] = field(default_factory=list)
    formats: list[FormatEntry] = field(default_factory=list)
    entries: list[PlaylistEntry] = field(default_factory=list)

    @property
    def combined_formats(self) -> list[FormatEntry]:
        return [f for f in self.formats if f.kind == "video+audio"]

    @property
    def video_formats(self) -> list[FormatEntry]:
        return [f for f in self.formats if f.kind == "video"]

    @property
    def audio_formats(self) -> list[FormatEntry]:
        return [f for f in self.formats if f.kind == "audio"]


def _format_sort_key(fmt: FormatEntry) -> tuple[int, int, float, float]:
    kind_rank = {"video+audio": 3, "video": 2, "audio": 1}.get(fmt.kind, 0)
    height = fmt.height or 0
    fps = fmt.fps or 0.0
    tbr = fmt.tbr or fmt.vbr or fmt.abr or 0.0
    return (kind_rank, height, fps, tbr)


def parse_media_json(data: dict[str, Any]) -> MediaInfo:
    """Parse raw yt-dlp --dump-single-json dictionary into MediaInfo."""
    is_playlist = data.get("_type") in ("playlist", "multi_video") or (
        "entries" in data and not data.get("formats")
    )

    upload_raw = str(data.get("upload_date") or "")
    if len(upload_raw) == 8 and upload_raw.isdigit():
        upload_date = f"{upload_raw[:4]}-{upload_raw[4:6]}-{upload_raw[6:]}"
    else:
        upload_date = upload_raw

    uploader = str(
        data.get("uploader")
        or data.get("channel")
        or data.get("playlist_uploader")
        or "Unknown"
    )

    formats: list[FormatEntry] = []
    for raw_fmt in data.get("formats") or []:
        if not isinstance(raw_fmt, dict):
            continue
        fmt_id = str(raw_fmt.get("format_id") or "").strip()
        if not fmt_id:
            continue
        protocol = str(raw_fmt.get("protocol") or "")
        ext = str(raw_fmt.get("ext") or "")
        # Skip storyboard / mhtml frames
        if ext == "mhtml" or (
            str(raw_fmt.get("vcodec") or "none") == "none"
            and str(raw_fmt.get("acodec") or "none") == "none"
        ):
            continue
        formats.append(
            FormatEntry(
                format_id=fmt_id,
                ext=ext or "?",
                resolution=str(raw_fmt.get("resolution") or ""),
                width=raw_fmt.get("width"),
                height=raw_fmt.get("height"),
                fps=raw_fmt.get("fps"),
                vcodec=str(raw_fmt.get("vcodec") or "none"),
                acodec=str(raw_fmt.get("acodec") or "none"),
                tbr=raw_fmt.get("tbr"),
                vbr=raw_fmt.get("vbr"),
                abr=raw_fmt.get("abr"),
                filesize=raw_fmt.get("filesize"),
                filesize_approx=raw_fmt.get("filesize_approx"),
                note=str(raw_fmt.get("format_note") or ""),
                dynamic_range=str(raw_fmt.get("dynamic_range") or ""),
                protocol=protocol,
            )
        )

    formats.sort(key=_format_sort_key, reverse=True)

    entries: list[PlaylistEntry] = []
    for idx, raw_entry in enumerate(data.get("entries") or [], start=1):
        if not isinstance(raw_entry, dict):
            continue
        entry_url = str(
            raw_entry.get("webpage_url")
            or raw_entry.get("url")
            or ""
        )
        entry_id = str(raw_entry.get("id") or "")
        if not entry_url and entry_id:
            entry_url = entry_id
        entries.append(
            PlaylistEntry(
                index=idx,
                id=entry_id,
                title=str(raw_entry.get("title") or f"Item {idx}"),
                duration=raw_entry.get("duration"),
                uploader=str(raw_entry.get("uploader") or raw_entry.get("channel") or ""),
                url=entry_url,
            )
        )

    subs = sorted(k for k in (data.get("subtitles") or {}).keys() if k != "live_chat")
    auto_subs = sorted((data.get("automatic_captions") or {}).keys())
    chapters = data.get("chapters") or []

    return MediaInfo(
        is_playlist=bool(is_playlist),
        id=str(data.get("id") or ""),
        title=str(data.get("title") or "Untitled"),
        uploader=uploader,
        duration=data.get("duration"),
        upload_date=upload_date,
        view_count=data.get("view_count"),
        webpage_url=str(data.get("webpage_url") or data.get("original_url") or ""),
        extractor=str(data.get("extractor_key") or data.get("extractor") or ""),
        chapters_count=len(chapters) if isinstance(chapters, list) else 0,
        subtitles=subs,
        automatic_captions=auto_subs,
        formats=formats,
        entries=entries,
    )


class InspectError(RuntimeError):
    """Raised when yt-dlp metadata inspection fails."""


def inspect_url(
    config: DownloadConfig,
    url: str,
    *,
    flat_playlist: bool = True,
    timeout: int = 45,
) -> MediaInfo:
    """Invoke yt-dlp to retrieve JSON metadata and formats for a URL."""
    ytdlp_bin = shutil.which("yt-dlp")
    if not ytdlp_bin:
        raise InspectError("yt-dlp executable was not found in PATH.")

    cmd = build_inspect_command(
        config,
        url,
        executable=ytdlp_bin,
        flat_playlist=flat_playlist,
    )
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise InspectError(f"Metadata inspection timed out after {timeout}s.") from exc
    except OSError as exc:
        raise InspectError(f"Failed to execute yt-dlp: {exc}") from exc

    if proc.returncode != 0:
        err_lines = [line.strip() for line in proc.stderr.splitlines() if line.strip()]
        msg = err_lines[-1] if err_lines else f"yt-dlp exited with status {proc.returncode}"
        raise InspectError(msg)

    stdout = proc.stdout.strip()
    if not stdout:
        raise InspectError("yt-dlp returned empty metadata output.")

    try:
        parsed = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise InspectError(f"Could not parse yt-dlp JSON metadata: {exc}") from exc

    if not isinstance(parsed, dict):
        raise InspectError("Unexpected metadata payload from yt-dlp.")

    return parse_media_json(parsed)
