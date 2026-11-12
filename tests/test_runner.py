"""Tests for yt-dlp output parsing, environment checks, and subprocess runner."""

from __future__ import annotations

import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest

from yt_dlp_helper.runner import (
    ProgressEvent,
    StatusEvent,
    check_environment,
    parse_ytdlp_line,
    run_ytdlp,
)


class TestRunner(unittest.TestCase):
    def test_parse_progress_lines(self) -> None:
        ev1 = parse_ytdlp_line("[download]  42.3% of   15.20MiB at    3.40MiB/s ETA 00:03")
        self.assertIsInstance(ev1, ProgressEvent)
        assert isinstance(ev1, ProgressEvent)
        self.assertAlmostEqual(ev1.percent, 42.3)
        self.assertEqual(ev1.total, "15.20MiB")
        self.assertEqual(ev1.speed, "3.40MiB/s")
        self.assertEqual(ev1.eta, "00:03")
        self.assertFalse(ev1.finished)

        ev2 = parse_ytdlp_line("[download]  80.0% of ~  25.00MiB at    2.10MiB/s ETA 00:04 (frag 12/15)")
        assert isinstance(ev2, ProgressEvent)
        self.assertEqual(ev2.extra, "frag 12/15")

        ev3 = parse_ytdlp_line("[download] 100% of   15.20MiB in 00:00:04 at    3.50MiB/s")
        assert isinstance(ev3, ProgressEvent)
        self.assertTrue(ev3.finished)
        self.assertEqual(ev3.percent, 100.0)

    def test_parse_status_lines(self) -> None:
        d = parse_ytdlp_line("[download] Destination: /tmp/video.mp4")
        assert isinstance(d, StatusEvent)
        self.assertEqual(d.kind, "destination")
        self.assertEqual(d.path, "/tmp/video.mp4")

        m = parse_ytdlp_line('[Merger] Merging formats into "/tmp/final.mp4"')
        assert isinstance(m, StatusEvent)
        self.assertEqual(m.kind, "postprocess")
        self.assertEqual(m.path, "/tmp/final.mp4")

        a = parse_ytdlp_line("[ExtractAudio] Destination: /tmp/song.mp3")
        assert isinstance(a, StatusEvent)
        self.assertEqual(a.kind, "postprocess")
        self.assertEqual(a.path, "/tmp/song.mp3")

        e = parse_ytdlp_line("ERROR: Video unavailable")
        assert isinstance(e, StatusEvent)
        self.assertEqual(e.kind, "error")

        w = parse_ytdlp_line("WARNING: Falling back to default format")
        assert isinstance(w, StatusEvent)
        self.assertEqual(w.kind, "warning")

    def test_run_ytdlp_streams_fake_executable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_bin = Path(tmp) / "fake-ytdlp"
            fake_bin.write_text(
                f"#!{sys.executable}\n"
                "import sys\n"
                "assert '--newline' in sys.argv\n"
                "print('[download] Destination: /tmp/out.mp4')\n"
                "print('[download]  50.0% of   10.00MiB at    5.00MiB/s ETA 00:01')\n"
                "print('[download] 100% of   10.00MiB in 00:00:02 at    5.00MiB/s')\n"
                "print('WARNING: Sample warning', file=sys.stderr)\n"
                "print('[Merger] Merging formats into \"/tmp/final.mkv\"')\n",
                encoding="utf-8",
            )
            fake_bin.chmod(fake_bin.stat().st_mode | stat.S_IXUSR)

            progress_events: list[ProgressEvent] = []
            status_events: list[StatusEvent] = []
            result = run_ytdlp(
                [str(fake_bin), "--", "https://example.com"],
                on_progress=progress_events.append,
                on_status=status_events.append,
            )
            self.assertEqual(result.exit_code, 0)
            self.assertFalse(result.cancelled)
            self.assertEqual(len(progress_events), 2)
            self.assertIn("/tmp/out.mp4", result.destinations)
            self.assertIn("/tmp/final.mkv", result.destinations)
            self.assertEqual(result.warnings, ["WARNING: Sample warning"])

    def test_check_environment(self) -> None:
        env = check_environment()
        # yt-dlp and ffmpeg are installed on this system
        self.assertIsNotNone(env.yt_dlp_path)
        self.assertTrue(bool(env.yt_dlp_version))


if __name__ == "__main__":
    unittest.main()
