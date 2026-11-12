"""Tests for ANSI/Unicode UI width, boxes, and interactive terminal controls."""

from __future__ import annotations

from contextlib import redirect_stdout
import io
import unittest

from yt_dlp_helper.runner import ProgressEvent
from yt_dlp_helper.ui import (
    ProgressDisplay,
    Style,
    TerminalUI,
    display_width,
    pad_display,
    strip_ansi,
    truncate_display,
    wrap_display,
)


class TestUI(unittest.TestCase):
    def test_ansi_and_unicode_display_width(self) -> None:
        styled = "\x1b[96mHello 日本語\x1b[0m"
        self.assertEqual(strip_ansi(styled), "Hello 日本語")
        # 'Hello ' (6) + 3 CJK chars (6) = 12
        self.assertEqual(display_width(styled), 12)

        trunc = truncate_display(styled, 9)
        self.assertLessEqual(display_width(trunc), 9)
        self.assertTrue(trunc.endswith("\x1b[0m"))

        padded = pad_display(styled, 15)
        self.assertEqual(display_width(padded), 15)

    def test_print_panel_exact_row_widths(self) -> None:
        ui = TerminalUI()
        ui.style = Style(enabled=True)
        ui.unicode = True

        buf = io.StringIO()
        with redirect_stdout(buf):
            ui.print_panel(
                "Active Profile",
                [
                    ui.style.ok("Line one"),
                    "A very long line that should cleanly truncate before reaching the border edge of the box",
                ],
                width=50,
            )
        lines = [line for line in buf.getvalue().splitlines() if line]
        self.assertEqual(len(lines), 4)
        for line in lines:
            self.assertEqual(display_width(line), 50)

    def test_wrap_display_and_wrapped_panel(self) -> None:
        wrapped = wrap_display("yt-dlp -f bv*+ba/b https://example.com/very/long/path/without/spaces", 24)
        self.assertGreaterEqual(len(wrapped), 3)
        for row in wrapped:
            self.assertLessEqual(display_width(row), 24)

        ui = TerminalUI()
        ui.style = Style(enabled=False)
        ui.unicode = True
        buf = io.StringIO()
        with redirect_stdout(buf):
            ui.print_panel(
                "Cmd",
                ["yt-dlp -f 'bv*+ba/b' --merge-output-format mp4 -- https://example.com/watch?v=1234567890"],
                width=40,
                wrap=True,
            )
        panel_lines = [line for line in buf.getvalue().splitlines() if line]
        self.assertGreaterEqual(len(panel_lines), 5)
        for line in panel_lines:
            self.assertEqual(display_width(line), 40)

    def test_progress_display_non_tty_milestones(self) -> None:
        ui = TerminalUI()
        ui.style = Style(enabled=False)
        pd = ProgressDisplay(ui)
        buf = io.StringIO()
        with redirect_stdout(buf):
            pd.handle_progress(ProgressEvent(percent=10.0, total="10MiB", speed="1MiB/s", eta="00:09"))
            pd.handle_progress(ProgressEvent(percent=50.0, total="10MiB", speed="1MiB/s", eta="00:05"))
            pd.handle_progress(ProgressEvent(percent=100.0, total="10MiB", speed="1MiB/s", eta="00:01", finished=True))
        output = buf.getvalue()
        self.assertIn("10.0%", output)
        self.assertIn("50.0%", output)
        self.assertIn("100.0%", output)


if __name__ == "__main__":
    unittest.main()
