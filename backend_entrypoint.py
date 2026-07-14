"""PyInstaller entrypoint for the Tauri Python backend sidecar."""

from __future__ import annotations

from liepin_agent.api.__main__ import main


if __name__ == "__main__":
    raise SystemExit(main())
