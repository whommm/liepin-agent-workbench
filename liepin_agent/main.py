"""Application entry point."""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError as exc:  # pragma: no cover - depends on local GUI deps
        print("PySide6 未安装，请先运行: python -m pip install -e .[dev]")
        print(str(exc))
        return 1

    from .storage.sqlite_store import SQLiteStore
    from .ui.main_window import MainWindow

    if getattr(sys, "frozen", False):
        # Running as PyInstaller bundle
        root = Path(sys.executable).parent
    else:
        root = Path(__file__).resolve().parents[1]
    db_path = root / "liepin_agent_workbench.db"

    app = QApplication(sys.argv)
    app.setApplicationName("猎聘寻访 Agent 工作台")
    store = SQLiteStore(str(db_path))
    window = MainWindow(store=store, workspace_root=root)
    window.resize(1480, 920)
    window.show()
    return app.exec()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

