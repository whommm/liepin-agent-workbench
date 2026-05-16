"""Standalone entry point for PyInstaller bundle.

This module is used as the PyInstaller analysis entry point instead of
liepin_agent/main.py, because the latter uses relative imports and must
be imported as part of the ``liepin_agent`` package.
"""

from liepin_agent.main import main

if __name__ == "__main__":
    raise SystemExit(main())
