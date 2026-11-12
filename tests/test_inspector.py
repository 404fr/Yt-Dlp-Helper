"""Tests for metadata & format JSON inspection parsing."""

from __future__ import annotations

import unittest

from yt_dlp_helper.inspector import (
    format_bytes,
    format_duration,
    parse_media_json,
    shorten_codec,
)


class TestInspector(unittest.TestCase):
    def test_helpers(self) -> None:
        self.assertEqual(format_bytes(None), "~")
        self.assertEqual(format_bytes(512), "512 B")
        self.assertEqual(format_bytes(1024 * 1024 * 5), "5.0 MiB")
        self.assertEqual(format_duration(None), "Live/Unknown")
        self.assertEqual(format_duration(65), "1:05")
        self.assertEqual(format_duration(3661), "1:01:01")
        self.assertEqual(shorten_codec("avc1.640028"), "h264")
        self.assertEqual(shorten_codec("vp09.00.41.08"), "vp9")
        self.assertEqual(shorten_codec("av01.0.08M.08"), "av1")
        self.assertEqual(shorten_codec("mp4a.40.2"), "aac")
        self.assertEqual(shorten_codec("none"), "-")

    def test_parse_single_video_json(self) -> None:
        raw = {
            "id": "abc123",
            "title": "Sample Video",
            "uploader": "ChannelOne",
            "duration": 185,
            "upload_date": "20261112",
            "view_count": 42000,
            "webpage_url": "https://example.com/watch?v=abc123",
            "extractor_key": "Youtube",
            "chapters": [{"title": "Intro"}, {"title": "Main"}],
            "subtitles": {"en": [], "ja": [], "live_chat": []},
            "automatic_captions": {"en": [], "es": []},
            "formats": [
                {"format_id": "sb0", "ext": "mhtml", "vcodec": "none", "acodec": "none"},
                {
                    "format_id": "140",
                    "ext": "m4a",
                    "vcodec": "none",
                    "acodec": "mp4a.40.2",
                    "abr": 129.0,
                    "filesize": 3_000_000,
                    "format_note": "medium",
                },
                {
                    "format_id": "137",
                    "ext": "mp4",
                    "width": 1920,
                    "height": 1080,
                    "fps": 60,
                    "vcodec": "avc1.640028",
                    "acodec": "none",
                    "vbr": 4500.0,
                    "filesize": 90_000_000,
                    "format_note": "1080p60",
                },
                {
                    "format_id": "22",
                    "ext": "mp4",
                    "width": 1280,
                    "height": 720,
                    "fps": 30,
                    "vcodec": "avc1.64001F",
                    "acodec": "mp4a.40.2",
                    "tbr": 1500.0,
                    "filesize_approx": 32_000_000,
                    "format_note": "720p",
                },
            ],
        }
        info = parse_media_json(raw)
        self.assertFalse(info.is_playlist)
        self.assertEqual(info.upload_date, "2026-11-12")
        self.assertEqual(info.chapters_count, 2)
        self.assertEqual(info.subtitles, ["en", "ja"])
        self.assertEqual(len(info.formats), 3)  # mhtml storyboard filtered out
        self.assertEqual(len(info.combined_formats), 1)
        self.assertEqual(len(info.video_formats), 1)
        self.assertEqual(len(info.audio_formats), 1)
        self.assertEqual(info.formats[0].format_id, "22")
        self.assertIn("1920x1080@60", info.video_formats[0].label())

    def test_parse_playlist_json(self) -> None:
        raw = {
            "_type": "playlist",
            "id": "PL123",
            "title": "My Playlist",
            "channel": "Creator",
            "extractor": "youtube:tab",
            "entries": [
                {"id": "v1", "title": "First", "duration": 60, "url": "https://example.com/1"},
                {"id": "v2", "title": "Second", "duration": 120, "webpage_url": "https://example.com/2"},
            ],
        }
        info = parse_media_json(raw)
        self.assertTrue(info.is_playlist)
        self.assertEqual(info.title, "My Playlist")
        self.assertEqual(len(info.entries), 2)
        self.assertEqual(info.entries[0].index, 1)
        self.assertEqual(info.entries[1].url, "https://example.com/2")


if __name__ == "__main__":
    unittest.main()
