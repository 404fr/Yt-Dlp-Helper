"""End-to-end CLI and PTY tests for Yt-Dlp Helper."""

from __future__ import annotations

import os
from pathlib import Path
import pty
import select
import subprocess
import sys
import tempfile
import time
import unittest


REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "yt-dlp-helper"


class TestCLI(unittest.TestCase):
    def _env(self, config_dir: Path) -> dict[str, str]:
        env = os.environ.copy()
        env["YT_DLP_HELPER_CONFIG_DIR"] = str(config_dir)
        env["NO_COLOR"] = "1"
        return env

    def test_version_and_doctor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env(Path(tmp))
            v = subprocess.run(
                [str(LAUNCHER), "--version"],
                capture_output=True,
                text=True,
                env=env,
                timeout=10,
            )
            self.assertEqual(v.returncode, 0)
            self.assertIn("yt-dlp-helper", v.stdout)

            doc = subprocess.run(
                [str(LAUNCHER), "--doctor"],
                capture_output=True,
                text=True,
                env=env,
                timeout=10,
            )
            self.assertEqual(doc.returncode, 0)
            self.assertIn("System Doctor", doc.stdout)
            self.assertIn("yt-dlp", doc.stdout)

    def test_invalid_preset_and_missing_inspect_url(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env(Path(tmp))
            bad_preset = subprocess.run(
                [str(LAUNCHER), "--preset", "nonexistent"],
                capture_output=True,
                text=True,
                env=env,
                timeout=10,
            )
            self.assertEqual(bad_preset.returncode, 2)

            bad_inspect = subprocess.run(
                [str(LAUNCHER), "--inspect"],
                capture_output=True,
                text=True,
                env=env,
                timeout=10,
            )
            self.assertEqual(bad_inspect.returncode, 2)

    def test_interactive_piped_preset_switch_and_preview(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg_dir = Path(tmp)
            env = self._env(cfg_dir)
            # Sequence in fallback menu:
            # 3 -> Switch Quality / Format Preset
            # 6 -> Audio Only (MP3) (6th item in BUILTIN_PRESETS)
            # 9 -> Command Preview, History & Tools
            # 1 -> Preview generated yt-dlp command
            # 0 -> Back
            # 0 -> Exit
            stdin_script = "3\n6\n9\n1\n0\n0\n"
            proc = subprocess.run(
                [sys.executable, "-m", "yt_dlp_helper"],
                input=stdin_script,
                capture_output=True,
                text=True,
                cwd=str(REPO_ROOT),
                env=env,
                timeout=15,
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            self.assertIn("Audio Only (MP3)", proc.stdout)
            self.assertIn("--audio-format mp3", proc.stdout)
            self.assertTrue((cfg_dir / "config.json").exists())

    def test_interactive_pty_arrow_keys_and_clean_exit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env(Path(tmp))
            env.pop("NO_COLOR", None)
            env["TERM"] = "xterm-256color"

            master_fd, slave_fd = pty.openpty()
            proc = subprocess.Popen(
                [str(LAUNCHER)],
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                env=env,
                close_fds=True,
            )
            os.close(slave_fd)

            output_chunks: list[bytes] = []

            def drain(duration: float) -> bytes:
                end = time.monotonic() + duration
                buf = b""
                while time.monotonic() < end:
                    ready, _, _ = select.select([master_fd], [], [], 0.05)
                    if ready:
                        try:
                            chunk = os.read(master_fd, 4096)
                            if not chunk:
                                break
                            buf += chunk
                            output_chunks.append(chunk)
                        except OSError:
                            break
                    elif proc.poll() is not None:
                        break
                return buf

            try:
                # Wait for initial banner & menu
                drain(0.5)
                # Move down twice with arrow-down, then press '9' hotkey for Tools,
                # press '1' hotkey for Preview Command, Enter to dismiss pause, '0' back, '0' exit.
                os.write(master_fd, b"\x1b[B\x1b[B")
                drain(0.15)
                os.write(master_fd, b"9")
                drain(0.25)
                os.write(master_fd, b"1")
                drain(0.25)
                os.write(master_fd, b"\r")  # pause Enter
                drain(0.25)
                os.write(master_fd, b"0")   # back to Main Menu
                drain(0.25)
                os.write(master_fd, b"0")   # exit
                drain(0.4)
                rc = proc.wait(timeout=5)
            finally:
                os.close(master_fd)
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()

            full_out = b"".join(output_chunks).decode("utf-8", errors="replace")
            self.assertEqual(rc, 0, msg=full_out)
            self.assertIn("Yt-Dlp Helper", full_out)
            self.assertIn("Generated Command", full_out)
            # Cursor show sequence restored on exit
            self.assertIn("\x1b[?25h", full_out)


if __name__ == "__main__":
    unittest.main()
