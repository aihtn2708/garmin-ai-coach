#!/usr/bin/env python3
"""Start Garmin AI Coach.  Usage:  python run.py [--port 8765] [--no-browser]"""

import argparse
import sys

if sys.version_info < (3, 9):
    raise SystemExit("Python 3.9 or newer is required.")

from coach.server import serve  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Local, private AI analysis of your own Garmin data.")
    parser.add_argument("--port", type=int, default=8765, help="local port (default 8765)")
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser window")
    args = parser.parse_args()
    serve(port=args.port, open_browser=not args.no_browser)


if __name__ == "__main__":
    main()
