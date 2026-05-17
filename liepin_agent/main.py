"""Application entry point."""

from __future__ import annotations

import sys
from pathlib import Path


def _setup_windows_app_id() -> None:
    """Set Windows AppUserModelID so taskbar shows our icon instead of Python's."""
    try:
        import ctypes
        # This tells Windows to treat this as a separate app, not a Python subprocess
        app_id = "LiepinAgent.Workbench.1.0"
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except Exception:
        pass  # Not on Windows or failed, ignore


def main() -> int:
    # Set app ID before creating QApplication (Windows taskbar icon fix)
    if sys.platform == "win32":
        _setup_windows_app_id()

    try:
        from PySide6.QtWidgets import QApplication
        from PySide6.QtGui import QFont
    except ImportError as exc:  # pragma: no cover - depends on local GUI deps
        print("PySide6 未安装，请先运行: python -m pip install -e .[dev]")
        print(str(exc))
        return 1

    from .storage.sqlite_store import SQLiteStore
    from .ui.main_window import MainWindow
    from .ui.icon import create_app_icon

    if getattr(sys, "frozen", False):
        # Running as PyInstaller bundle
        root = Path(sys.executable).parent
    else:
        root = Path(__file__).resolve().parents[1]
    db_path = root / "liepin_agent_workbench.db"

    app = QApplication(sys.argv)
    app.setApplicationName("猎聘寻访 Agent 工作台")

    # Set application icon (affects taskbar and window title)
    app_icon = create_app_icon()
    app.setWindowIcon(app_icon)

    # Set default font for better appearance
    font = QFont("Microsoft YaHei UI", 9)
    font.setStyleStrategy(QFont.PreferAntialias)
    app.setFont(font)

    store = SQLiteStore(str(db_path))
    window = MainWindow(store=store, workspace_root=root)
    window.setWindowIcon(app_icon)  # Also set on window for consistency
    window.resize(1480, 920)
    window.show()
    return app.exec()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

