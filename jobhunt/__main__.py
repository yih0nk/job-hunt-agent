"""`python -m jobhunt` — start the local server (and open the UI unless --no-browser).

The Electron shell launches this with --no-browser --port <free port> and JOBHUNT_TOKEN
set, then loads the window itself.
"""
import argparse
import os
import socket
import threading
import webbrowser


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main():
    ap = argparse.ArgumentParser(prog="jobhunt")
    ap.add_argument("--port", type=int, default=int(os.environ.get("JOBHUNT_PORT", 0)) or None)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    port = args.port or free_port()

    import uvicorn
    from .server import app

    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(f"http://127.0.0.1:{port}/")).start()
    print(f"JOBHUNT_READY http://127.0.0.1:{port}/", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
