"""Configuration models, built-in presets, and XDG persistence for Yt-Dlp Helper."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any


DEFAULT_OUTPUT_DIR = "~/Downloads"
DEFAULT_OUTPUT_TEMPLATE = "%(title)s [%(id)s].%(ext)s"

OUTPUT_TEMPLATE_PRESETS: list[tuple[str, str, str]] = [
    ("Title + ID (Default)", "%(title)s [%(id)s].%(ext)s", "Prevents filename collisions across videos"),
    ("Clean Title", "%(title)s.%(ext)s", "Simple title.ext without video ID"),
    ("Uploader / Title", "%(uploader)s/%(title)s [%(id)s].%(ext)s", "Groups downloads into channel folders"),
    (
        "Playlist Indexed",
        "%(playlist_title)s/%(playlist_index)03d - %(title)s [%(id)s].%(ext)s",
        "Numbered tracks inside a playlist folder",
    ),
    (
        "Date + Title",
        "%(upload_date>%Y-%m-%d)s - %(title)s [%(id)s].%(ext)s",
        "Prefixes upload date (YYYY-MM-DD)",
    ),
    (
        "Audio Artist - Title",
        "%(artist,uploader)s - %(title)s.%(ext)s",
        "Ideal for music and podcasts",
    ),
]


@dataclass
class DownloadConfig:
    """Complete user configuration state translated into yt-dlp CLI arguments."""

    preset_name: str = "Best Video (MP4)"
    mode: str = "video"  # "video" | "audio"
    format_selector: str = "bv*+ba/b"
    format_sort: str = ""
    merge_output_format: str = "mp4"  # "", "mp4", "mkv", "webm"
    remux_video: str = ""
    recode_video: str = ""

    audio_format: str = "mp3"  # "mp3", "m4a", "aac", "opus", "flac", "wav", "best"
    audio_quality: str = "0"  # "0" (best) .. "10", or bitrate like "320K"

    output_dir: str = DEFAULT_OUTPUT_DIR
    output_template: str = DEFAULT_OUTPUT_TEMPLATE
    restrict_filenames: bool = False
    windows_filenames: bool = False
    trim_filenames: int = 0
    no_overwrites: bool = False

    playlist_mode: str = "auto"  # "auto", "single", "playlist"
    playlist_items: str = ""
    download_archive: str = ""
    ignore_errors: bool = False

    write_subs: bool = False
    write_auto_subs: bool = False
    sub_langs: str = "en.*,en"
    embed_subs: bool = False

    embed_thumbnail: bool = False
    embed_metadata: bool = True
    embed_chapters: bool = True

    sponsorblock_mode: str = "none"  # "none", "mark", "remove"
    sponsorblock_cats: str = "sponsor,selfpromo,interaction"

    download_sections: str = ""
    split_chapters: bool = False

    cookies_from_browser: str = ""
    cookies_file: str = ""
    concurrent_fragments: int = 4
    rate_limit: str = ""
    proxy: str = ""
    extra_args: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize configuration to a JSON-compatible dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> DownloadConfig:
        """Create a DownloadConfig safely from a dictionary, ignoring unknown keys."""
        if not isinstance(data, dict):
            return cls()
        defaults = cls()
        valid_fields = {f.name: f for f in fields(cls)}
        kwargs: dict[str, Any] = {}
        for key, value in data.items():
            if key not in valid_fields:
                continue
            default_val = getattr(defaults, key)
            if isinstance(default_val, bool):
                kwargs[key] = bool(value)
            elif isinstance(default_val, int):
                try:
                    kwargs[key] = int(value)
                except (TypeError, ValueError):
                    kwargs[key] = default_val
            elif isinstance(default_val, str):
                kwargs[key] = str(value) if value is not None else ""
        cfg = cls(**kwargs)
        if cfg.mode not in ("video", "audio"):
            cfg.mode = "video"
        if cfg.playlist_mode not in ("auto", "single", "playlist"):
            cfg.playlist_mode = "auto"
        if cfg.sponsorblock_mode not in ("none", "mark", "remove"):
            cfg.sponsorblock_mode = "none"
        if cfg.concurrent_fragments < 1:
            cfg.concurrent_fragments = 1
        if cfg.trim_filenames < 0:
            cfg.trim_filenames = 0
        return cfg

    def summary_features(self) -> list[str]:
        """Return concise labels describing enabled features."""
        items: list[str] = []
        if self.embed_metadata:
            items.append("Metadata")
        if self.embed_thumbnail:
            items.append("Thumbnail")
        if self.embed_chapters and self.mode == "video":
            items.append("Chapters")
        if self.write_subs or self.write_auto_subs:
            sub_kind = "Auto+Subs" if (self.write_subs and self.write_auto_subs) else (
                "AutoSubs" if self.write_auto_subs else "Subs"
            )
            if self.embed_subs and self.mode == "video":
                sub_kind += "+Embed"
            items.append(f"{sub_kind}({self.sub_langs or 'all'})")
        if self.sponsorblock_mode != "none":
            items.append(f"SponsorBlock:{self.sponsorblock_mode}")
        if self.download_sections:
            items.append(f"Clip({self.download_sections})")
        if self.split_chapters:
            items.append("SplitChapters")
        return items


@dataclass(frozen=True)
class Preset:
    """Named preset configuration."""

    key: str
    name: str
    description: str
    overrides: dict[str, Any]


PRESET_SCOPED_DEFAULTS: dict[str, Any] = {
    "mode": "video",
    "format_selector": "bv*+ba/b",
    "format_sort": "",
    "merge_output_format": "",
    "remux_video": "",
    "recode_video": "",
    "audio_format": "mp3",
    "audio_quality": "0",
    "write_subs": False,
    "write_auto_subs": False,
    "embed_subs": False,
    "embed_thumbnail": False,
    "embed_metadata": True,
    "embed_chapters": True,
}


BUILTIN_PRESETS: list[Preset] = [
    Preset(
        key="mp4",
        name="Best Video (MP4)",
        description="Highest quality video + audio merged into MP4 container",
        overrides={
            "mode": "video",
            "format_selector": "bv*+ba/b",
            "merge_output_format": "mp4",
            "embed_metadata": True,
            "embed_chapters": True,
        },
    ),
    Preset(
        key="compat-mp4",
        name="Compatible MP4 (H.264/AAC)",
        description="Universal playback MP4 (H.264 video + AAC audio, remuxed)",
        overrides={
            "mode": "video",
            "format_selector": "bv*+ba/b",
            "format_sort": "vcodec:h264,lang,quality,res,fps,hdr:12,acodec:aac",
            "merge_output_format": "mp4",
            "remux_video": "mp4",
            "embed_metadata": True,
            "embed_chapters": True,
        },
    ),
    Preset(
        key="mkv",
        name="Best Quality Native (MKV)",
        description="Highest native codecs (AV1/VP9/Opus) merged into MKV without re-encoding",
        overrides={
            "mode": "video",
            "format_selector": "bv*+ba/b",
            "merge_output_format": "mkv",
            "embed_metadata": True,
            "embed_chapters": True,
        },
    ),
    Preset(
        key="1080p",
        name="1080p MP4",
        description="Best video capped at 1080p in MP4 container",
        overrides={
            "mode": "video",
            "format_selector": "bv*[height<=1080]+ba/b[height<=1080]/b",
            "merge_output_format": "mp4",
            "embed_metadata": True,
            "embed_chapters": True,
        },
    ),
    Preset(
        key="720p",
        name="720p MP4",
        description="Best video capped at 720p in MP4 container (smaller files)",
        overrides={
            "mode": "video",
            "format_selector": "bv*[height<=720]+ba/b[height<=720]/b",
            "merge_output_format": "mp4",
            "embed_metadata": True,
            "embed_chapters": True,
        },
    ),
    Preset(
        key="mp3",
        name="Audio Only (MP3)",
        description="Extract best audio to high-quality VBR MP3 with cover art & metadata",
        overrides={
            "mode": "audio",
            "format_selector": "ba/b",
            "audio_format": "mp3",
            "audio_quality": "0",
            "embed_metadata": True,
            "embed_thumbnail": True,
            "embed_chapters": False,
        },
    ),
    Preset(
        key="m4a",
        name="Audio Only (M4A / AAC)",
        description="Extract best audio to M4A/AAC with cover art & metadata",
        overrides={
            "mode": "audio",
            "format_selector": "ba/b",
            "audio_format": "m4a",
            "audio_quality": "0",
            "embed_metadata": True,
            "embed_thumbnail": True,
            "embed_chapters": False,
        },
    ),
    Preset(
        key="opus",
        name="Audio Only (Opus)",
        description="Extract high-efficiency Opus audio with cover art & metadata",
        overrides={
            "mode": "audio",
            "format_selector": "ba/b",
            "audio_format": "opus",
            "audio_quality": "0",
            "embed_metadata": True,
            "embed_thumbnail": True,
            "embed_chapters": False,
        },
    ),
    Preset(
        key="flac",
        name="Audio Only (FLAC)",
        description="Convert best audio to lossless FLAC with cover art & metadata",
        overrides={
            "mode": "audio",
            "format_selector": "ba/b",
            "audio_format": "flac",
            "audio_quality": "0",
            "embed_metadata": True,
            "embed_thumbnail": True,
            "embed_chapters": False,
        },
    ),
    Preset(
        key="archival",
        name="Archival (MKV + Subs + Meta)",
        description="Best quality MKV with embedded subtitles, thumbnail, metadata & chapters",
        overrides={
            "mode": "video",
            "format_selector": "bv*+ba/b",
            "merge_output_format": "mkv",
            "write_subs": True,
            "embed_subs": True,
            "embed_thumbnail": True,
            "embed_metadata": True,
            "embed_chapters": True,
        },
    ),
]


def find_builtin_preset(identifier: str) -> Preset | None:
    """Find a built-in preset by key or display name (case-insensitive)."""
    needle = identifier.strip().lower()
    if not needle:
        return None
    for preset in BUILTIN_PRESETS:
        if preset.key.lower() == needle or preset.name.lower() == needle:
            return preset
    return None


def apply_builtin_preset(config: DownloadConfig, preset: Preset) -> DownloadConfig:
    """Apply a built-in preset while preserving user path/auth/playlist preferences."""
    data = config.to_dict()
    for field_name, default_val in PRESET_SCOPED_DEFAULTS.items():
        data[field_name] = default_val
    for field_name, override_val in preset.overrides.items():
        data[field_name] = override_val
    data["preset_name"] = preset.name
    return DownloadConfig.from_dict(data)


@dataclass
class HistoryEntry:
    """Single historical download or dry-run entry."""

    timestamp: str
    urls: list[str]
    preset_name: str
    command: list[str]
    output_dir: str
    exit_code: int
    titles: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HistoryEntry | None:
        if not isinstance(data, dict):
            return None
        urls = [str(u) for u in data.get("urls", []) if u]
        command = [str(c) for c in data.get("command", [])]
        if not urls and not command:
            return None
        return cls(
            timestamp=str(data.get("timestamp", "")),
            urls=urls,
            preset_name=str(data.get("preset_name", "")),
            command=command,
            output_dir=str(data.get("output_dir", "")),
            exit_code=int(data.get("exit_code", 0)),
            titles=[str(t) for t in data.get("titles", []) if t],
        )


def get_config_dir() -> Path:
    """Resolve configuration directory respecting YT_DLP_HELPER_CONFIG_DIR and XDG_CONFIG_HOME."""
    override = os.environ.get("YT_DLP_HELPER_CONFIG_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return base / "yt-dlp-helper"


class ConfigStore:
    """Handles persistence of active settings, custom presets, and download history."""

    def __init__(self, config_dir: Path | None = None) -> None:
        self.config_dir = config_dir or get_config_dir()
        self.config_path = self.config_dir / "config.json"
        self.history_path = self.config_dir / "history.json"

    def load(self) -> tuple[DownloadConfig, dict[str, DownloadConfig]]:
        """Load active configuration and saved custom presets from disk."""
        if not self.config_path.exists():
            return DownloadConfig(), {}
        try:
            raw = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return DownloadConfig(), {}
        if not isinstance(raw, dict):
            return DownloadConfig(), {}

        active = DownloadConfig.from_dict(raw.get("active_config"))
        custom_presets: dict[str, DownloadConfig] = {}
        raw_presets = raw.get("custom_presets")
        if isinstance(raw_presets, dict):
            for name, preset_dict in raw_presets.items():
                if isinstance(name, str) and name.strip() and isinstance(preset_dict, dict):
                    cfg = DownloadConfig.from_dict(preset_dict)
                    cfg.preset_name = name
                    custom_presets[name] = cfg
        return active, custom_presets

    def save(
        self,
        active_config: DownloadConfig,
        custom_presets: dict[str, DownloadConfig],
    ) -> None:
        """Persist active configuration and custom presets atomically."""
        self.config_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "active_config": active_config.to_dict(),
            "custom_presets": {
                name: cfg.to_dict() for name, cfg in custom_presets.items()
            },
        }
        tmp_path = self.config_path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        tmp_path.replace(self.config_path)

    def load_history(self, limit: int = 50) -> list[HistoryEntry]:
        """Load recent history entries (newest first)."""
        if not self.history_path.exists():
            return []
        try:
            raw = json.loads(self.history_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        if not isinstance(raw, list):
            return []
        entries: list[HistoryEntry] = []
        for item in raw:
            entry = HistoryEntry.from_dict(item)
            if entry is not None:
                entries.append(entry)
        return entries[:limit]

    def append_history(
        self,
        urls: list[str],
        config: DownloadConfig,
        command: list[str],
        exit_code: int,
        titles: list[str] | None = None,
        max_entries: int = 50,
    ) -> None:
        """Record a download execution in persistent history."""
        entries = self.load_history(limit=max_entries)
        new_entry = HistoryEntry(
            timestamp=datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S"),
            urls=list(urls),
            preset_name=config.preset_name,
            command=list(command),
            output_dir=os.path.expanduser(config.output_dir),
            exit_code=exit_code,
            titles=list(titles or []),
        )
        updated = [new_entry, *entries][:max_entries]
        self.config_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = self.history_path.with_suffix(".json.tmp")
        tmp_path.write_text(
            json.dumps([e.to_dict() for e in updated], indent=2) + "\n",
            encoding="utf-8",
        )
        tmp_path.replace(self.history_path)

    def clear_history(self) -> None:
        """Remove the history file if it exists."""
        try:
            self.history_path.unlink(missing_ok=True)
        except OSError:
            pass
