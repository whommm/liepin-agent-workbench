# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for 猎聘寻访 Agent Workbench (onefile mode)."""

from pathlib import Path
import glob

ROOT = Path(SPECPATH).resolve()

# Collect all prompt markdown files
prompt_files = glob.glob(str(ROOT / "liepin_agent" / "prompts" / "*.md"))
prompt_datas = [(f, "liepin_agent/prompts") for f in prompt_files]

# Other runtime data files to bundle
other_datas = [
    (str(ROOT / "config.json.example"), "."),
]

all_datas = prompt_datas + other_datas

a = Analysis(
    ['entrypoint.py'],
    pathex=[str(ROOT)],
    binaries=[],
    datas=all_datas,
    hiddenimports=[
        # Playwright
        "playwright",
        "playwright.sync_api",
        "playwright.async_api",
        "playwright._impl._api_structures",
        "playwright._impl._connection",
        "playwright._impl._browser",
        "playwright._impl._browser_context",
        "playwright._impl._page",
        "playwright._impl._frame",
        "playwright._impl._js_handle",
        "playwright._impl._element_handle",
        "playwright._impl._network",
        "playwright._impl._locator",
        "playwright._impl._errors",
        "playwright._impl._event_context_manager",
        "playwright._impl._fetch",
        "playwright._impl._file_chooser",
        "playwright._impl._helper",
        "playwright._impl._impl_to_api_mapping",
        "playwright._impl._input",
        "playwright._impl._local_utils",
        "playwright._impl._map",
        "playwright._impl._object_factory",
        "playwright._impl._playwright",
        "playwright._impl._selectors",
        "playwright._impl._set_input_files_helpers",
        "playwright._impl._str_utils",
        "playwright._impl._tracing",
        "playwright._impl._video",
        "playwright._impl._waiter",
        "playwright._impl._web_error",
        "playwright._impl._writable_stream",
        "playwright.driver",
        # Openpyxl
        "openpyxl",
        "openpyxl.cell._writer",
        "openpyxl.styles",
        "openpyxl.styles.numbers",
        "openpyxl.utils.datetime",
        "openpyxl.chart",
        "openpyxl.chart.series_factory",
        "openpyxl.chart.reference",
        # OpenAI
        "openai",
        "openai._base_client",
        "openai.resources",
        "openai.types",
        "openai.types.chat",
        # Liepin agent subpackages
        "liepin_agent.agent",
        "liepin_agent.core",
        "liepin_agent.domain",
        "liepin_agent.models",
        "liepin_agent.prompts",
        "liepin_agent.services",
        "liepin_agent.storage",
        "liepin_agent.tools",
        "liepin_agent.ui",
        "liepin_agent.utils",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
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
    name='猎聘寻访Agent工作台',
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
