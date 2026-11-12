"""Module entry point for `python3 -m yt_dlp_helper`."""

import sys

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
