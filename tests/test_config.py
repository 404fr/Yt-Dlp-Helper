"""Tests for configuration models, presets, and XDG persistence."""

from __future__ import annotations

import unittest
from pathlib import Path
import tempfile

from yt_dlp_helper.config import (
    BUILTIN_PRESETS,
    ConfigStore,
    DownloadConfig,
    apply_builtin_preset,
    find_builtin_preset,
)


class TestConfig(unittest.TestCase):
    def test_from_dict_ignores_unknown_and_clamps_invalid(self) -> None:
        cfg = DownloadConfig.from_dict(
            {
                "mode": "invalid_mode",
                "playlist_mode": "nope",
                "sponsorblock_mode": "bad",
                "concurrent_fragments": 0,
                "trim_filenames": -5,
                "unknown_key": "ignored",
                "embed_thumbnail": 1,
            }
        )
        self.assertEqual(cfg.mode, "video")
        self.assertEqual(cfg.playlist_mode, "auto")
        self.assertEqual(cfg.sponsorblock_mode, "none")
        self.assertEqual(cfg.concurrent_fragments, 1)
        self.assertEqual(cfg.trim_filenames, 0)
        self.assertTrue(cfg.embed_thumbnail)

    def test_find_and_apply_builtin_preset_preserves_user_env(self) -> None:
        base = DownloadConfig(
            output_dir="~/CustomVideos",
            output_template="%(title)s.%(ext)s",
            cookies_from_browser="firefox",
            proxy="socks5://127.0.0.1:1080",
            write_subs=True,
            embed_subs=True,
        )
        mp3 = find_builtin_preset("mp3")
        self.assertIsNotNone(mp3)
        assert mp3 is not None

        updated = apply_builtin_preset(base, mp3)
        self.assertEqual(updated.mode, "audio")
        self.assertEqual(updated.audio_format, "mp3")
        self.assertEqual(updated.preset_name, "Audio Only (MP3)")
        # Preset-scoped subtitle flags should reset to defaults unless preset sets them
        self.assertFalse(updated.write_subs)
        self.assertFalse(updated.embed_subs)
        # Environment/user preferences must be preserved
        self.assertEqual(updated.output_dir, "~/CustomVideos")
        self.assertEqual(updated.output_template, "%(title)s.%(ext)s")
        self.assertEqual(updated.cookies_from_browser, "firefox")
        self.assertEqual(updated.proxy, "socks5://127.0.0.1:1080")

    def test_all_builtin_presets_can_be_found_by_key_and_name(self) -> None:
        for preset in BUILTIN_PRESETS:
            self.assertEqual(find_builtin_preset(preset.key), preset)
            self.assertEqual(find_builtin_preset(preset.name.upper()), preset)

    def test_config_store_round_trip_and_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ConfigStore(Path(tmp))
            active = DownloadConfig(output_dir="/tmp/dl", concurrent_fragments=8)
            custom = {"Podcasts": DownloadConfig(mode="audio", audio_format="opus")}
            store.save(active, custom)

            loaded_active, loaded_custom = store.load()
            self.assertEqual(loaded_active.output_dir, "/tmp/dl")
            self.assertEqual(loaded_active.concurrent_fragments, 8)
            self.assertIn("Podcasts", loaded_custom)
            self.assertEqual(loaded_custom["Podcasts"].audio_format, "opus")
            self.assertEqual(loaded_custom["Podcasts"].preset_name, "Podcasts")

            store.append_history(
                urls=["https://example.com/v1"],
                config=active,
                command=["yt-dlp", "--", "https://example.com/v1"],
                exit_code=0,
            )
            hist = store.load_history()
            self.assertEqual(len(hist), 1)
            self.assertEqual(hist[0].urls, ["https://example.com/v1"])
            self.assertEqual(hist[0].exit_code, 0)

            store.clear_history()
            self.assertEqual(store.load_history(), [])


if __name__ == "__main__":
    unittest.main()
