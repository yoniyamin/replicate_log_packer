"""`logpacker` / `python -m logpacker`: start the local app and open the browser."""

from __future__ import annotations

import argparse
import threading
import webbrowser

import uvicorn

from logpacker.config import get_settings


def main() -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(prog="logpacker", description=__doc__)
    parser.add_argument("--port", type=int, default=settings.port)
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser tab")
    args = parser.parse_args()

    url = f"http://{settings.host}:{args.port}/"
    if not args.no_browser:
        threading.Timer(1.2, webbrowser.open, args=(url,)).start()
    print(f"LogPacker running at {url}  (Ctrl+C to stop)")
    uvicorn.run("logpacker.web.app:create_app", factory=True, host=settings.host, port=args.port,
                log_level="warning")


if __name__ == "__main__":
    main()
