"""Tests for yt-dlp command construction."""

from __future__ import annotations

import os
import unittest

from yt_dlp_helper.command_builder import (
    build_command,
    build_inspect_command,
    format_command_preview,
    parse_extra_args,
)
from yt_dlp_helper.config import DownloadConfig


class TestCommandBuilder(unittest.TestCase):
    def test_video_mode_default_command(self) -> None:
        cfg = DownloadConfig()
        cmd = build_command(cfg, ["https://example.com/watch?v=123"])
        self.assertEqual(cmd[0], "yt-dlp")
        self.assertIn("-f", cmd)
        self.assertIn("bv*+ba/b", cmd)
        self.assertIn("--merge-output-format", cmd)
        self.assertIn("mp4", cmd)
        self.assertIn("--embed-metadata", cmd)
        self.assertIn("--embed-chapters", cmd)
        self.assertIn("-N", cmd)
        self.assertEqual(cmd[-2:], ["--", "https://example.com/watch?v=123"])

    def test_audio_mode_forces_audio_selector_over_video_selector(self) -> None:
        cfg = DownloadConfig(
            mode="audio",
            format_selector="bv*+ba/b",
            audio_format="mp3",
            audio_quality="0",
            embed_subs=True,  # should not emit --embed-subs in audio mode
        )
        cmd = build_command(cfg, ["https://example.com/a"])
        idx_f = cmd.index("-f")
        self.assertEqual(cmd[idx_f + 1], "ba/b")
        self.assertIn("-x", cmd)
        self.assertIn("--audio-format", cmd)
        self.assertIn("mp3", cmd)
        self.assertNotIn("--embed-subs", cmd)

    def test_embed_subs_implies_write_subs_in_video_mode(self) -> None:
        cfg = DownloadConfig(
            mode="video",
            write_subs=False,
            write_auto_subs=False,
            embed_subs=True,
            sub_langs="en,ja",
        )
        cmd = build_command(cfg, ["https://example.com/v"])
        self.assertIn("--write-subs", cmd)
        self.assertIn("--embed-subs", cmd)
        self.assertIn("--sub-langs", cmd)
        self.assertIn("en,ja", cmd)

    def test_cookies_playlist_sponsorblock_and_sections(self) -> None:
        cfg = DownloadConfig(
            playlist_mode="playlist",
            playlist_items="1-3,5",
            download_archive="~/archive.txt",
            cookies_from_browser="firefox",
            cookies_file="~/ignored.txt",
            sponsorblock_mode="remove",
            sponsorblock_cats="sponsor,selfpromo",
            download_sections="*00:01:00-00:02:00",
            split_chapters=True,
            rate_limit="2M",
            proxy="socks5://127.0.0.1:1080",
            extra_args='--sleep-interval 2 --user-agent "Custom Agent"',
        )
        cmd = build_command(cfg, ["https://example.com/p"], simulate=True)
        self.assertIn("--yes-playlist", cmd)
        self.assertIn("-I", cmd)
        self.assertIn("1-3,5", cmd)
        self.assertIn("--download-archive", cmd)
        self.assertIn(os.path.expanduser("~/archive.txt"), cmd)
        self.assertIn("--cookies-from-browser", cmd)
        self.assertIn("firefox", cmd)
        self.assertNotIn("--cookies", cmd)
        self.assertIn("--sponsorblock-remove", cmd)
        self.assertIn("--download-sections", cmd)
        self.assertIn("*00:01:00-00:02:00", cmd)
        self.assertIn("--split-chapters", cmd)
        self.assertIn("-r", cmd)
        self.assertIn("2M", cmd)
        self.assertIn("--proxy", cmd)
        self.assertIn("--simulate", cmd)
        self.assertIn("Custom Agent", cmd)

    def test_invalid_extra_args_raises(self) -> None:
        with self.assertRaises(ValueError):
            parse_extra_args('--user-agent "unterminated')

    def test_empty_targets_raises(self) -> None:
        with self.assertRaises(ValueError):
            build_command(DownloadConfig(), [])

    def test_batch_file_accepted_without_urls(self) -> None:
        cmd = build_command(DownloadConfig(), [], batch_file="~/urls.txt")
        self.assertIn("-a", cmd)
        self.assertIn(os.path.expanduser("~/urls.txt"), cmd)
        self.assertNotIn("--", cmd)

    def test_inspect_command_and_preview(self) -> None:
        cfg = DownloadConfig(playlist_mode="single", cookies_file="~/c.txt")
        cmd = build_inspect_command(cfg, "https://example.com/v")
        self.assertIn("--dump-single-json", cmd)
        self.assertIn("--flat-playlist", cmd)
        self.assertIn("--no-playlist", cmd)
        self.assertIn("--cookies", cmd)
        preview = format_command_preview(cmd)
        self.assertIn("--dump-single-json", preview)


if __name__ == "__main__":
    unittest.main()
