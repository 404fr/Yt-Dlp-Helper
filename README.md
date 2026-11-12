# Yt-Dlp Helper

An interactive, zero-dependency terminal CLI interface for [`yt-dlp`](https://github.com/yt-dlp/yt-dlp).

## Quick Start

Run directly in your terminal from the project folder:

```bash
./yt-dlp-helper
```

Or launch via Python module:

```bash
python3 -m yt_dlp_helper
```

Optional global install via `uv` (adds `yt-dlp-helper` to your PATH):

```bash
uv tool install --editable .
```

## Features

- **Interactive Terminal Menus**: Arrow-key / `j`/`k` navigation, instant numeric hotkeys (`1`–`9`, `0`), scrollable lists, and multi-select toggles.
- **Format & Metadata Inspector**: Inspect video/playlist metadata (`yt-dlp --dump-single-json`), view categorized streams (Combined, Video-only, Audio-only) with resolutions, codecs, bitrates, and sizes, and pair custom video + audio format IDs (e.g. `137+140`).
- **Curated & Custom Presets**:
  - `mp4` — Best Video (MP4)
  - `compat-mp4` — Compatible MP4 (H.264/AAC)
  - `mkv` — Best Quality Native (MKV)
  - `1080p` / `720p` — Resolution-capped MP4
  - `mp3` / `m4a` / `opus` / `flac` — Audio extraction with cover art & metadata
  - `archival` — MKV with embedded subtitles, thumbnail, metadata & chapters
  - Save, switch, and delete your own custom presets (`~/.config/yt-dlp-helper/config.json`).
- **Batch Queue & `.txt` Lists**: Queue multiple URLs interactively or load a `.txt` batch file.
- **Subtitles, SponsorBlock & Clipping**: Toggle subtitles/auto-subs, embed metadata/thumbnails/chapters, mark or remove SponsorBlock segments, split chapters, or download time-range clips (`--download-sections`).
- **Cookies, Archive & Network**: Browser cookie extraction (`--cookies-from-browser`), `cookies.txt`, download archives (`--download-archive`), proxies, rate limits, concurrent fragments (`-N`), and extra flags.
- **Command Preview, Dry-Run & History**: Inspect or simulate (`--simulate`) the generated `yt-dlp` command and re-run previous downloads from history.

## Direct CLI Options

```bash
# Open interactive menu preloaded with a URL
./yt-dlp-helper "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

# Download immediately using a preset
./yt-dlp-helper -p mp3 --download "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

# Inspect metadata and formats
./yt-dlp-helper -i "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

# Dry-run simulation
./yt-dlp-helper -p 1080p --dry-run "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

# Check yt-dlp, ffmpeg, and JS runtime availability
./yt-dlp-helper --doctor
```
