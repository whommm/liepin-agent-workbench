"""Nuitka 打包脚本：将 Python 项目编译为原生机器码 EXE。

使用方法：
    python nuitka_build.py

首次编译约 5~15 分钟（取决于机器性能），后续增量编译约 1~3 分钟。
输出文件：dist\猎聘寻访Agent工作台-nuitka.exe
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST_DIR = ROOT / "dist"
BUILD_DIR = ROOT / "build" / "nuitka"
OUTPUT_NAME = "猎聘寻访Agent工作台-nuitka"


def clean_old_build():
    """清理旧的 Nuitka 构建目录以强制全量编译。"""
    for d in [BUILD_DIR, DIST_DIR / f"{OUTPUT_NAME}.exe"]:
        if d.exists():
            if d.is_dir():
                shutil.rmtree(d)
            else:
                d.unlink()
            print(f"[清理] {d}")


def build():
    cmd = [
        sys.executable,
        "-m",
        "nuitka",
        # ---- 输出模式 ----
        "--standalone",          # 独立运行，不依赖系统 Python
        "--onefile",             # 单文件 EXE
        "--output-dir=dist",     # 输出到 dist 目录
        f"--output-filename={OUTPUT_NAME}",
        # ---- PySide6 支持 ----
        "--enable-plugin=pyside6",
        # ---- 数据文件 ----
        "--include-data-dir=liepin_agent/prompts=liepin_agent/prompts",
        "--include-data-files=config.json.example=config.json.example",
        # ---- 隐藏导入 ----
        "--include-package=playwright",
        "--include-package=playwright.async_api",
        "--include-package=playwright.sync_api",
        "--include-package=openpyxl",
        "--include-package=openai",
        "--include-package=anthropic",
        "--include-package=pydantic",
        "--include-package=liepin_agent.agent",
        "--include-package=liepin_agent.core",
        "--include-package=liepin_agent.domain",
        "--include-package=liepin_agent.models",
        "--include-package=liepin_agent.prompts",
        "--include-package=liepin_agent.services",
        "--include-package=liepin_agent.storage",
        "--include-package=liepin_agent.tools",
        "--include-package=liepin_agent.ui",
        "--include-package=liepin_agent.utils",
        # ---- 排除不需要的大型库（避免 depends.exe 分析失败）----
        "--nofollow-import-to=scipy",
        "--nofollow-import-to=numpy",
        "--nofollow-import-to=pandas",
        "--nofollow-import-to=matplotlib",
        "--nofollow-import-to=PIL",
        "--nofollow-import-to=IPython",
        "--nofollow-import-to=jupyter",
        "--nofollow-import-to=notebook",
        "--nofollow-import-to=cv2",
        "--nofollow-import-to=tensorflow",
        "--nofollow-import-to=torch",
        "--nofollow-import-to=pytest",
        "--nofollow-import-to=unittest",
        "--nofollow-import-to=tkinter",
        # ---- Windows 特定 ----
        "--windows-console-mode=disable",  # 无黑窗口（GUI 程序）
        "--assume-yes-for-downloads",      # 自动下载依赖工具
        # ---- 优化 ----
        "--lto=yes",                       # 链接时优化
        "--jobs=4",                        # 并行编译
        # ---- 入口文件 ----
        "entrypoint.py",
    ]

    print("=" * 60)
    print("Nuitka 编译开始")
    print("=" * 60)
    print(f"Python: {sys.executable}")
    print()
    print("首次编译约需 5~15 分钟，请耐心等待...")
    print()

    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode != 0:
        print("[错误] Nuitka 编译失败，请查看上方日志")
        sys.exit(1)

    output = DIST_DIR / f"{OUTPUT_NAME}.exe"
    if output.exists():
        size_mb = output.stat().st_size / (1024 * 1024)
        print()
        print("=" * 60)
        print("编译成功！")
        print("=" * 60)
        print(f"输出文件: {output}")
        print(f"文件大小: {size_mb:.1f} MB")
    else:
        print("[警告] 未找到输出文件，请检查 dist 目录")


if __name__ == "__main__":
    clean_old_build()
    build()
