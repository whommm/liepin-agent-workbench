# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Tauri Python backend sidecar."""

from pathlib import Path
import glob

ROOT = Path(SPECPATH).resolve()

prompt_files = glob.glob(str(ROOT / "liepin_agent" / "prompts" / "*.md"))
prompt_datas = [(f, "liepin_agent/prompts") for f in prompt_files]
config_files = glob.glob(str(ROOT / "liepin_agent" / "config" / "*.json"))
config_datas = [(f, "liepin_agent/config") for f in config_files]

other_datas = [
    (str(ROOT / "config.json.example"), "."),
]

all_datas = prompt_datas + config_datas + other_datas

a = Analysis(
    ['backend_entrypoint.py'],
    pathex=[str(ROOT)],
    binaries=[],
    datas=all_datas,
    hiddenimports=[
        "fastapi",
        "fastapi.middleware.cors",
        "uvicorn",
        "uvicorn.loops",
        "uvicorn.loops.auto",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets",
        "uvicorn.lifespan",
        "uvicorn.lifespan.on",
        "playwright",
        "playwright.sync_api",
        "playwright.async_api",
        "playwright.driver",
        "openpyxl",
        "openpyxl.cell._writer",
        "openpyxl.styles",
        "openpyxl.utils.datetime",
        "openai",
        "openai._base_client",
        "openai.resources",
        "openai.types",
        "openai.types.chat",
        "anthropic",
        "pydantic",
        "liepin_agent.api",
        "liepin_agent.agent",
        "liepin_agent.core",
        "liepin_agent.domain",
        "liepin_agent.models",
        "liepin_agent.prompts",
        "liepin_agent.services",
        "liepin_agent.storage",
        "liepin_agent.tools",
        "liepin_agent.utils",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "PySide6",
        "pytest",
        "unittest",
        "tkinter",
        "matplotlib",
        "numpy",
        "scipy",
        "pandas",
        "IPython",
        "jupyter",
        "notebook",
        "cv2",
        "tensorflow",
        "torch",
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='liepin-agent-backend',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
