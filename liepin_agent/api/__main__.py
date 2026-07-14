"""Command-line entrypoint for the workbench API backend."""

from __future__ import annotations

import argparse

import uvicorn

from .app import create_app


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Liepin Agent Workbench API backend")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--workspace-root", default="")
    args = parser.parse_args()

    app = create_app(args.workspace_root or None)
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
