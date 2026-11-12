"""Terminal UI engine: ANSI styling, Unicode-safe boxes, menus, and progress rendering."""

from __future__ import annotations

from dataclasses import dataclass
import glob
import os
import re
import select
import shutil
import sys
import termios
import tty
import unicodedata
from typing import Any, Callable

try:
    import readline
except ImportError:  # pragma: no cover - platform fallback
    readline = None  # type: ignore[assignment]

from .config import DownloadConfig
from .runner import EnvironmentStatus, ProgressEvent, StatusEvent


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def supports_color() -> bool:
    """Determine whether ANSI color codes should be emitted."""
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return sys.stdout.isatty()


def supports_unicode() -> bool:
    """Determine whether stdout supports Unicode box-drawing characters."""
    enc = (getattr(sys.stdout, "encoding", None) or "").lower()
    return "utf" in enc


def strip_ansi(text: str) -> str:
    """Remove ANSI SGR escape sequences from text."""
    return _ANSI_RE.sub("", text)


def _char_width(ch: str) -> int:
    if not ch:
        return 0
    cat = unicodedata.category(ch)
    if cat in ("Mn", "Me", "Cf"):
        return 0
    if unicodedata.east_asian_width(ch) in ("W", "F"):
        return 2
    return 1


def display_width(text: str) -> int:
    """Compute visible terminal column width of a string (ANSI- and Unicode-aware)."""
    plain = strip_ansi(text)
    return sum(_char_width(ch) for ch in plain)


def truncate_display(text: str, max_width: int, ellipsis: str = "…") -> str:
    """Truncate a string (possibly containing ANSI codes) to fit within max_width columns."""
    if max_width <= 0:
        return ""
    if display_width(text) <= max_width:
        return text

    ell_w = display_width(ellipsis)
    if max_width <= ell_w:
        return ellipsis[:max_width]
    target = max_width - ell_w

    out: list[str] = []
    width = 0
    i = 0
    has_ansi = False
    while i < len(text):
        match = _ANSI_RE.match(text, i)
        if match:
            out.append(match.group(0))
            has_ansi = True
            i = match.end()
            continue
        ch = text[i]
        cw = _char_width(ch)
        if width + cw > target:
            break
        out.append(ch)
        width += cw
        i += 1

    out.append(ellipsis)
    if has_ansi:
        out.append("\x1b[0m")
    return "".join(out)


def pad_display(text: str, width: int) -> str:
    """Right-pad text with spaces to reach exact display width."""
    cur = display_width(text)
    if cur >= width:
        return text
    return text + (" " * (width - cur))


def wrap_display(text: str, max_width: int) -> list[str]:
    """Wrap text cleanly to fit within max_width terminal columns."""
    if max_width <= 1 or display_width(text) <= max_width:
        return [text]

    words = text.split(" ")
    lines: list[str] = []
    current = ""
    for word in words:
        if not current:
            if display_width(word) <= max_width:
                current = word
                continue
        else:
            candidate = f"{current} {word}"
            if display_width(candidate) <= max_width:
                current = candidate
                continue
            lines.append(current)
            current = ""
            if display_width(word) <= max_width:
                current = word
                continue

        # Hard-split a single long token (e.g. a long URL) across lines
        chunk = ""
        chunk_w = 0
        for ch in word:
            cw = _char_width(ch)
            if chunk and chunk_w + cw > max_width:
                lines.append(chunk)
                chunk = ch
                chunk_w = cw
            else:
                chunk += ch
                chunk_w += cw
        current = chunk

    if current:
        lines.append(current)
    return lines or [""]


def term_size() -> tuple[int, int]:
    """Return clamped (columns, lines) for the terminal."""
    size = shutil.get_terminal_size((90, 28))
    return max(50, size.columns), max(14, size.lines)


class Style:
    """Lightweight ANSI styling helper."""

    CODES = {
        "reset": "\x1b[0m",
        "bold": "\x1b[1m",
        "dim": "\x1b[2m",
        "cyan": "\x1b[36m",
        "bright_cyan": "\x1b[96m",
        "green": "\x1b[32m",
        "bright_green": "\x1b[92m",
        "yellow": "\x1b[33m",
        "red": "\x1b[31m",
        "magenta": "\x1b[35m",
        "blue": "\x1b[34m",
        "white": "\x1b[97m",
        "gray": "\x1b[90m",
    }

    def __init__(self, enabled: bool | None = None) -> None:
        self.enabled = supports_color() if enabled is None else enabled

    def apply(self, text: str, *names: str) -> str:
        if not self.enabled or not names:
            return text
        prefix = "".join(self.CODES.get(name, "") for name in names)
        if not prefix:
            return text
        return f"{prefix}{text}{self.CODES['reset']}"

    def bold(self, text: str) -> str:
        return self.apply(text, "bold")

    def dim(self, text: str) -> str:
        return self.apply(text, "dim")

    def cyan(self, text: str) -> str:
        return self.apply(text, "cyan")

    def accent(self, text: str) -> str:
        return self.apply(text, "bright_cyan", "bold")

    def ok(self, text: str) -> str:
        return self.apply(text, "bright_green")

    def warn(self, text: str) -> str:
        return self.apply(text, "yellow")

    def err(self, text: str) -> str:
        return self.apply(text, "red", "bold")

    def tag(self, text: str) -> str:
        return self.apply(text, "magenta")


@dataclass(frozen=True)
class MenuOption:
    """Option entry for interactive select or multi-select menus."""

    label: str
    value: Any
    description: str = ""
    hotkey: str = ""
    selected: bool = False


class TerminalUI:
    """Interactive terminal interface controller."""

    def __init__(self) -> None:
        self.style = Style()
        self.unicode = supports_unicode()
        self._history_urls: list[str] = []

    def is_interactive(self) -> bool:
        return sys.stdin.isatty() and sys.stdout.isatty()

    def clear_screen(self) -> None:
        if self.is_interactive():
            sys.stdout.write("\x1b[2J\x1b[H")
            sys.stdout.flush()

    def _box_chars(self) -> tuple[str, str, str, str, str, str]:
        if self.unicode:
            return ("╭", "╮", "╰", "╯", "─", "│")
        return ("+", "+", "+", "+", "-", "|")

    def panel_width(self, max_width: int = 94) -> int:
        cols, _ = term_size()
        return min(max_width, max(50, cols - 2))

    def print_panel(
        self,
        title: str,
        lines: list[str],
        *,
        border: str = "cyan",
        width: int | None = None,
        wrap: bool = False,
    ) -> None:
        """Render a framed box containing styled lines."""
        box_w = width or self.panel_width()
        inner_w = max(20, box_w - 4)
        tl, tr, bl, br, hz, vt = self._box_chars()

        if title:
            title_clean = truncate_display(title, inner_w - 2)
            fill_len = max(1, box_w - 5 - display_width(title_clean))
            top = (
                f"{self.style.apply(tl + hz, border)} "
                f"{self.style.accent(title_clean)} "
                f"{self.style.apply((hz * fill_len) + tr, border)}"
            )
        else:
            top = self.style.apply(tl + (hz * (box_w - 2)) + tr, border)

        bottom = self.style.apply(bl + (hz * (box_w - 2)) + br, border)
        vbar = self.style.apply(vt, border)

        rendered: list[str] = []
        for line in lines:
            if wrap:
                rendered.extend(wrap_display(line, inner_w))
            else:
                rendered.append(truncate_display(line, inner_w))

        print(top)
        for row in rendered:
            clipped = truncate_display(row, inner_w)
            padded = pad_display(clipped, inner_w)
            print(f"{vbar} {padded} {vbar}")
        print(bottom)

    def print_banner(
        self,
        version: str,
        env: EnvironmentStatus,
        config: DownloadConfig,
    ) -> None:
        """Print the top application header and active configuration summary."""
        w = self.panel_width()
        yt_status = (
            self.style.ok(f"yt-dlp {env.yt_dlp_version or 'found'}")
            if env.yt_dlp_path
            else self.style.err("yt-dlp MISSING")
        )
        ff_status = (
            self.style.ok("ffmpeg OK")
            if env.ffmpeg_path
            else self.style.warn("ffmpeg missing")
        )
        js_status = (
            self.style.dim(f"JS:{','.join(env.js_runtimes.keys())}")
            if env.js_runtimes
            else self.style.dim("JS:none")
        )

        left = f"{self.style.accent('Yt-Dlp Helper')} {self.style.dim('v' + version)}"
        right = f"{yt_status} {self.style.dim('•')} {ff_status} {self.style.dim('•')} {js_status}"
        gap = max(2, (w - 4) - display_width(left) - display_width(right))
        header_line = f"{left}{' ' * gap}{right}"
        self.print_panel("", [header_line], border="cyan", width=w)

        if config.mode == "audio":
            mode_desc = f"Audio ({config.audio_format.upper()} q={config.audio_quality})"
        else:
            container = config.merge_output_format or config.remux_video or "auto"
            mode_desc = f"Video ({config.format_selector} -> {container})"

        features = config.summary_features()
        feat_str = ", ".join(features) if features else "None"
        cookies = (
            f"Browser:{config.cookies_from_browser}"
            if config.cookies_from_browser
            else (f"File:{config.cookies_file}" if config.cookies_file else "None")
        )
        pl_desc = config.playlist_mode.capitalize()
        if config.playlist_items and config.playlist_mode != "single":
            pl_desc += f" [{config.playlist_items}]"

        summary_lines = [
            f"{self.style.dim('Mode    :')} {mode_desc}",
            f"{self.style.dim('Output  :')} {config.output_dir} / {config.output_template}",
            f"{self.style.dim('Options :')} {feat_str}",
            f"{self.style.dim('Session :')} Playlist={pl_desc}  |  Fragments={config.concurrent_fragments}  |  Cookies={cookies}",
        ]
        self.print_panel(f"Active Profile: {config.preset_name}", summary_lines, border="gray", width=w)

    def info(self, msg: str) -> None:
        print(f"{self.style.cyan('ℹ')} {msg}")

    def success(self, msg: str) -> None:
        print(f"{self.style.ok('✓')} {msg}")

    def warn(self, msg: str) -> None:
        print(f"{self.style.warn('!')} {msg}")

    def error(self, msg: str) -> None:
        print(f"{self.style.err('✗')} {msg}")

    def pause(self, message: str = "Press Enter to continue...") -> None:
        if not self.is_interactive():
            return
        try:
            input(self.style.dim(f"\n{message}"))
        except (EOFError, KeyboardInterrupt):
            print()

    def _read_key_tty(self) -> str:
        """Read a single keypress or normalized navigation token from raw TTY."""
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setcbreak(fd)
            b = os.read(fd, 1)
            if not b:
                return "EOF"
            if b == b"\x03":
                raise KeyboardInterrupt
            if b == b"\x04":
                return "EOF"
            if b in (b"\r", b"\n"):
                return "ENTER"
            if b == b" ":
                return "SPACE"
            if b in (b"\x7f", b"\x08"):
                return "BACKSPACE"
            if b == b"\x1b":
                ready, _, _ = select.select([fd], [], [], 0.04)
                if not ready:
                    return "ESC"
                seq = b""
                for _ in range(12):
                    more, _, _ = select.select([fd], [], [], 0.015)
                    if not more:
                        break
                    chunk = os.read(fd, 1)
                    if not chunk:
                        break
                    seq += chunk
                    if chunk[-1:] and (64 <= chunk[0] <= 126):
                        # CSI/SS3 terminator (letters or ~)
                        if len(seq) >= 2 or seq in (b"OA", b"OB", b"OC", b"OD", b"OH", b"OF"):
                            break
                if seq in (b"[A", b"OA"):
                    return "UP"
                if seq in (b"[B", b"OB"):
                    return "DOWN"
                if seq in (b"[C", b"OC"):
                    return "RIGHT"
                if seq in (b"[D", b"OD"):
                    return "LEFT"
                if seq == b"[5~":
                    return "PGUP"
                if seq == b"[6~":
                    return "PGDN"
                if seq in (b"[H", b"[1~", b"OH"):
                    return "HOME"
                if seq in (b"[F", b"[4~", b"OF"):
                    return "END"
                return "ESC"

            # Decode possible multi-byte UTF-8 character
            buf = b
            first = b[0]
            expected = 1
            if (first & 0xE0) == 0xC0:
                expected = 2
            elif (first & 0xF0) == 0xE0:
                expected = 3
            elif (first & 0xF8) == 0xF0:
                expected = 4
            while len(buf) < expected:
                more, _, _ = select.select([fd], [], [], 0.02)
                if not more:
                    break
                buf += os.read(fd, 1)
            return buf.decode("utf-8", errors="ignore")
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

    def select_menu(
        self,
        title: str,
        options: list[MenuOption],
        *,
        default_index: int = 0,
        allow_cancel: bool = True,
        subtitle: str = "",
    ) -> Any:
        """Display an interactive menu and return the chosen option's value (or None on cancel)."""
        if not options:
            return None

        cursor = min(max(default_index, 0), len(options) - 1)
        if not self.is_interactive():
            return self._select_menu_fallback(title, options, cursor, allow_cancel, subtitle)

        cols, rows = term_size()
        max_line_w = max(36, cols - 2)
        max_visible = max(5, min(len(options), rows - 10))
        scroll_top = 0

        def ensure_visible(idx: int) -> None:
            nonlocal scroll_top
            if idx < scroll_top:
                scroll_top = idx
            elif idx >= scroll_top + max_visible:
                scroll_top = idx - max_visible + 1

        ensure_visible(cursor)

        hint = "↑/↓ or j/k: move  •  Enter: select"
        if any(o.hotkey for o in options):
            hint += "  •  Hotkeys: instant jump"
        if allow_cancel:
            hint += "  •  q/Esc: back"

        has_scroll = len(options) > max_visible

        def render_lines() -> list[str]:
            lines: list[str] = [self.style.accent(truncate_display(title, max_line_w))]
            if subtitle:
                lines.append(self.style.dim(truncate_display(subtitle, max_line_w)))
            if has_scroll:
                above = scroll_top
                msg = f"  ↑ {above} more above" if above > 0 else ""
                lines.append(self.style.dim(truncate_display(msg, max_line_w)))

            end = min(len(options), scroll_top + max_visible)
            for idx in range(scroll_top, end):
                opt = options[idx]
                active = idx == cursor
                pointer = self.style.accent("❯ ") if active else "  "
                hk = f"[{opt.hotkey}] " if opt.hotkey else ""
                label_txt = self.style.bold(opt.label) if active else opt.label
                desc_txt = f"  {self.style.dim(opt.description)}" if opt.description else ""
                raw_row = f"{pointer}{self.style.cyan(hk)}{label_txt}{desc_txt}"
                lines.append(truncate_display(raw_row, max_line_w))

            if has_scroll:
                below = max(0, len(options) - (scroll_top + max_visible))
                msg = f"  ↓ {below} more below" if below > 0 else ""
                lines.append(self.style.dim(truncate_display(msg, max_line_w)))

            lines.append(self.style.dim(truncate_display(hint, max_line_w)))
            return lines

        hotkey_map = {
            opt.hotkey.lower(): opt.value
            for opt in options
            if opt.hotkey
        }

        rendered_count = 0
        try:
            sys.stdout.write("\x1b[?25l")  # Hide cursor during menu navigation
            while True:
                lines = render_lines()
                if rendered_count > 0:
                    sys.stdout.write(f"\x1b[{rendered_count}A\x1b[J")
                sys.stdout.write("\n".join(lines) + "\n")
                sys.stdout.flush()
                rendered_count = len(lines)

                key = self._read_key_tty()
                if key in ("UP", "k"):
                    cursor = (cursor - 1) % len(options)
                    ensure_visible(cursor)
                elif key in ("DOWN", "j"):
                    cursor = (cursor + 1) % len(options)
                    ensure_visible(cursor)
                elif key == "PGUP":
                    cursor = max(0, cursor - max_visible)
                    ensure_visible(cursor)
                elif key == "PGDN":
                    cursor = min(len(options) - 1, cursor + max_visible)
                    ensure_visible(cursor)
                elif key == "HOME":
                    cursor = 0
                    ensure_visible(cursor)
                elif key == "END":
                    cursor = len(options) - 1
                    ensure_visible(cursor)
                elif key == "ENTER":
                    return options[cursor].value
                elif key in ("ESC", "EOF") or (key.lower() == "q" and "q" not in hotkey_map):
                    if allow_cancel:
                        return None
                elif key.lower() in hotkey_map:
                    return hotkey_map[key.lower()]
        finally:
            sys.stdout.write("\x1b[?25h")
            sys.stdout.flush()

    def _select_menu_fallback(
        self,
        title: str,
        options: list[MenuOption],
        default_index: int,
        allow_cancel: bool,
        subtitle: str,
    ) -> Any:
        print(f"\n{title}")
        if subtitle:
            print(subtitle)
        all_hotkeyed = all(bool(opt.hotkey) for opt in options)
        for idx, opt in enumerate(options, start=1):
            desc = f" - {opt.description}" if opt.description else ""
            if all_hotkeyed:
                print(f"  [{opt.hotkey}] {opt.label}{desc}")
            else:
                hk = f" [{opt.hotkey}]" if opt.hotkey else ""
                print(f"  {idx}.{hk} {opt.label}{desc}")
        hotkey_map = {opt.hotkey.lower(): opt.value for opt in options if opt.hotkey}
        if all_hotkeyed:
            keys_str = "/".join(opt.hotkey for opt in options)
            default_label = options[default_index].hotkey
            prompt_str = f"Select [{keys_str}" + (", q=back" if allow_cancel else "") + f"] (default {default_label}): "
        else:
            prompt_str = f"Select [1-{len(options)}" + (", q=back" if allow_cancel else "") + f"] (default {default_index + 1}): "
        while True:
            try:
                raw = input(prompt_str).strip()
            except (EOFError, KeyboardInterrupt):
                return None if allow_cancel else options[default_index].value
            if not raw:
                return options[default_index].value
            if allow_cancel and raw.lower() in ("q", "back", "cancel"):
                return None
            if raw.lower() in hotkey_map:
                return hotkey_map[raw.lower()]
            if raw.isdigit():
                num = int(raw)
                if 1 <= num <= len(options):
                    return options[num - 1].value
            print("Invalid selection.")

    def multi_select_menu(
        self,
        title: str,
        options: list[MenuOption],
        *,
        subtitle: str = "",
    ) -> list[Any] | None:
        """Interactive checkbox multi-select. Returns list of selected values or None if cancelled."""
        if not options:
            return []
        checked = [bool(opt.selected) for opt in options]
        if not self.is_interactive():
            return self._multi_select_fallback(title, options, checked, subtitle)

        cols, rows = term_size()
        max_line_w = max(36, cols - 2)
        max_visible = max(5, min(len(options), rows - 10))
        cursor = 0
        scroll_top = 0
        has_scroll = len(options) > max_visible

        def ensure_visible(idx: int) -> None:
            nonlocal scroll_top
            if idx < scroll_top:
                scroll_top = idx
            elif idx >= scroll_top + max_visible:
                scroll_top = idx - max_visible + 1

        hint = "↑/↓: move  •  Space: toggle  •  a: all/none  •  Enter: apply  •  q/Esc: cancel"

        def render_lines() -> list[str]:
            lines: list[str] = [self.style.accent(truncate_display(title, max_line_w))]
            if subtitle:
                lines.append(self.style.dim(truncate_display(subtitle, max_line_w)))
            if has_scroll:
                msg = f"  ↑ {scroll_top} more above" if scroll_top > 0 else ""
                lines.append(self.style.dim(truncate_display(msg, max_line_w)))
            end = min(len(options), scroll_top + max_visible)
            for idx in range(scroll_top, end):
                opt = options[idx]
                active = idx == cursor
                pointer = self.style.accent("❯ ") if active else "  "
                box = self.style.ok("[✓]") if checked[idx] else self.style.dim("[ ]")
                label = self.style.bold(opt.label) if active else opt.label
                desc = f"  {self.style.dim(opt.description)}" if opt.description else ""
                lines.append(truncate_display(f"{pointer}{box} {label}{desc}", max_line_w))
            if has_scroll:
                below = max(0, len(options) - (scroll_top + max_visible))
                msg = f"  ↓ {below} more below" if below > 0 else ""
                lines.append(self.style.dim(truncate_display(msg, max_line_w)))
            lines.append(self.style.dim(truncate_display(hint, max_line_w)))
            return lines

        rendered_count = 0
        try:
            sys.stdout.write("\x1b[?25l")
            while True:
                lines = render_lines()
                if rendered_count > 0:
                    sys.stdout.write(f"\x1b[{rendered_count}A\x1b[J")
                sys.stdout.write("\n".join(lines) + "\n")
                sys.stdout.flush()
                rendered_count = len(lines)

                key = self._read_key_tty()
                if key in ("UP", "k"):
                    cursor = (cursor - 1) % len(options)
                    ensure_visible(cursor)
                elif key in ("DOWN", "j"):
                    cursor = (cursor + 1) % len(options)
                    ensure_visible(cursor)
                elif key == "SPACE":
                    checked[cursor] = not checked[cursor]
                elif key.lower() == "a":
                    target = not all(checked)
                    checked = [target] * len(options)
                elif key == "ENTER":
                    return [opt.value for opt, is_on in zip(options, checked) if is_on]
                elif key in ("ESC", "EOF") or key.lower() == "q":
                    return None
        finally:
            sys.stdout.write("\x1b[?25h")
            sys.stdout.flush()

    def _multi_select_fallback(
        self,
        title: str,
        options: list[MenuOption],
        checked: list[bool],
        subtitle: str,
    ) -> list[Any] | None:
        print(f"\n{title}")
        if subtitle:
            print(subtitle)
        for idx, (opt, is_on) in enumerate(zip(options, checked), start=1):
            mark = "x" if is_on else " "
            print(f"  {idx}. [{mark}] {opt.label}")
        try:
            raw = input("Enter numbers to toggle (comma-separated), Enter to apply, or 'q' to cancel: ").strip()
        except (EOFError, KeyboardInterrupt):
            return None
        if raw.lower() in ("q", "cancel"):
            return None
        if raw.lower() == "all":
            return [opt.value for opt in options]
        if raw.lower() == "none":
            return []
        if raw:
            for part in raw.split(","):
                part = part.strip()
                if part.isdigit():
                    num = int(part)
                    if 1 <= num <= len(options):
                        checked[num - 1] = not checked[num - 1]
        return [opt.value for opt, is_on in zip(options, checked) if is_on]

    def prompt_text(
        self,
        label: str,
        *,
        default: str = "",
        allow_empty: bool = False,
        strip_quotes: bool = False,
        complete_paths: bool = False,
    ) -> str | None:
        """Prompt user for a single line of text with optional default and path completion."""
        hint = f" [{default}]" if default else ""
        prompt = f"{self.style.cyan('?')} {self.style.bold(label)}{self.style.dim(hint)}: "

        old_completer = None
        old_delims = None
        if complete_paths and readline is not None and self.is_interactive():
            old_completer = readline.get_completer()
            old_delims = readline.get_completer_delims()

            def _path_completer(text: str, state: int) -> str | None:
                expanded = os.path.expanduser(text)
                matches = glob.glob(expanded + "*")
                results: list[str] = []
                for m in matches:
                    display_m = m
                    if text.startswith("~"):
                        home = os.path.expanduser("~")
                        if m.startswith(home):
                            display_m = "~" + m[len(home):]
                    if os.path.isdir(m) and not display_m.endswith(os.sep):
                        display_m += os.sep
                    results.append(display_m)
                return results[state] if state < len(results) else None

            readline.set_completer_delims(" \t\n;")
            readline.set_completer(_path_completer)
            readline.parse_and_bind("tab: complete")

        try:
            while True:
                try:
                    raw = input(prompt)
                except (EOFError, KeyboardInterrupt):
                    print()
                    return None
                val = raw.strip()
                if strip_quotes and len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
                    val = val[1:-1].strip()
                if not val:
                    if default:
                        return default
                    if allow_empty:
                        return ""
                    self.warn("Value cannot be empty (Ctrl+C to cancel).")
                    continue
                return val
        finally:
            if complete_paths and readline is not None and self.is_interactive():
                readline.set_completer(old_completer)
                if old_delims is not None:
                    readline.set_completer_delims(old_delims)

    def confirm(self, question: str, *, default: bool = True) -> bool:
        """Ask a yes/no confirmation question."""
        suffix = "[Y/n]" if default else "[y/N]"
        prompt = f"{self.style.cyan('?')} {self.style.bold(question)} {self.style.dim(suffix)}: "
        try:
            raw = input(prompt).strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return False
        if not raw:
            return default
        return raw in ("y", "yes")


class ProgressDisplay:
    """Renders live single-line download progress bars and status lines."""

    def __init__(self, ui: TerminalUI, *, verbose: bool = False) -> None:
        self.ui = ui
        self.verbose = verbose
        self._has_progress_line = False
        self._last_milestone = -1

    def _clear_progress_line(self) -> None:
        if self._has_progress_line:
            if self.ui.is_interactive():
                sys.stdout.write("\r\x1b[K")
            else:
                sys.stdout.write("\n")
            sys.stdout.flush()
            self._has_progress_line = False

    def handle_progress(self, event: ProgressEvent) -> None:
        """Render a ProgressEvent cleanly."""
        if not self.ui.is_interactive():
            milestone = int(event.percent // 25) * 25
            if milestone > self._last_milestone or event.finished:
                self._last_milestone = milestone
                print(
                    f"  [{event.percent:5.1f}%] {event.total} "
                    + (f"@ {event.speed} " if event.speed else "")
                    + (f"ETA {event.eta}" if event.eta else "")
                )
            return

        cols, _ = term_size()
        bar_w = max(14, min(28, cols - 58))
        filled = int(round((event.percent / 100.0) * bar_w))
        filled = min(max(filled, 0), bar_w)
        fill_ch = "█" if self.ui.unicode else "#"
        empty_ch = "░" if self.ui.unicode else "-"
        bar = (
            self.ui.style.ok(fill_ch * filled)
            + self.ui.style.dim(empty_ch * (bar_w - filled))
        )

        parts = [f"[{bar}]", f"{event.percent:5.1f}%", f"of {event.total}"]
        if event.speed:
            parts.append(f"at {event.speed}")
        if event.finished:
            if event.eta:
                parts.append(f"in {event.eta}")
        elif event.eta:
            parts.append(f"ETA {event.eta}")
        if event.extra:
            parts.append(f"({event.extra})")

        line = truncate_display("  " + "  ".join(parts), cols - 1)
        sys.stdout.write(f"\r\x1b[K{line}")
        if event.finished:
            sys.stdout.write("\n")
            self._has_progress_line = False
            self._last_milestone = -1
        else:
            self._has_progress_line = True
        sys.stdout.flush()

    def handle_status(self, event: StatusEvent) -> None:
        """Render a StatusEvent cleanly without colliding with active progress bars."""
        if event.kind == "info" and not self.verbose:
            # Show concise extractor/download milestones, hide noisy internal debug lines
            msg = event.message
            interesting = (
                msg.startswith("[info]")
                or msg.startswith("[download] Downloading playlist:")
                or msg.startswith("[download] Finished downloading playlist:")
                or "Downloading webpage" in msg
                or "Downloading 1 format(s)" in msg
            )
            if not interesting:
                return

        self._clear_progress_line()
        if event.kind == "error":
            self.ui.error(event.message)
        elif event.kind == "warning":
            self.ui.warn(event.message)
        elif event.kind == "postprocess":
            print(f"  {self.ui.style.tag('⚙')} {event.message}")
        elif event.kind == "destination":
            print(f"  {self.ui.style.ok('↓')} {event.message}")
        elif event.kind == "already":
            print(f"  {self.ui.style.cyan('•')} {event.message}")
        elif event.kind == "playlist_item":
            print(f"\n{self.ui.style.accent('▶')} {self.ui.style.bold(event.message)}")
        else:
            print(f"  {self.ui.style.dim(event.message)}")

    def finalize(self) -> None:
        """Ensure terminal cursor is on a fresh line when download ends."""
        if self._has_progress_line:
            sys.stdout.write("\n")
            sys.stdout.flush()
            self._has_progress_line = False
