"""CLI entry point and interactive terminal menus for Yt-Dlp Helper."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
from typing import Sequence

from . import __version__
from .command_builder import (
    build_command,
    format_command_preview,
    parse_extra_args,
)
from .config import (
    BUILTIN_PRESETS,
    ConfigStore,
    DownloadConfig,
    OUTPUT_TEMPLATE_PRESETS,
    apply_builtin_preset,
    find_builtin_preset,
)
from .inspector import (
    FormatEntry,
    InspectError,
    MediaInfo,
    format_duration,
    inspect_url,
)
from .runner import (
    EnvironmentStatus,
    check_environment,
    run_ytdlp,
)
from .ui import MenuOption, ProgressDisplay, TerminalUI


_SEARCH_PREFIXES = (
    "ytsearch",
    "ytsearchdate",
    "yvsearch",
    "scsearch",
    "gvsearch",
    "nicosearch",
    "bilisearch",
)


def looks_like_url_or_search(target: str) -> bool:
    """Check if target looks like a URL or an explicit yt-dlp search prefix."""
    t = target.strip().lower()
    if "://" in t:
        return True
    return any(t.startswith(prefix + ":") or re_digit_search(t, prefix) for prefix in _SEARCH_PREFIXES)


def re_digit_search(text: str, prefix: str) -> bool:
    if not text.startswith(prefix):
        return False
    rest = text[len(prefix):]
    if ":" not in rest:
        return False
    count_part = rest.split(":", 1)[0]
    return count_part == "" or count_part == "all" or count_part.isdigit()


def load_batch_urls(path_str: str) -> list[str]:
    """Read URLs from a text file, ignoring blank lines and `#` comments."""
    path = Path(os.path.expanduser(path_str.strip()))
    lines = path.read_text(encoding="utf-8").splitlines()
    urls: list[str] = []
    for line in lines:
        cleaned = line.strip()
        if cleaned and not cleaned.startswith("#"):
            urls.append(cleaned)
    return urls


def print_doctor_report(ui: TerminalUI, env: EnvironmentStatus, store: ConfigStore) -> None:
    """Render an environment and dependency diagnostic report."""
    lines = [
        f"yt-dlp      : {ui.style.ok(env.yt_dlp_version + ' (' + env.yt_dlp_path + ')') if env.yt_dlp_path else ui.style.err('NOT FOUND')}",
        f"ffmpeg      : {ui.style.ok(env.ffmpeg_version + ' (' + env.ffmpeg_path + ')') if env.ffmpeg_path else ui.style.warn('Missing (required for merging/audio extraction)')}",
        f"ffprobe     : {ui.style.ok(env.ffprobe_path) if env.ffprobe_path else ui.style.warn('Missing')}",
        f"JS runtimes : {ui.style.ok(', '.join(f'{k} ({v})' for k, v in env.js_runtimes.items())) if env.js_runtimes else ui.style.dim('None detected (optional: deno/node/bun/quickjs)')}",
        f"Config dir  : {store.config_dir}",
    ]
    ui.print_panel("System Doctor", lines, border="cyan")


def print_media_info_summary(ui: TerminalUI, info: MediaInfo) -> None:
    """Print structured metadata summary for an inspected video or playlist."""
    if info.is_playlist:
        lines = [
            f"{ui.style.dim('Type     :')} Playlist ({len(info.entries)} items)",
            f"{ui.style.dim('Title    :')} {info.title}",
            f"{ui.style.dim('Channel  :')} {info.uploader}",
            f"{ui.style.dim('Extractor:')} {info.extractor or 'Unknown'}",
        ]
        ui.print_panel("Playlist Metadata", lines, border="cyan")
        return

    views = f"{info.view_count:,}" if isinstance(info.view_count, int) else "Unknown"
    subs_str = ", ".join(info.subtitles[:10]) if info.subtitles else "None"
    if len(info.subtitles) > 10:
        subs_str += f" (+{len(info.subtitles) - 10} more)"
    auto_str = f"{len(info.automatic_captions)} languages" if info.automatic_captions else "None"

    lines = [
        f"{ui.style.dim('Title    :')} {ui.style.bold(info.title)}",
        f"{ui.style.dim('Channel  :')} {info.uploader}   {ui.style.dim('ID:')} {info.id}",
        f"{ui.style.dim('Duration :')} {format_duration(info.duration)}   {ui.style.dim('Date:')} {info.upload_date or 'Unknown'}   {ui.style.dim('Views:')} {views}",
        f"{ui.style.dim('Chapters :')} {info.chapters_count}   {ui.style.dim('Subtitles:')} {subs_str}   {ui.style.dim('Auto-caps:')} {auto_str}",
        f"{ui.style.dim('Formats  :')} {len(info.combined_formats)} combined, {len(info.video_formats)} video-only, {len(info.audio_formats)} audio-only",
    ]
    ui.print_panel("Media Metadata", lines, border="cyan")


def print_formats_table(ui: TerminalUI, info: MediaInfo) -> None:
    """Print categorized format listings in plain terminal output."""
    sections = [
        ("Combined (Video + Audio)", info.combined_formats),
        ("Video Only", info.video_formats),
        ("Audio Only", info.audio_formats),
    ]
    header = f"{'ID':<8} {'EXT':<5} {'RESOLUTION':<14} {'CODEC':<11} {'RATE':>6} {'SIZE':>10}  NOTE"
    for title, items in sections:
        if not items:
            continue
        print(f"\n{ui.style.accent(title)}")
        print(ui.style.dim(header))
        print(ui.style.dim("-" * len(header)))
        for fmt in items:
            print(fmt.label())


class App:
    """Interactive Yt-Dlp Helper terminal application."""

    def __init__(
        self,
        *,
        store: ConfigStore | None = None,
        ui: TerminalUI | None = None,
        verbose: bool = False,
    ) -> None:
        self.store = store or ConfigStore()
        self.ui = ui or TerminalUI()
        self.verbose = verbose
        self.config, self.custom_presets = self.store.load()
        self.env = check_environment()
        self.queue: list[str] = []
        self.last_url: str = ""

    def save_state(self) -> None:
        self.store.save(self.config, self.custom_presets)

    def _refresh_header(self) -> None:
        self.ui.clear_screen()
        self.ui.print_banner(__version__, self.env, self.config)
        print()

    def _warn_if_missing_tools(self, config: DownloadConfig) -> bool:
        if not self.env.yt_dlp_path:
            self.ui.error("yt-dlp was not found in PATH. Please install yt-dlp first.")
            return False
        needs_ffmpeg = (
            config.mode == "audio"
            or "+" in config.format_selector
            or bool(config.merge_output_format)
            or bool(config.remux_video)
            or bool(config.recode_video)
            or config.embed_subs
            or config.embed_thumbnail
            or config.embed_metadata
            or bool(config.download_sections)
            or config.split_chapters
        )
        if needs_ffmpeg and not self.env.ffmpeg_path:
            self.ui.warn(
                "ffmpeg is not installed; merging streams, audio extraction, or metadata embedding may fail."
            )
        return True

    def _resolve_target_input(self, raw_input: str) -> list[str] | None:
        """Convert user input into one or more URLs/search specs (offering YouTube search if plain text)."""
        tokens = raw_input.strip().split()
        if not tokens:
            return None
        if len(tokens) == 1 and not looks_like_url_or_search(tokens[0]):
            query = raw_input.strip()
            choice = self.ui.select_menu(
                f"'{query}' does not look like a URL. How should yt-dlp handle it?",
                [
                    MenuOption("Search YouTube and pick from top 5 results", "search5", hotkey="1"),
                    MenuOption("Download top 1 YouTube search match (ytsearch1:)", "search1", hotkey="2"),
                    MenuOption("Pass raw text to yt-dlp anyway", "raw", hotkey="3"),
                ],
            )
            if choice is None:
                return None
            if choice == "search5":
                return [f"ytsearch5:{query}"]
            if choice == "search1":
                return [f"ytsearch1:{query}"]
            return [query]
        return tokens

    def _execute_download(
        self,
        urls: list[str],
        *,
        simulate: bool = False,
        batch_file: str | None = None,
        extra_override_args: list[str] | None = None,
        confirm_before_run: bool = True,
    ) -> int:
        if not self._warn_if_missing_tools(self.config):
            return 127

        try:
            cmd = build_command(
                self.config,
                urls,
                simulate=simulate,
                batch_file=batch_file,
                extra_override_args=extra_override_args,
            )
        except ValueError as exc:
            self.ui.error(str(exc))
            return 2

        preview = format_command_preview(cmd)
        print()
        self.ui.print_panel(
            "Dry-Run Command" if simulate else "yt-dlp Command",
            [preview],
            border="yellow" if simulate else "cyan",
            wrap=True,
        )

        if confirm_before_run:
            action = "Run simulation?" if simulate else "Start download?"
            if not self.ui.confirm(action, default=True):
                self.ui.info("Cancelled.")
                return 0

        print()
        display = ProgressDisplay(self.ui, verbose=self.verbose)
        result = run_ytdlp(
            cmd,
            on_progress=display.handle_progress,
            on_status=display.handle_status,
        )
        display.finalize()

        print()
        if result.cancelled:
            self.ui.warn(f"Download cancelled after {result.elapsed_seconds:.1f}s.")
        elif result.exit_code == 0:
            self.ui.success(
                f"{'Simulation' if simulate else 'Download'} finished in {result.elapsed_seconds:.1f}s."
            )
        else:
            self.ui.error(
                f"yt-dlp exited with code {result.exit_code} after {result.elapsed_seconds:.1f}s."
            )
            if any("cookie" in e.lower() or "sign in" in e.lower() or "403" in e for e in result.errors):
                self.ui.info(
                    "Tip: Configure browser cookies under 'Network, Cookies & Extra Flags' if authentication is required."
                )

        if result.destinations:
            print(self.ui.style.dim("Output files:"))
            for dest in result.destinations[-6:]:
                print(f"  {self.ui.style.ok('•')} {dest}")

        if not simulate and urls:
            self.store.append_history(
                urls=urls,
                config=self.config,
                command=cmd,
                exit_code=result.exit_code,
            )
        return result.exit_code

    def run_interactive(self, initial_urls: list[str] | None = None) -> int:
        """Run the main interactive CLI loop."""
        if initial_urls:
            self.last_url = initial_urls[0]
            cont = self._handle_initial_urls(initial_urls)
            if not cont:
                return 0

        while True:
            self._refresh_header()
            options = [
                MenuOption(
                    "Download URL / Search",
                    "download",
                    "Download a video, playlist, or YouTube search",
                    hotkey="1",
                ),
                MenuOption(
                    "Inspect Formats & Metadata",
                    "inspect",
                    "List resolutions, codecs, file sizes & pair streams",
                    hotkey="2",
                ),
                MenuOption(
                    "Switch Quality / Format Preset",
                    "presets",
                    f"Active: {self.config.preset_name}",
                    hotkey="3",
                ),
                MenuOption(
                    "Batch Download & Queue",
                    "batch",
                    f"{len(self.queue)} queued URL(s)" if self.queue else "Queue multiple URLs or .txt file",
                    hotkey="4",
                ),
                MenuOption(
                    "Output Folder & Filename Template",
                    "output",
                    f"{self.config.output_dir}",
                    hotkey="5",
                ),
                MenuOption(
                    "Subtitles, Metadata & SponsorBlock",
                    "post",
                    "Embed subs, cover art, metadata, chapters, audio quality",
                    hotkey="6",
                ),
                MenuOption(
                    "Playlist, Clips & Archive",
                    "playlist",
                    f"Playlist: {self.config.playlist_mode}"
                    + (f" | Clip: {self.config.download_sections}" if self.config.download_sections else ""),
                    hotkey="7",
                ),
                MenuOption(
                    "Network, Cookies & Extra Flags",
                    "network",
                    f"Cookies: {self.config.cookies_from_browser or ('file' if self.config.cookies_file else 'none')}",
                    hotkey="8",
                ),
                MenuOption(
                    "Command Preview, History & Tools",
                    "tools",
                    "Preview command, dry-run, history, system doctor",
                    hotkey="9",
                ),
                MenuOption("Exit", "exit", "Quit Yt-Dlp Helper", hotkey="0"),
            ]

            try:
                choice = self.ui.select_menu(
                    "Main Menu",
                    options,
                    allow_cancel=True,
                )
            except KeyboardInterrupt:
                print()
                return 0

            if choice in (None, "exit"):
                return 0

            try:
                if choice == "download":
                    self._menu_download()
                elif choice == "inspect":
                    self._menu_inspect()
                elif choice == "presets":
                    self._menu_presets()
                elif choice == "batch":
                    self._menu_batch()
                elif choice == "output":
                    self._menu_output()
                elif choice == "post":
                    self._menu_postprocess()
                elif choice == "playlist":
                    self._menu_playlist_clips()
                elif choice == "network":
                    self._menu_network()
                elif choice == "tools":
                    self._menu_tools()
            except KeyboardInterrupt:
                print()
                self.ui.warn("Action cancelled.")
                self.ui.pause()

    def _handle_initial_urls(self, urls: list[str]) -> bool:
        """Handle URLs supplied as CLI arguments when opening interactively."""
        self._refresh_header()
        self.ui.print_panel("Target URL(s)", urls, border="cyan")
        choice = self.ui.select_menu(
            "What would you like to do with the provided URL(s)?",
            [
                MenuOption("Download now with active preset", "download", hotkey="1"),
                MenuOption("Inspect formats & pick exact quality", "inspect", hotkey="2"),
                MenuOption("Switch preset first", "preset", hotkey="3"),
                MenuOption("Open full main menu", "menu", hotkey="4"),
                MenuOption("Exit", "exit", hotkey="0"),
            ],
        )
        if choice in (None, "exit"):
            return False
        if choice == "download":
            self._execute_download(urls, confirm_before_run=False)
            self.ui.pause()
            return self.ui.confirm("Return to main menu?", default=True)
        if choice == "inspect":
            self._inspect_interactive(urls[0])
            return True
        if choice == "preset":
            self._menu_presets()
            if self.ui.confirm("Download provided URL(s) with newly selected preset?", default=True):
                self._execute_download(urls, confirm_before_run=False)
                self.ui.pause()
            return True
        return True

    def _menu_download(self) -> None:
        self._refresh_header()
        raw = self.ui.prompt_text(
            "Enter URL(s) or YouTube search query",
            default=self.last_url,
            strip_quotes=True,
        )
        if not raw:
            return
        targets = self._resolve_target_input(raw)
        if not targets:
            return
        self.last_url = targets[0]

        # If search5 was chosen, jump into inspector so user can pick a search result!
        if len(targets) == 1 and targets[0].startswith("ytsearch5:"):
            self._inspect_interactive(targets[0])
            return

        self._execute_download(targets)
        self.ui.pause()

    def _menu_inspect(self) -> None:
        self._refresh_header()
        url = self.ui.prompt_text(
            "Enter URL (or ytsearch5:<query>) to inspect",
            default=self.last_url,
            strip_quotes=True,
        )
        if not url:
            return
        resolved = self._resolve_target_input(url)
        if not resolved:
            return
        self.last_url = resolved[0]
        self._inspect_interactive(resolved[0])

    def _inspect_interactive(self, url: str) -> None:
        self.ui.info(f"Fetching metadata for {url} ...")
        try:
            info = inspect_url(self.config, url, flat_playlist=True)
        except InspectError as exc:
            self.ui.error(str(exc))
            self.ui.pause()
            return

        while True:
            self._refresh_header()
            print_media_info_summary(self.ui, info)
            print()

            if info.is_playlist:
                choice = self.ui.select_menu(
                    "Playlist Inspector",
                    [
                        MenuOption("Browse & inspect a specific item", "pick_entry", hotkey="1"),
                        MenuOption("Download entire playlist with active preset", "dl_all", hotkey="2"),
                        MenuOption("Download specific playlist items (e.g. 1-3,5)", "dl_range", hotkey="3"),
                        MenuOption("Add all playlist item URLs to Batch Queue", "queue_all", hotkey="4"),
                        MenuOption("Back", "back", hotkey="0"),
                    ],
                )
                if choice in (None, "back"):
                    return
                if choice == "dl_all":
                    self._execute_download([url])
                    self.ui.pause()
                    return
                if choice == "dl_range":
                    spec = self.ui.prompt_text("Playlist item range (e.g. 1-3,5)", default=self.config.playlist_items)
                    if spec:
                        self._execute_download([url], extra_override_args=["-I", spec])
                        self.ui.pause()
                elif choice == "queue_all":
                    added = 0
                    for e in info.entries:
                        if e.url and e.url not in self.queue:
                            self.queue.append(e.url)
                            added += 1
                    self.ui.success(f"Added {added} URL(s) to Batch Queue.")
                    self.ui.pause()
                elif choice == "pick_entry":
                    if not info.entries:
                        self.ui.warn("Playlist contains no entries.")
                        self.ui.pause()
                        continue
                    entry_opts = [
                        MenuOption(
                            f"{e.index:03d}. {e.title}",
                            e,
                            format_duration(e.duration) if e.duration else "",
                        )
                        for e in info.entries
                    ]
                    chosen_entry = self.ui.select_menu(
                        "Select Playlist Item",
                        entry_opts,
                        subtitle=f"{len(info.entries)} items",
                    )
                    if chosen_entry and chosen_entry.url:
                        self.last_url = chosen_entry.url
                        self._inspect_interactive(chosen_entry.url)
                continue

            # Single media view
            choice = self.ui.select_menu(
                "Format & Stream Inspector",
                [
                    MenuOption("Download now with active preset", "dl_preset", hotkey="1"),
                    MenuOption("Pick a format ID from full list", "pick_single", hotkey="2"),
                    MenuOption("Pair custom Video + Audio stream IDs", "pair_va", hotkey="3"),
                    MenuOption("Print full format table & subtitles", "table", hotkey="4"),
                    MenuOption("Back", "back", hotkey="0"),
                ],
            )
            if choice in (None, "back"):
                return
            if choice == "dl_preset":
                self._execute_download([info.webpage_url or url])
                self.ui.pause()
                return
            if choice == "table":
                print_formats_table(self.ui, info)
                if info.subtitles:
                    print(f"\n{self.ui.style.accent('Subtitles:')} {', '.join(info.subtitles)}")
                if info.automatic_captions:
                    print(f"{self.ui.style.accent('Auto-captions:')} {', '.join(info.automatic_captions[:20])}")
                self.ui.pause()
            elif choice == "pick_single":
                self._pick_single_format_and_run(info, info.webpage_url or url)
            elif choice == "pair_va":
                self._pair_video_audio_and_run(info, info.webpage_url or url)

    def _pick_single_format_and_run(self, info: MediaInfo, target_url: str) -> None:
        if not info.formats:
            self.ui.warn("No selectable formats found.")
            self.ui.pause()
            return

        opts: list[MenuOption] = []
        for fmt in info.formats:
            kind_badge = f"[{fmt.kind}]"
            opts.append(MenuOption(fmt.label(), fmt, kind_badge))

        chosen: FormatEntry | None = self.ui.select_menu(
            "Select Format",
            opts,
            subtitle="ID       EXT   RES            CODEC       RATE       SIZE  NOTE",
        )
        if chosen is None:
            return

        selector = chosen.format_id
        if chosen.kind == "video" and info.audio_formats:
            if self.ui.confirm(
                f"Format {chosen.format_id} is video-only. Merge with best audio ({chosen.format_id}+ba)?",
                default=True,
            ):
                selector = f"{chosen.format_id}+ba/b"

        action = self.ui.select_menu(
            f"Selected selector: {selector}",
            [
                MenuOption("Download once with this format", "once", hotkey="1"),
                MenuOption("Save as active format selector & download", "save_dl", hotkey="2"),
                MenuOption("Save as active format selector only", "save", hotkey="3"),
            ],
        )
        if action == "once":
            orig = self.config.format_selector
            orig_mode = self.config.mode
            self.config.format_selector = selector
            if chosen.kind == "audio":
                self.config.mode = "audio"
            else:
                self.config.mode = "video"
            try:
                self._execute_download([target_url])
            finally:
                self.config.format_selector = orig
                self.config.mode = orig_mode
            self.ui.pause()
        elif action in ("save_dl", "save"):
            self.config.format_selector = selector
            self.config.mode = "audio" if chosen.kind == "audio" else "video"
            self.config.preset_name = f"Custom ({selector})"
            self.save_state()
            self.ui.success(f"Saved format selector '{selector}'.")
            if action == "save_dl":
                self._execute_download([target_url])
            self.ui.pause()

    def _pair_video_audio_and_run(self, info: MediaInfo, target_url: str) -> None:
        if not info.video_formats or not info.audio_formats:
            self.ui.warn("Both video-only and audio-only streams are required to pair streams.")
            self.ui.pause()
            return

        vid: FormatEntry | None = self.ui.select_menu(
            "Step 1/2: Choose Video Stream",
            [MenuOption(f.label(), f) for f in info.video_formats],
            subtitle="ID       EXT   RES            CODEC       RATE       SIZE  NOTE",
        )
        if vid is None:
            return

        aud: FormatEntry | None = self.ui.select_menu(
            "Step 2/2: Choose Audio Stream",
            [MenuOption(f.label(), f) for f in info.audio_formats],
            subtitle="ID       EXT   RES            CODEC       RATE       SIZE  NOTE",
        )
        if aud is None:
            return

        selector = f"{vid.format_id}+{aud.format_id}"
        orig_mode = self.config.mode
        orig_sel = self.config.format_selector
        self.config.mode = "video"
        self.config.format_selector = selector
        try:
            self._execute_download([target_url])
        finally:
            self.config.mode = orig_mode
            self.config.format_selector = orig_sel
        self.ui.pause()

    def _menu_presets(self) -> None:
        while True:
            self._refresh_header()
            opts: list[MenuOption] = []
            for p in BUILTIN_PRESETS:
                marker = " (active)" if self.config.preset_name == p.name else ""
                opts.append(
                    MenuOption(
                        f"{p.name}{marker}",
                        ("builtin", p),
                        p.description,
                    )
                )
            for cname, ccfg in self.custom_presets.items():
                marker = " (active)" if self.config.preset_name == cname else ""
                opts.append(
                    MenuOption(
                        f"[Custom] {cname}{marker}",
                        ("custom", cname),
                        f"{ccfg.mode} | {ccfg.format_selector}",
                    )
                )
            opts.extend(
                [
                    MenuOption("Custom format selector / container", ("action", "custom_format"), "Set -f, -S, merge/remux"),
                    MenuOption("Save current config as custom preset", ("action", "save_custom"), "Store reusable profile"),
                    MenuOption("Delete a custom preset", ("action", "delete_custom")),
                    MenuOption("Reset all settings to defaults", ("action", "reset")),
                    MenuOption("Back", ("action", "back"), hotkey="0"),
                ]
            )

            picked = self.ui.select_menu("Presets & Format Profiles", opts)
            if picked is None or picked == ("action", "back"):
                return

            kind, payload = picked
            if kind == "builtin":
                self.config = apply_builtin_preset(self.config, payload)
                self.save_state()
                self.ui.success(f"Activated preset: {payload.name}")
                return
            if kind == "custom":
                saved = self.custom_presets[payload]
                self.config = DownloadConfig.from_dict(saved.to_dict())
                self.config.preset_name = payload
                self.save_state()
                self.ui.success(f"Loaded custom preset: {payload}")
                return
            if payload == "custom_format":
                self._configure_custom_format()
            elif payload == "save_custom":
                name = self.ui.prompt_text("Custom preset name")
                if name:
                    if find_builtin_preset(name):
                        self.ui.warn("Cannot overwrite a built-in preset name.")
                    else:
                        snap = DownloadConfig.from_dict(self.config.to_dict())
                        snap.preset_name = name
                        self.custom_presets[name] = snap
                        self.config.preset_name = name
                        self.save_state()
                        self.ui.success(f"Saved custom preset '{name}'.")
                    self.ui.pause()
            elif payload == "delete_custom":
                if not self.custom_presets:
                    self.ui.info("No custom presets saved.")
                    self.ui.pause()
                    continue
                del_opts = [MenuOption(name, name) for name in self.custom_presets]
                target_del = self.ui.select_menu("Delete Custom Preset", del_opts)
                if target_del and target_del in self.custom_presets:
                    del self.custom_presets[target_del]
                    self.save_state()
                    self.ui.success(f"Deleted preset '{target_del}'.")
                    self.ui.pause()
            elif payload == "reset":
                if self.ui.confirm("Reset all active settings to factory defaults?", default=False):
                    self.config = DownloadConfig()
                    self.save_state()
                    self.ui.success("Configuration reset to defaults.")
                    self.ui.pause()

    def _configure_custom_format(self) -> None:
        mode = self.ui.select_menu(
            "Download Mode",
            [
                MenuOption("Video (Video + Audio)", "video", hotkey="1"),
                MenuOption("Audio Only (-x)", "audio", hotkey="2"),
            ],
        )
        if mode is None:
            return
        self.config.mode = mode
        if mode == "audio":
            fmt = self.ui.select_menu(
                "Audio Output Format",
                [
                    MenuOption("mp3", "mp3"),
                    MenuOption("m4a", "m4a"),
                    MenuOption("aac", "aac"),
                    MenuOption("opus", "opus"),
                    MenuOption("flac", "flac"),
                    MenuOption("wav", "wav"),
                    MenuOption("best (keep original)", "best"),
                ],
            )
            if fmt:
                self.config.audio_format = fmt
            qual = self.ui.prompt_text(
                "Audio quality (0=best VBR .. 10=worst, or bitrate like 320K)",
                default=self.config.audio_quality,
            )
            if qual is not None:
                self.config.audio_quality = qual
            sel = self.ui.prompt_text("Format selector (-f)", default="ba/b")
            if sel is not None:
                self.config.format_selector = sel
        else:
            sel = self.ui.prompt_text(
                "Format selector (-f)",
                default=self.config.format_selector or "bv*+ba/b",
            )
            if sel is not None:
                self.config.format_selector = sel
            sort_spec = self.ui.prompt_text(
                "Format sort (-S, leave blank for default)",
                default=self.config.format_sort,
                allow_empty=True,
            )
            if sort_spec is not None:
                self.config.format_sort = sort_spec
            container = self.ui.select_menu(
                "Merge Output Container",
                [
                    MenuOption("mp4", "mp4"),
                    MenuOption("mkv", "mkv"),
                    MenuOption("webm", "webm"),
                    MenuOption("Auto (no forced merge format)", ""),
                ],
            )
            if container is not None:
                self.config.merge_output_format = container
        self.config.preset_name = "Custom"
        self.save_state()
        self.ui.success("Updated format configuration.")
        self.ui.pause()

    def _menu_batch(self) -> None:
        while True:
            self._refresh_header()
            if self.queue:
                preview_lines = [f"{idx:2d}. {u}" for idx, u in enumerate(self.queue[:12], start=1)]
                if len(self.queue) > 12:
                    preview_lines.append(f"... and {len(self.queue) - 12} more")
                self.ui.print_panel(f"Batch Queue ({len(self.queue)})", preview_lines, border="gray")
                print()

            choice = self.ui.select_menu(
                "Batch Download & Queue",
                [
                    MenuOption("Add URL(s) to queue", "add", hotkey="1"),
                    MenuOption("Load URLs from a .txt file", "load_file", hotkey="2"),
                    MenuOption("Run yt-dlp directly with batch file (-a)", "run_file", hotkey="3"),
                    MenuOption("Download all queued URLs sequentially", "run_queue", hotkey="4"),
                    MenuOption("Remove an item from queue", "remove", hotkey="5"),
                    MenuOption("Clear queue", "clear", hotkey="6"),
                    MenuOption("Back", "back", hotkey="0"),
                ],
            )
            if choice in (None, "back"):
                return
            if choice == "add":
                print(self.ui.style.dim("Enter URLs one per line (or space-separated). Empty line finishes:"))
                while True:
                    line = self.ui.prompt_text("URL", allow_empty=True, strip_quotes=True)
                    if not line:
                        break
                    for token in line.split():
                        if token not in self.queue:
                            self.queue.append(token)
            elif choice == "load_file":
                fpath = self.ui.prompt_text("Path to URL list (.txt)", complete_paths=True, strip_quotes=True)
                if fpath:
                    try:
                        loaded = load_batch_urls(fpath)
                        added = 0
                        for u in loaded:
                            if u not in self.queue:
                                self.queue.append(u)
                                added += 1
                        self.ui.success(f"Loaded {added} new URL(s) from {fpath}.")
                    except OSError as exc:
                        self.ui.error(f"Could not read file: {exc}")
                    self.ui.pause()
            elif choice == "run_file":
                fpath = self.ui.prompt_text("Batch file path (-a)", complete_paths=True, strip_quotes=True)
                if fpath:
                    expanded = Path(os.path.expanduser(fpath))
                    if not expanded.is_file():
                        self.ui.error(f"File not found: {expanded}")
                    else:
                        self._execute_download([], batch_file=str(expanded))
                    self.ui.pause()
            elif choice == "run_queue":
                if not self.queue:
                    self.ui.warn("Batch queue is empty.")
                    self.ui.pause()
                    continue
                self._run_queue_sequentially()
            elif choice == "remove":
                if not self.queue:
                    self.ui.info("Queue is already empty.")
                    self.ui.pause()
                    continue
                opts = [MenuOption(f"{idx}. {u}", idx - 1) for idx, u in enumerate(self.queue, start=1)]
                rem_idx = self.ui.select_menu("Remove Queued URL", opts)
                if rem_idx is not None:
                    removed = self.queue.pop(rem_idx)
                    self.ui.info(f"Removed: {removed}")
                    self.ui.pause()
            elif choice == "clear":
                self.queue.clear()
                self.ui.info("Queue cleared.")
                self.ui.pause()

    def _run_queue_sequentially(self) -> None:
        total = len(self.queue)
        if not self.ui.confirm(f"Start downloading {total} queued URL(s)?", default=True):
            return
        succeeded: list[str] = []
        failed: list[tuple[str, int]] = []
        for idx, url in enumerate(list(self.queue), start=1):
            print(f"\n{self.ui.style.accent(f'[{idx}/{total}]')} {url}")
            code = self._execute_download([url], confirm_before_run=False)
            if code == 0:
                succeeded.append(url)
                if url in self.queue:
                    self.queue.remove(url)
            elif code == 130:
                self.ui.warn("Batch stopped by user.")
                break
            else:
                failed.append((url, code))

        print()
        summary = [
            f"Succeeded: {len(succeeded)}",
            f"Failed   : {len(failed)}",
            f"Remaining: {len(self.queue)}",
        ]
        for url, code in failed:
            summary.append(f"  ✗ ({code}) {url}")
        self.ui.print_panel("Batch Summary", summary, border="green" if not failed else "yellow")
        self.ui.pause()

    def _menu_output(self) -> None:
        while True:
            self._refresh_header()
            choice = self.ui.select_menu(
                "Output Folder & Filename Template",
                [
                    MenuOption("Output directory", "dir", self.config.output_dir, hotkey="1"),
                    MenuOption("Filename template (-o)", "tpl", self.config.output_template, hotkey="2"),
                    MenuOption(
                        "Filename safety & overwrite flags",
                        "safety",
                        f"ASCII={self.config.restrict_filenames}, Win={self.config.windows_filenames}, NoOverwrite={self.config.no_overwrites}, Trim={self.config.trim_filenames}",
                        hotkey="3",
                    ),
                    MenuOption("Back", "back", hotkey="0"),
                ],
            )
            if choice in (None, "back"):
                return
            if choice == "dir":
                dir_choice = self.ui.select_menu(
                    "Choose Output Directory",
                    [
                        MenuOption("~/Downloads", "~/Downloads", hotkey="1"),
                        MenuOption("~/Videos", "~/Videos", hotkey="2"),
                        MenuOption("~/Music", "~/Music", hotkey="3"),
                        MenuOption("Current working directory (.)", ".", hotkey="4"),
                        MenuOption("Enter custom path...", "custom", hotkey="5"),
                    ],
                )
                if dir_choice == "custom":
                    custom_dir = self.ui.prompt_text(
                        "Custom output directory",
                        default=self.config.output_dir,
                        complete_paths=True,
                        strip_quotes=True,
                    )
                    if custom_dir:
                        self.config.output_dir = custom_dir
                        self.save_state()
                elif dir_choice:
                    self.config.output_dir = dir_choice
                    self.save_state()
            elif choice == "tpl":
                tpl_opts = [
                    MenuOption(name, tpl, f"{tpl}  ({desc})")
                    for name, tpl, desc in OUTPUT_TEMPLATE_PRESETS
                ]
                tpl_opts.append(MenuOption("Custom template string...", "__custom__"))
                picked_tpl = self.ui.select_menu("Filename Template (-o)", tpl_opts)
                if picked_tpl == "__custom__":
                    custom_tpl = self.ui.prompt_text(
                        "Enter yt-dlp output template",
                        default=self.config.output_template,
                    )
                    if custom_tpl:
                        self.config.output_template = custom_tpl
                        self.save_state()
                elif picked_tpl:
                    self.config.output_template = picked_tpl
                    self.save_state()
            elif choice == "safety":
                selected = self.ui.multi_select_menu(
                    "Filename Safety & Overwrite Flags",
                    [
                        MenuOption(
                            "Restrict filenames to ASCII (--restrict-filenames)",
                            "restrict_filenames",
                            selected=self.config.restrict_filenames,
                        ),
                        MenuOption(
                            "Windows-compatible filenames (--windows-filenames)",
                            "windows_filenames",
                            selected=self.config.windows_filenames,
                        ),
                        MenuOption(
                            "Do not overwrite existing files (--no-overwrites)",
                            "no_overwrites",
                            selected=self.config.no_overwrites,
                        ),
                    ],
                )
                if selected is not None:
                    self.config.restrict_filenames = "restrict_filenames" in selected
                    self.config.windows_filenames = "windows_filenames" in selected
                    self.config.no_overwrites = "no_overwrites" in selected
                    trim = self.ui.prompt_text(
                        "Max filename length (--trim-filenames, 0 = disabled)",
                        default=str(self.config.trim_filenames),
                    )
                    if trim is not None and trim.isdigit():
                        self.config.trim_filenames = int(trim)
                    self.save_state()

    def _menu_postprocess(self) -> None:
        while True:
            self._refresh_header()
            choice = self.ui.select_menu(
                "Subtitles, Metadata & SponsorBlock",
                [
                    MenuOption(
                        "Toggle embedding & subtitle flags",
                        "toggles",
                        ", ".join(self.config.summary_features()) or "None enabled",
                        hotkey="1",
                    ),
                    MenuOption("Subtitle languages (--sub-langs)", "sub_langs", self.config.sub_langs, hotkey="2"),
                    MenuOption(
                        "SponsorBlock mode & categories",
                        "sponsorblock",
                        f"{self.config.sponsorblock_mode}"
                        + (f" ({self.config.sponsorblock_cats})" if self.config.sponsorblock_mode != "none" else ""),
                        hotkey="3",
                    ),
                    MenuOption(
                        "Audio extraction format & quality",
                        "audio",
                        f"{self.config.audio_format} (q={self.config.audio_quality})",
                        hotkey="4",
                    ),
                    MenuOption("Video remux / recode container", "container", hotkey="5"),
                    MenuOption("Back", "back", hotkey="0"),
                ],
            )
            if choice in (None, "back"):
                return
            if choice == "toggles":
                selected = self.ui.multi_select_menu(
                    "Post-Processing & Subtitles",
                    [
                        MenuOption("Embed metadata (--embed-metadata)", "embed_metadata", selected=self.config.embed_metadata),
                        MenuOption("Embed thumbnail cover art (--embed-thumbnail)", "embed_thumbnail", selected=self.config.embed_thumbnail),
                        MenuOption("Embed chapter markers (--embed-chapters)", "embed_chapters", selected=self.config.embed_chapters),
                        MenuOption("Download subtitles (--write-subs)", "write_subs", selected=self.config.write_subs),
                        MenuOption("Download auto-generated subtitles (--write-auto-subs)", "write_auto_subs", selected=self.config.write_auto_subs),
                        MenuOption("Embed subtitles into video (--embed-subs)", "embed_subs", selected=self.config.embed_subs),
                    ],
                )
                if selected is not None:
                    self.config.embed_metadata = "embed_metadata" in selected
                    self.config.embed_thumbnail = "embed_thumbnail" in selected
                    self.config.embed_chapters = "embed_chapters" in selected
                    self.config.write_subs = "write_subs" in selected
                    self.config.write_auto_subs = "write_auto_subs" in selected
                    self.config.embed_subs = "embed_subs" in selected
                    if self.config.embed_subs and not (self.config.write_subs or self.config.write_auto_subs):
                        self.config.write_subs = True
                    self.save_state()
            elif choice == "sub_langs":
                langs = self.ui.prompt_text(
                    "Subtitle languages (e.g. en.*,en or en,es,ja or all)",
                    default=self.config.sub_langs,
                )
                if langs:
                    self.config.sub_langs = langs
                    self.save_state()
            elif choice == "sponsorblock":
                sb_mode = self.ui.select_menu(
                    "SponsorBlock Action",
                    [
                        MenuOption("Off", "none", hotkey="1"),
                        MenuOption("Mark SponsorBlock segments as chapters", "mark", hotkey="2"),
                        MenuOption("Remove SponsorBlock segments from video", "remove", hotkey="3"),
                    ],
                )
                if sb_mode is not None:
                    self.config.sponsorblock_mode = sb_mode
                    if sb_mode != "none":
                        cats = self.ui.prompt_text(
                            "Categories (sponsor,intro,outro,selfpromo,interaction,filler,preview,all)",
                            default=self.config.sponsorblock_cats,
                        )
                        if cats:
                            self.config.sponsorblock_cats = cats
                    self.save_state()
            elif choice == "audio":
                afmt = self.ui.select_menu(
                    "Audio Extraction Format",
                    [
                        MenuOption("mp3", "mp3"),
                        MenuOption("m4a", "m4a"),
                        MenuOption("aac", "aac"),
                        MenuOption("opus", "opus"),
                        MenuOption("flac", "flac"),
                        MenuOption("wav", "wav"),
                        MenuOption("best (native audio)", "best"),
                    ],
                )
                if afmt:
                    self.config.audio_format = afmt
                aqual = self.ui.prompt_text(
                    "Audio quality (0=best .. 10=worst, or bitrate like 320K)",
                    default=self.config.audio_quality,
                )
                if aqual:
                    self.config.audio_quality = aqual
                self.save_state()
            elif choice == "container":
                remux = self.ui.select_menu(
                    "Remux Video Container (--remux-video, lossless)",
                    [
                        MenuOption("None (keep merge container)", ""),
                        MenuOption("mp4", "mp4"),
                        MenuOption("mkv", "mkv"),
                        MenuOption("webm", "webm"),
                    ],
                )
                if remux is not None:
                    self.config.remux_video = remux
                    self.save_state()

    def _menu_playlist_clips(self) -> None:
        while True:
            self._refresh_header()
            choice = self.ui.select_menu(
                "Playlist, Clips & Archive",
                [
                    MenuOption("Playlist mode", "pl_mode", self.config.playlist_mode, hotkey="1"),
                    MenuOption("Playlist item range (-I)", "pl_items", self.config.playlist_items or "All", hotkey="2"),
                    MenuOption(
                        "Download archive file",
                        "archive",
                        self.config.download_archive or "Disabled",
                        hotkey="3",
                    ),
                    MenuOption(
                        "Time-range / chapter clip (--download-sections)",
                        "sections",
                        self.config.download_sections or "Full video",
                        hotkey="4",
                    ),
                    MenuOption(
                        "Split chapters & error handling",
                        "flags",
                        f"SplitChapters={self.config.split_chapters}, IgnoreErrors={self.config.ignore_errors}",
                        hotkey="5",
                    ),
                    MenuOption("Back", "back", hotkey="0"),
                ],
            )
            if choice in (None, "back"):
                return
            if choice == "pl_mode":
                mode = self.ui.select_menu(
                    "Playlist Handling",
                    [
                        MenuOption("Auto (default yt-dlp behavior)", "auto", hotkey="1"),
                        MenuOption("Single video only (--no-playlist)", "single", hotkey="2"),
                        MenuOption("Full playlist (--yes-playlist)", "playlist", hotkey="3"),
                    ],
                )
                if mode:
                    self.config.playlist_mode = mode
                    self.save_state()
            elif choice == "pl_items":
                val = self.ui.prompt_text(
                    "Playlist items (-I, e.g. 1-5,8 or -3: for last 3; empty to clear)",
                    default=self.config.playlist_items,
                    allow_empty=True,
                )
                if val is not None:
                    self.config.playlist_items = val
                    self.save_state()
            elif choice == "archive":
                default_arch = str(self.store.config_dir / "archive.txt")
                pick = self.ui.select_menu(
                    "Download Archive (--download-archive)",
                    [
                        MenuOption("Disable archive", "", hotkey="1"),
                        MenuOption(f"Use default ({default_arch})", default_arch, hotkey="2"),
                        MenuOption("Enter custom archive file path...", "__custom__", hotkey="3"),
                    ],
                )
                if pick == "__custom__":
                    p = self.ui.prompt_text(
                        "Archive file path",
                        default=self.config.download_archive or default_arch,
                        complete_paths=True,
                        strip_quotes=True,
                    )
                    if p is not None:
                        self.config.download_archive = p
                        self.save_state()
                elif pick is not None:
                    self.config.download_archive = pick
                    self.save_state()
            elif choice == "sections":
                sec = self.ui.prompt_text(
                    "Section spec (e.g. *00:01:00-00:02:30 or chapter regex; empty to clear)",
                    default=self.config.download_sections,
                    allow_empty=True,
                )
                if sec is not None:
                    self.config.download_sections = sec
                    self.save_state()
            elif choice == "flags":
                selected = self.ui.multi_select_menu(
                    "Playlist & Chapter Flags",
                    [
                        MenuOption("Split video into chapter files (--split-chapters)", "split_chapters", selected=self.config.split_chapters),
                        MenuOption("Continue playlist on download errors (-i)", "ignore_errors", selected=self.config.ignore_errors),
                    ],
                )
                if selected is not None:
                    self.config.split_chapters = "split_chapters" in selected
                    self.config.ignore_errors = "ignore_errors" in selected
                    self.save_state()

    def _menu_network(self) -> None:
        while True:
            self._refresh_header()
            choice = self.ui.select_menu(
                "Network, Cookies & Extra Flags",
                [
                    MenuOption("Cookies from browser", "browser", self.config.cookies_from_browser or "None", hotkey="1"),
                    MenuOption("Cookies file (cookies.txt)", "cookie_file", self.config.cookies_file or "None", hotkey="2"),
                    MenuOption("Concurrent fragments (-N)", "fragments", str(self.config.concurrent_fragments), hotkey="3"),
                    MenuOption("Download speed limit (-r)", "rate", self.config.rate_limit or "Unlimited", hotkey="4"),
                    MenuOption("Proxy URL (--proxy)", "proxy", self.config.proxy or "None", hotkey="5"),
                    MenuOption("Extra raw yt-dlp CLI flags", "extra", self.config.extra_args or "None", hotkey="6"),
                    MenuOption("Back", "back", hotkey="0"),
                ],
            )
            if choice in (None, "back"):
                return
            if choice == "browser":
                browsers = ["", "firefox", "chrome", "chromium", "brave", "edge", "vivaldi", "opera", "safari"]
                opts = [
                    MenuOption(b or "None (disable browser cookies)", b)
                    for b in browsers
                ]
                opts.append(MenuOption("Custom (e.g. firefox:default-release)", "__custom__"))
                picked = self.ui.select_menu("Cookies From Browser", opts)
                if picked == "__custom__":
                    val = self.ui.prompt_text("Browser[:Profile] string", default=self.config.cookies_from_browser)
                    if val is not None:
                        self.config.cookies_from_browser = val
                        if val:
                            self.config.cookies_file = ""
                        self.save_state()
                elif picked is not None:
                    self.config.cookies_from_browser = picked
                    if picked:
                        self.config.cookies_file = ""
                    self.save_state()
            elif choice == "cookie_file":
                cpath = self.ui.prompt_text(
                    "Netscape cookies.txt path (empty to clear)",
                    default=self.config.cookies_file,
                    allow_empty=True,
                    complete_paths=True,
                    strip_quotes=True,
                )
                if cpath is not None:
                    self.config.cookies_file = cpath
                    if cpath:
                        self.config.cookies_from_browser = ""
                    self.save_state()
            elif choice == "fragments":
                picked = self.ui.select_menu(
                    "Concurrent Fragments (-N)",
                    [
                        MenuOption("1 (Sequential)", 1, hotkey="1"),
                        MenuOption("4 (Balanced default)", 4, hotkey="2"),
                        MenuOption("8 (Fast)", 8, hotkey="3"),
                        MenuOption("16 (Aggressive)", 16, hotkey="4"),
                    ],
                )
                if picked is not None:
                    self.config.concurrent_fragments = picked
                    self.save_state()
            elif choice == "rate":
                rate = self.ui.prompt_text(
                    "Rate limit (e.g. 5M, 500K; empty for unlimited)",
                    default=self.config.rate_limit,
                    allow_empty=True,
                )
                if rate is not None:
                    self.config.rate_limit = rate
                    self.save_state()
            elif choice == "proxy":
                proxy = self.ui.prompt_text(
                    "Proxy URL (e.g. socks5://127.0.0.1:1080; empty to clear)",
                    default=self.config.proxy,
                    allow_empty=True,
                )
                if proxy is not None:
                    self.config.proxy = proxy
                    self.save_state()
            elif choice == "extra":
                raw_extra = self.ui.prompt_text(
                    "Extra yt-dlp flags (empty to clear)",
                    default=self.config.extra_args,
                    allow_empty=True,
                )
                if raw_extra is not None:
                    try:
                        parse_extra_args(raw_extra)
                        self.config.extra_args = raw_extra
                        self.save_state()
                    except ValueError as exc:
                        self.ui.error(str(exc))
                        self.ui.pause()

    def _menu_tools(self) -> None:
        while True:
            self._refresh_header()
            choice = self.ui.select_menu(
                "Command Preview, History & Tools",
                [
                    MenuOption("Preview generated yt-dlp command", "preview", hotkey="1"),
                    MenuOption("Dry-run (simulate) a URL", "simulate", hotkey="2"),
                    MenuOption("Download history (view / re-run)", "history", hotkey="3"),
                    MenuOption("System Doctor (diagnostics)", "doctor", hotkey="4"),
                    MenuOption("Back", "back", hotkey="0"),
                ],
            )
            if choice in (None, "back"):
                return
            if choice == "preview":
                sample_url = self.last_url or "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
                try:
                    cmd = build_command(self.config, [sample_url])
                    self.ui.print_panel("Generated Command", [format_command_preview(cmd)], border="cyan", wrap=True)
                except ValueError as exc:
                    self.ui.error(str(exc))
                self.ui.pause()
            elif choice == "simulate":
                url = self.ui.prompt_text("URL to simulate", default=self.last_url, strip_quotes=True)
                if url:
                    self.last_url = url
                    self._execute_download([url], simulate=True)
                    self.ui.pause()
            elif choice == "history":
                self._menu_history()
            elif choice == "doctor":
                self.env = check_environment()
                print_doctor_report(self.ui, self.env, self.store)
                self.ui.pause()

    def _menu_history(self) -> None:
        while True:
            entries = self.store.load_history()
            if not entries:
                self.ui.info("Download history is empty.")
                self.ui.pause()
                return
            opts = [
                MenuOption(
                    f"[{e.timestamp}] {', '.join(e.urls)}",
                    ("entry", e),
                    f"{e.preset_name} | exit={e.exit_code}",
                )
                for e in entries
            ]
            opts.append(MenuOption("Clear history", ("clear", None)))
            opts.append(MenuOption("Back", ("back", None), hotkey="0"))

            picked = self.ui.select_menu("Download History", opts)
            if picked is None or picked[0] == "back":
                return
            if picked[0] == "clear":
                if self.ui.confirm("Clear all download history?", default=False):
                    self.store.clear_history()
                    self.ui.success("History cleared.")
                    self.ui.pause()
                    return
            elif picked[0] == "entry":
                entry = picked[1]
                self.ui.print_panel(
                    "History Entry",
                    [
                        f"Timestamp : {entry.timestamp}",
                        f"Preset    : {entry.preset_name}",
                        f"Output Dir: {entry.output_dir}",
                        f"Exit Code : {entry.exit_code}",
                        f"Command   : {format_command_preview(entry.command)}",
                    ],
                    wrap=True,
                )
                if self.ui.confirm("Re-run URL(s) with current active settings?", default=False):
                    self._execute_download(entry.urls, confirm_before_run=False)
                    self.ui.pause()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="yt-dlp-helper",
        description="Interactive terminal CLI interface for yt-dlp.",
    )
    parser.add_argument(
        "targets",
        nargs="*",
        help="Optional URL(s) or search query to preload or download.",
    )
    parser.add_argument(
        "-p",
        "--preset",
        help="Apply a built-in or saved custom preset (e.g. mp4, compat-mp4, mkv, 1080p, mp3, opus, flac, archival).",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        help="Override download output directory.",
    )
    parser.add_argument(
        "-i",
        "--inspect",
        action="store_true",
        help="Inspect metadata and available formats for the given URL.",
    )
    parser.add_argument(
        "--download",
        action="store_true",
        help="Download provided URL(s) immediately without opening the main menu.",
    )
    parser.add_argument(
        "-a",
        "--batch-file",
        help="Read URLs from a batch text file.",
    )
    parser.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="Simulate download (--simulate) and show generated yt-dlp command.",
    )
    parser.add_argument(
        "--doctor",
        action="store_true",
        help="Check yt-dlp, ffmpeg, and JavaScript runtime availability and exit.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Show detailed yt-dlp informational output lines.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point."""
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    ui = TerminalUI()
    store = ConfigStore()
    app = App(store=store, ui=ui, verbose=args.verbose)

    if args.preset:
        builtin = find_builtin_preset(args.preset)
        if builtin is not None:
            app.config = apply_builtin_preset(app.config, builtin)
        elif args.preset in app.custom_presets:
            app.config = DownloadConfig.from_dict(app.custom_presets[args.preset].to_dict())
            app.config.preset_name = args.preset
        else:
            available = ", ".join(p.key for p in BUILTIN_PRESETS)
            ui.error(f"Unknown preset '{args.preset}'. Built-in keys: {available}")
            return 2

    if args.output_dir:
        app.config.output_dir = args.output_dir

    if args.doctor:
        print_doctor_report(ui, app.env, store)
        return 0 if app.env.ok else 1

    if args.inspect:
        if not args.targets:
            ui.error("--inspect requires a URL.")
            return 2
        if ui.is_interactive():
            app._inspect_interactive(args.targets[0])
            return 0
        try:
            info = inspect_url(app.config, args.targets[0], flat_playlist=True)
        except InspectError as exc:
            ui.error(str(exc))
            return 1
        print_media_info_summary(ui, info)
        if info.is_playlist:
            for e in info.entries:
                print(f"  {e.index:03d}. {e.title} ({format_duration(e.duration)}) - {e.url}")
        else:
            print_formats_table(ui, info)
        return 0

    if args.download or args.dry_run or args.batch_file:
        if not args.targets and not args.batch_file:
            ui.error("Provide at least one URL or --batch-file.")
            return 2
        return app._execute_download(
            list(args.targets),
            simulate=args.dry_run,
            batch_file=args.batch_file,
            confirm_before_run=False,
        )

    return app.run_interactive(list(args.targets) if args.targets else None)
