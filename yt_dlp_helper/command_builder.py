"""Deterministic yt-dlp command construction from DownloadConfig."""

from __future__ import annotations

import os
import shlex

from .config import DownloadConfig


def _expand_path(path: str) -> str:
    return os.path.expanduser(path.strip())


def parse_extra_args(raw: str) -> list[str]:
    """Parse user-provided extra CLI flags using shell quoting rules."""
    cleaned = raw.strip()
    if not cleaned:
        return []
    try:
        return shlex.split(cleaned)
    except ValueError as exc:
        raise ValueError(f"Invalid syntax in extra arguments: {exc}") from exc


def build_command(
    config: DownloadConfig,
    urls: list[str],
    *,
    executable: str = "yt-dlp",
    simulate: bool = False,
    batch_file: str | None = None,
    extra_override_args: list[str] | None = None,
    allow_empty_targets: bool = False,
) -> list[str]:
    """Build a complete yt-dlp command list from a DownloadConfig and target URLs."""
    cleaned_urls = [u.strip() for u in urls if u and u.strip()]
    cleaned_batch = _expand_path(batch_file) if batch_file and batch_file.strip() else ""

    if not allow_empty_targets and not cleaned_urls and not cleaned_batch:
        raise ValueError("At least one URL or batch file is required.")

    cmd: list[str] = [executable]

    # Mode and format selection
    if config.mode == "audio":
        selector = config.format_selector.strip()
        if not selector or selector.startswith("bv"):
            selector = "ba/b"
        cmd.extend(["-f", selector])
        cmd.append("-x")
        if config.audio_format and config.audio_format != "best":
            cmd.extend(["--audio-format", config.audio_format])
        if config.audio_quality.strip():
            cmd.extend(["--audio-quality", config.audio_quality.strip()])
    else:
        if config.format_selector.strip():
            cmd.extend(["-f", config.format_selector.strip()])
        if config.format_sort.strip():
            cmd.extend(["-S", config.format_sort.strip()])
        if config.merge_output_format.strip():
            cmd.extend(["--merge-output-format", config.merge_output_format.strip()])
        if config.remux_video.strip():
            cmd.extend(["--remux-video", config.remux_video.strip()])
        if config.recode_video.strip():
            cmd.extend(["--recode-video", config.recode_video.strip()])

    # Output path & filename template
    if config.output_dir.strip():
        cmd.extend(["-P", _expand_path(config.output_dir)])
    if config.output_template.strip():
        cmd.extend(["-o", config.output_template.strip()])
    if config.restrict_filenames:
        cmd.append("--restrict-filenames")
    if config.windows_filenames:
        cmd.append("--windows-filenames")
    if config.trim_filenames > 0:
        cmd.extend(["--trim-filenames", str(config.trim_filenames)])
    if config.no_overwrites:
        cmd.append("--no-overwrites")

    # Playlist & archive controls
    if config.playlist_mode == "single":
        cmd.append("--no-playlist")
    elif config.playlist_mode == "playlist":
        cmd.append("--yes-playlist")

    if config.playlist_items.strip() and config.playlist_mode != "single":
        cmd.extend(["-I", config.playlist_items.strip()])
    if config.download_archive.strip():
        cmd.extend(["--download-archive", _expand_path(config.download_archive)])
    if config.ignore_errors:
        cmd.append("-i")

    # Subtitles
    write_subs = config.write_subs
    if config.embed_subs and config.mode == "video" and not (write_subs or config.write_auto_subs):
        # Embedding subtitles requires downloading subtitles first.
        write_subs = True

    if write_subs:
        cmd.append("--write-subs")
    if config.write_auto_subs:
        cmd.append("--write-auto-subs")
    if config.embed_subs and config.mode == "video":
        cmd.append("--embed-subs")
    if (write_subs or config.write_auto_subs or (config.embed_subs and config.mode == "video")) and config.sub_langs.strip():
        cmd.extend(["--sub-langs", config.sub_langs.strip()])

    # Metadata, thumbnails, chapters, SponsorBlock, clipping
    if config.embed_metadata:
        cmd.append("--embed-metadata")
    if config.embed_thumbnail:
        cmd.append("--embed-thumbnail")
    if config.embed_chapters and config.mode == "video":
        cmd.append("--embed-chapters")

    if config.sponsorblock_mode == "mark" and config.sponsorblock_cats.strip():
        cmd.extend(["--sponsorblock-mark", config.sponsorblock_cats.strip()])
    elif config.sponsorblock_mode == "remove" and config.sponsorblock_cats.strip():
        cmd.extend(["--sponsorblock-remove", config.sponsorblock_cats.strip()])

    if config.download_sections.strip():
        cmd.extend(["--download-sections", config.download_sections.strip()])
    if config.split_chapters:
        cmd.append("--split-chapters")

    # Network & Authentication
    if config.cookies_from_browser.strip():
        cmd.extend(["--cookies-from-browser", config.cookies_from_browser.strip()])
    elif config.cookies_file.strip():
        cmd.extend(["--cookies", _expand_path(config.cookies_file)])

    if config.concurrent_fragments > 1:
        cmd.extend(["-N", str(config.concurrent_fragments)])
    if config.rate_limit.strip():
        cmd.extend(["-r", config.rate_limit.strip()])
    if config.proxy.strip():
        cmd.extend(["--proxy", config.proxy.strip()])

    if simulate:
        cmd.append("--simulate")

    # Extra user arguments
    if config.extra_args.strip():
        cmd.extend(parse_extra_args(config.extra_args))
    if extra_override_args:
        cmd.extend(extra_override_args)

    if cleaned_batch:
        cmd.extend(["-a", cleaned_batch])

    if cleaned_urls:
        cmd.append("--")
        cmd.extend(cleaned_urls)

    return cmd


def build_inspect_command(
    config: DownloadConfig,
    url: str,
    *,
    executable: str = "yt-dlp",
    flat_playlist: bool = True,
) -> list[str]:
    """Build a yt-dlp command to fetch JSON metadata and formats for a URL."""
    cleaned_url = url.strip()
    if not cleaned_url:
        raise ValueError("URL cannot be empty.")

    cmd = [executable, "--dump-single-json", "--no-warnings"]
    if flat_playlist:
        cmd.append("--flat-playlist")

    if config.playlist_mode == "single":
        cmd.append("--no-playlist")
    elif config.playlist_mode == "playlist":
        cmd.append("--yes-playlist")

    if config.cookies_from_browser.strip():
        cmd.extend(["--cookies-from-browser", config.cookies_from_browser.strip()])
    elif config.cookies_file.strip():
        cmd.extend(["--cookies", _expand_path(config.cookies_file)])

    if config.proxy.strip():
        cmd.extend(["--proxy", config.proxy.strip()])

    cmd.extend(["--", cleaned_url])
    return cmd


def format_command_preview(argv: list[str]) -> str:
    """Format an argument list as a copy-pasteable shell command string."""
    return shlex.join(argv)
