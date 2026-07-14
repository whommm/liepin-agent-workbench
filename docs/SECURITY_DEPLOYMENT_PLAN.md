# 猎聘寻访 Agent 工作台 — 安全部署与授权计划

> 目标：将当前 PyInstaller 打包方案升级为 Nuitka 原生编译 + 在线授权验证，
> 使破解难度从「5 分钟提取源码」提升至「专业逆向团队也需数天分析」。

---

## 1. 现状与问题

| 项目 | 现状 | 风险 |
|------|------|------|
| 打包工具 | PyInstaller --onefile | 运行时解压到临时目录，.pyc 文件可直接提取反编译 |
| 代码保护 | 无 | 任何会搜索的开发者 5 分钟内可获得完整 Python 源码 |
| 授权验证 | 无 | 复制粘贴即可分发，无法追踪、无法撤销 |
| 项目规模 | ~10,500 行 Python | 核心逻辑（brain.py、runtime.py 等）一旦被复制，商业价值归零 |

### 破解演示（当前状态）

```powershell
# 攻击者只需执行：
pip install pyinstxtractor
python pyinstxtractor.py 猎聘寻访Agent工作台.exe
# 输出目录里找到 liepin_agent/agent/brain.pyc
pip install uncompyle6
uncompyle6 brain.pyc > brain.py  # 源码还原
```

---

## 2. 目标安全等级

| 攻击者类型 | 当前（PyInstaller） | 目标（Nuitka + 授权） |
|-----------|-------------------|---------------------|
| 普通用户 | 无法破解 | 无法破解 |
| 会搜索的开发者 | **5 分钟** | **数小时起步，大概率放弃** |
| 专业逆向工程师 | 30 分钟 | **数天~数周** |
| 国家级/APT | 半天 | 需要专项分析 |

---

## 3. 技术方案总览

```
┌─────────────────────────────────────────────────────────────┐
│  Phase 1: Nuitka 原生编译（核心防线）                        │
│  ├── Python → C++ → x86_64 机器码                            │
│  ├── 无解释器、无 .pyc、无临时解压                           │
│  └── 反编译产出 = 汇编代码（IDA Pro / Ghidra 级别）           │
├─────────────────────────────────────────────────────────────┤
│  Phase 2: 在线授权验证（访问控制）                           │
│  ├── 自有云服务器后端（arm64）                               │
│  ├── 首次启动：输入授权码 → 联网激活 → 缓存 token             │
│  ├── 日常启动：Token 本地缓存 + 定期联网验证                  │
│  └── 支持远程撤销、到期自动失效                              │
├─────────────────────────────────────────────────────────────┤
│  Phase 3: 混合加固（可选增强）                               │
│  ├── Cython 编译关键模块为 .pyd（双保险）                     │
│  ├── UPX 压缩（减小体积，增加静态分析难度）                    │
│  └── EULA 法律声明（启动时强制同意）                         │
└─────────────────────────────────────────────────────────────┘
```

---

## 4. Phase 1: Nuitka 原生编译

### 4.1 原理

```
brain.py ──► Nuitka 前端 ──► brain.cpp ──► MSVC/MinGW ──► brain.o
                                                         │
main_window.py ──► ... ──► main_window.cpp ──► ... ──────┤
                                                         ▼
                                                   entrypoint.exe
                                                    (原生机器码)
```

与 PyInstaller 的本质区别：
- **PyInstaller**：打包 Python 解释器 + .pyc 字节码 → 运行时解压执行 → 源码可提取
- **Nuitka**：将 Python 编译为 C++ → 编译为原生机器码 → 直接由 CPU 执行 → 无源码残留

### 4.2 安装编译环境

```powershell
# 1. 安装 Nuitka
.venv\Scripts\pip install nuitka

# 2. 安装 C++ 编译器（二选一，推荐 MSVC）

# 方案 A：Visual Studio Build Tools（编译最快，推荐）
# 下载地址：https://visualstudio.microsoft.com/downloads/#build-tools-for-visual-studio-2022
# 安装时勾选："使用 C++ 的桌面开发"
# 或命令行安装：
winget install Microsoft.VisualStudio.2022.BuildTools --override "--wait --add Microsoft.VisualStudio.Workload.VCTools"

# 方案 B：MinGW（无需安装 VS，编译稍慢）
.venv\Scripts\pip install mingw64
# 打包时加参数：--mingw64

# 3. 验证安装
.venv\Scripts\python -m nuitka --version
```

### 4.3 编写 Nuitka 打包脚本

新建文件：`nuitka_build.py`

```python
"""Nuitka 打包脚本：将 Python 项目编译为原生机器码 EXE。

使用方法：
    .venv\Scripts\python nuitka_build.py

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
        "--onefile",             # 单文件 EXE（和 PyInstaller 一样）
        "--output-dir=dist",     # 输出到 dist 目录
        f"--output-filename={OUTPUT_NAME}",
        # ---- PySide6 支持 ----
        "--enable-plugin=pyside6",
        # ---- 数据文件 ----
        "--include-data-dir=liepin_agent/prompts=liepin_agent/prompts",
        "--include-data-files=config.json.example=config.json.example",
        # ---- 隐藏导入（Nuitka 无法自动发现的动态导入）----
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
        # ---- Windows 特定 ----
        "--windows-disable-console",       # 无黑窗口（GUI 程序）
        "--windows-icon-from-ico=assets/icon.ico" if (ROOT / "assets/icon.ico").exists() else "",
        # ---- 优化 ----
        "--lto=yes",                       # 链接时优化
        "--jobs=4",                        # 并行编译（根据 CPU 核心数调整）
        # ---- 调试（开发阶段可打开）----
        # "--debug",                       # 开发调试用
        # "--unstripped",                  # 保留符号表（调试用）
        # ---- 入口文件 ----
        "entrypoint.py",
    ]
    # 过滤掉空字符串
    cmd = [c for c in cmd if c]

    print("=" * 60)
    print("Nuitka 编译开始")
    print("=" * 60)
    print(f"命令: {' '.join(cmd)}")
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
        print()
        print("验证方式:")
        print(f"  1. 双击运行: {output}")
        print("  2. 用 7-Zip 打开 EXE，不应看到 .pyc 文件")
        print("  3. 用 IDA Pro / Ghidra 打开，应看到 x86_64 汇编")
    else:
        print("[警告] 未找到输出文件，请检查 dist 目录")


if __name__ == "__main__":
    clean_old_build()
    build()
```

### 4.4 解决常见问题

#### 问题 1：Playwright 浏览器驱动找不到

Nuitka 不会自动包含 Playwright 的浏览器驱动和 Node 脚本，需要手动指定：

```python
# 在 nuitka_build.py 的 cmd 中添加：
import site, playwright
pw_pkg = Path(playwright.__file__).parent

# 包含 Playwright 驱动
"--include-data-files=" + str(pw_pkg / "driver" / "package" / "*.exe") + "=playwright/driver/package",

# 或者更简单的：首次运行时引导用户安装
# playwright install chromium
```

**建议**：在 EXE 中嵌入 `playwright install` 的引导逻辑，或要求用户预先安装。

#### 问题 2：PySide6 资源文件（.qrc）

如果有 `.qrc` 文件，需先用 `pyrcc5` 或 `pyside6-rcc` 编译为 Python 模块：

```powershell
pyside6-rcc resources.qrc -o liepin_agent/ui/resources_rc.py
```

#### 问题 3：动态导入（`importlib.import_module`）

Nuitka 是静态分析，动态导入的模块需显式声明：

```python
# 已在打包脚本中用 --include-package 声明所有子包
# 如有遗漏，运行时会出现 ModuleNotFoundError
# 根据错误信息补充到 --include-package 列表中
```

### 4.5 验证 Nuitka 编译结果

```powershell
# 验证 1：运行测试
.\dist\猎聘寻访Agent工作台-nuitka.exe

# 验证 2：检查是否包含 .pyc（应无）
7z l .\dist\猎聘寻访Agent工作台-nuitka.exe | findstr ".pyc"
# 预期：无任何输出

# 验证 3：检查是否包含 python311.dll（应无）
7z l .\dist\猎聘寻访Agent工作台-nuitka.exe | findstr "python"
# 预期：无任何输出

# 验证 4：用 strings 工具查看是否暴露源码路径
strings .\dist\猎聘寻访Agent工作台-nuitka.exe | findstr "def build_criteria"
# 预期：无任何输出（或极少调试信息）
```

---

## 5. Phase 2: 在线授权验证

### 5.1 架构

```
┌─────────────────┐     HTTPS POST      ┌─────────────────────────┐
│   客户端 EXE    │ ◄─────────────────► │   自有云服务器 (arm64)  │
│  (Nuitka编译)   │                     │   Flask/FastAPI + 数据库 │
└─────────────────┘                     └─────────────────────────┘
        │                                          │
        │  1. activate: 授权码                     │  licenses 表
        │     返回: token + 过期时间               │  ├── code (授权码)
        │                                          │  ├── expire_at
        │  2. verify: token                        │  ├── status
        │     返回: ok / expired / revoked         │  └── max_activations
        │                                          │
        │  3. heartbeat: token                     │  activations 表
        │     返回: ok / revoked                   │  └── 激活记录
```

> **后端实现由你自行完成**，本节只约定客户端 ↔ 服务端接口协议。你可以用 Flask/FastAPI/Node.js 任意技术栈，只要暴露以下三个接口即可。

### 5.2 后端 API 接口约定

**由你在自有云服务器上实现**，技术栈不限（Flask/FastAPI/Node.js 均可）。客户端只关心以下三个接口，返回 JSON 格式。

#### 接口 1：激活（activate）

```
POST /api/auth/activate
Content-Type: application/json

请求体：
{
  "action": "activate",
  "code": "LIE-PIN-2026-001"
}

成功响应（200）：
{
  "ok": true,
  "token": "eyJhbGciOiJIUzI1NiIs...",
  "expire_at": "2026-12-31"
}

失败响应（200，但 ok=false）：
{
  "ok": false,
  "msg": "授权码无效或已过期"
}
```

**服务端逻辑建议**：
1. 查 `licenses` 表，确认 `code` 存在且 `status='active'`
2. 检查 `expire_at` 是否已过期
3. 查 `activations` 表，确认激活次数未超过 `max_activations`
4. 生成 JWT Token（含 `code` + `exp`）
5. 写入 `activations` 表，返回 token

#### 接口 2：验证（verify）

```
POST /api/auth/verify
Content-Type: application/json

请求体：
{
  "action": "verify",
  "token": "eyJhbGciOiJIUzI1NiIs..."
}

成功响应：
{ "ok": true, "code": "LIE-PIN-2026-001" }

失败响应：
{ "ok": false, "msg": "授权已过期" }
```

**服务端逻辑建议**：
1. 验签 JWT Token
2. 查 `licenses` 表确认 `status='active'`
3. 检查 `expire_at`
4. 返回结果

#### 接口 3：心跳（heartbeat）

```
POST /api/auth/heartbeat
Content-Type: application/json

请求体：
{
  "action": "heartbeat",
  "token": "eyJhbGciOiJIUzI1NiIs..."
}

成功响应：
{ "ok": true }
```

**服务端逻辑建议**：
1. 验签 JWT Token
2. 更新该 token 的 `last_seen` 时间

#### 数据库表结构建议

```sql
CREATE TABLE licenses (
    code VARCHAR(64) PRIMARY KEY,
    max_activations INTEGER DEFAULT 1,
    expire_at DATE,
    status VARCHAR(16) DEFAULT 'active',  -- active / revoked / expired
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE activations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code VARCHAR(64),
    token TEXT,
    activated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_seen DATETIME,
    FOREIGN KEY (code) REFERENCES licenses(code)
);
```

#### 一个极简的 Flask 参考实现

```python
# server.py — 极简授权服务器（供参考，你用自己的实现替换）
from flask import Flask, request, jsonify
import jwt
import datetime

app = Flask(__name__)
SECRET = "your-secret-key-here"  # 改为强密钥

# TODO: 替换为真实数据库查询
LICENSES_DB = {
    "LIE-PIN-2026-001": {
        "max_activations": 1,
        "expire_at": "2026-12-31",
        "status": "active"
    }
}
ACTIVATIONS = {}  # token -> {code, activated_at}


@app.route("/api/auth/activate", methods=["POST"])
def activate():
    data = request.get_json() or {}
    code = data.get("code", "").strip()

    lic = LICENSES_DB.get(code)
    if not lic or lic["status"] != "active":
        return jsonify({"ok": False, "msg": "授权码无效或已被禁用"})

    if datetime.date.today() > datetime.date.fromisoformat(lic["expire_at"]):
        return jsonify({"ok": False, "msg": "授权码已过期"})

    # 检查激活次数
    count = sum(1 for a in ACTIVATIONS.values() if a["code"] == code)
    if count >= lic["max_activations"]:
        return jsonify({"ok": False, "msg": "授权码激活次数已达上限"})

    # 生成 JWT
    payload = {
        "code": code,
        "exp": datetime.datetime(2026, 12, 31, 23, 59, 59),
    }
    token = jwt.encode(payload, SECRET, algorithm="HS256")
    ACTIVATIONS[token] = {"code": code, "activated_at": datetime.datetime.now()}

    return jsonify({"ok": True, "token": token, "expire_at": lic["expire_at"]})


@app.route("/api/auth/verify", methods=["POST"])
def verify():
    data = request.get_json() or {}
    token = data.get("token", "")

    try:
        payload = jwt.decode(token, SECRET, algorithms=["HS256"])
        code = payload["code"]
        lic = LICENSES_DB.get(code)
        if not lic or lic["status"] != "active":
            return jsonify({"ok": False, "msg": "授权已被撤销"})
        return jsonify({"ok": True, "code": code})
    except jwt.ExpiredSignatureError:
        return jsonify({"ok": False, "msg": "Token 已过期"})
    except jwt.InvalidTokenError:
        return jsonify({"ok": False, "msg": "Token 无效"})


@app.route("/api/auth/heartbeat", methods=["POST"])
def heartbeat():
    data = request.get_json() or {}
    token = data.get("token", "")

    try:
        payload = jwt.decode(token, SECRET, algorithms=["HS256"])
        if token in ACTIVATIONS:
            ACTIVATIONS[token]["last_seen"] = datetime.datetime.now()
        return jsonify({"ok": True})
    except jwt.InvalidTokenError:
        return jsonify({"ok": False, "msg": "Token 无效"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
```

> 以上 Flask 代码仅为接口参考，**你需要自行实现完整版本**（接入真实数据库、WebUI 管理后台等）。

### 5.3 客户端：授权验证模块

新建文件：`liepin_agent/services/licensing.py`

```python
"""在线授权验证模块。

流程：
1. 首次启动：弹出激活窗口 → 输入授权码 → 联网激活 → 缓存 token
2. 后续启动：读取本地 token → 联网验证 → 通过则继续
3. 离线场景：token 本地缓存，允许离线宽限期（如 7 天）
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from PySide6.QtWidgets import (
    QDialog,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QLabel,
)

# 你的授权服务器地址（替换为实际地址）
AUTH_API_URL = "https://your-server.com/api/auth"
OFFLINE_GRACE_DAYS = 7  # 离线宽限期（天）


class LicenseManager:
    """授权管理器。"""

    def __init__(self, workspace_root: Path):
        self._token_file = workspace_root / ".auth.json"

    def verify_or_activate(self) -> bool:
        """启动时调用。返回 True 表示授权有效，可继续启动。"""
        # 1. 尝试本地验证
        if self._local_verify():
            return True

        # 2. 尝试联网验证
        if self._online_verify():
            return True

        # 3. 都失败了，弹出激活窗口
        return self._show_activation_dialog()

    def _local_verify(self) -> bool:
        """本地 token 验证（支持离线）。"""
        if not self._token_file.exists():
            return False

        try:
            data = json.loads(self._token_file.read_text(encoding="utf-8"))
            token = data.get("token", "")
            verified_at = data.get("verified_at", "")

            if not token or not verified_at:
                return False

            # 检查离线宽限期
            last_verify = datetime.fromisoformat(verified_at)
            if datetime.now() - last_verify > timedelta(days=OFFLINE_GRACE_DAYS):
                return False  # 离线太久，必须联网

            return True
        except Exception:
            return False

    def _online_verify(self) -> bool:
        """联网验证 token。"""
        if not self._token_file.exists():
            return False

        try:
            data = json.loads(self._token_file.read_text(encoding="utf-8"))
            token = data.get("token", "")

            import requests
            resp = requests.post(
                AUTH_API_URL,
                json={"action": "verify", "token": token},
                timeout=10,
            )
            result = resp.json()

            if result.get("ok"):
                # 更新本地验证时间
                data["verified_at"] = datetime.now().isoformat()
                self._token_file.write_text(json.dumps(data), encoding="utf-8")
                return True
        except Exception:
            pass  # 网络异常，回退到本地验证

        return False

    def _activate(self, code: str) -> bool:
        """向服务器申请激活。"""
        try:
            import requests
            resp = requests.post(
                AUTH_API_URL,
                json={
                    "action": "activate",
                    "code": code.strip(),
                },
                timeout=15,
            )
            result = resp.json()

            if result.get("ok"):
                # 保存 token
                self._token_file.write_text(
                    json.dumps({
                        "token": result["token"],
                        "expire_at": result.get("expire_at", ""),
                        "verified_at": datetime.now().isoformat(),
                    }, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                return True
            else:
                return False
        except Exception as exc:
            print(f"激活请求失败: {exc}")
            return False

    def _show_activation_dialog(self) -> bool:
        """弹出激活窗口，用户输入授权码。"""
        dlg = QDialog()
        dlg.setWindowTitle("软件激活")
        dlg.setMinimumWidth(400)

        layout = QVBoxLayout()
        layout.addWidget(QLabel("请输入授权码激活软件。"))
        layout.addSpacing(10)

        code_input = QLineEdit()
        code_input.setPlaceholderText("请输入授权码，如 LIE-PIN-2026-001")
        layout.addWidget(code_input)

        btn = QPushButton("激活")
        layout.addWidget(btn)

        result = {"ok": False}

        def on_activate():
            code = code_input.text().strip()
            if not code:
                QMessageBox.warning(dlg, "错误", "请输入授权码")
                return

            if self._activate(code):
                result["ok"] = True
                dlg.accept()
            else:
                QMessageBox.warning(dlg, "激活失败", "授权码无效、已过期或设备数已达上限。")

        btn.clicked.connect(on_activate)
        dlg.setLayout(layout)

        return dlg.exec() == QDialog.DialogCode.Accepted and result["ok"]


def check_license(workspace_root: Path) -> bool:
    """启动入口函数。返回 False 时程序应退出。"""
    mgr = LicenseManager(workspace_root)
    if mgr.verify_or_activate():
        return True
    return False
```

### 5.4 集成到 main.py

在 `main.py` 的 `main()` 函数开头插入授权检查：

```python
def main() -> int:
    _setup_logging()
    if sys.platform == "win32":
        _setup_windows_app_id()

    # ===== 新增：授权验证 =====
    from pathlib import Path
    from .services.licensing import check_license

    if getattr(sys, "frozen", False):
        root = Path(sys.executable).parent
    else:
        root = Path(__file__).resolve().parents[1]

    if not check_license(root):
        print("授权验证失败，程序退出。")
        return 1
    # ==========================

    # ... 原有代码继续 ...
```

---

## 6. Phase 3: 混合加固（可选增强）

### 6.1 Cython 编译核心模块

如果 Nuitka 编译整个项目后仍然担心核心逻辑暴露，可将 `brain.py`、`runtime.py` 等用 Cython 编译为 `.pyd`（机器码模块），**双保险**。

新建 `setup_cython.py`：

```python
"""将关键 Python 模块编译为 Cython .pyd（机器码）。

使用方法：
    .venv\Scripts\python setup_cython.py build_ext --inplace

产物：
    liepin_agent/agent/brain.cp311-win_amd64.pyd
    liepin_agent/agent/runtime.cp311-win_amd64.pyd
"""

from setuptools import setup
from Cython.Build import cythonize
from pathlib import Path

modules = [
    "liepin_agent/agent/brain.py",
    "liepin_agent/agent/runtime.py",
    "liepin_agent/tools/llm_client.py",
]

setup(
    ext_modules=cythonize(
        modules,
        compiler_directives={
            "language_level": "3",
            "embedsignature": False,
        },
        annotate=False,
    ),
    zip_safe=False,
)
```

```powershell
# 编译
.venv\Scripts\pip install cython
.venv\Scripts\python setup_cython.py build_ext --inplace

# 验证产物
ls liepin_agent/agent/*.pyd
# 应看到 brain.cp311-win_amd64.pyd 等文件
```

### 6.2 法律声明（EULA）

在 `main.py` 中授权检查之前加入：

```python
from PySide6.QtWidgets import QMessageBox

def show_eula() -> bool:
    """显示最终用户许可协议，必须点击"同意"才能继续。"""
    text = (
        "猎聘寻访 Agent 工作台 最终用户许可协议 (EULA)\n\n"
        "1. 本软件受著作权法和国际条约保护。\n"
        "2. 未经授权，禁止反编译、逆向工程、破解或分发。\n"
        "3. 每个授权码仅限指定数量的设备使用。\n"
        "4. 违反本协议将承担相应法律责任。\n\n"
        "点击"同意"即表示您接受以上条款。"
    )
    reply = QMessageBox.question(
        None, "许可协议", text,
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
    )
    return reply == QMessageBox.StandardButton.Yes
```

---

## 7. 打包脚本汇总

### 7.1 完整打包流程（一键脚本）

新建 `build_production.bat`：

```batch
@echo off
chcp 65001 >nul
echo ==========================================
echo  猎聘寻访 Agent 工作台 — 生产环境打包
echo ==========================================
echo.

set VENV=.venv

REM 1. 激活虚拟环境
call %VENV%\Scripts\activate.bat

REM 2. 可选：Cython 编译核心模块
echo [1/4] 编译 Cython 核心模块...
%VENV%\Scripts\python setup_cython.py build_ext --inplace

REM 3. Nuitka 编译
echo [2/4] Nuitka 编译为原生 EXE...
%VENV%\Scripts\python nuitka_build.py

REM 4. 验证
echo [3/4] 验证编译产物...
if exist "dist\猎聘寻访Agent工作台-nuitka.exe" (
    echo [OK] 编译成功
) else (
    echo [错误] 未找到输出文件
    pause
    exit /b 1
)

REM 5. 复制辅助文件
echo [4/4] 复制辅助文件...
if not exist "dist\browser_profile" mkdir "dist\browser_profile"

echo.
echo ==========================================
echo  打包完成！
echo ==========================================
echo.
echo 输出文件: dist\猎聘寻访Agent工作台-nuitka.exe
echo.
echo 安全验证清单:
echo   [ ] 用 7-Zip 打开 EXE，确认无 .pyc 文件
echo   [ ] 运行 EXE，弹出授权窗口
echo   [ ] 输入授权码，激活成功
echo   [ ] 重启 EXE，无需再次激活
echo   [ ] 复制 EXE 到另一台机器，需要重新激活
echo.
pause
```

---

## 8. 实施时间表

| 阶段 | 任务 | 预计时间 | 依赖 |
|------|------|---------|------|
| **Day 1 上午** | 安装 MSVC Build Tools + Nuitka | 30 分钟 | 网络下载 |
| | 编写 `nuitka_build.py`，首次编译测试 | 2 小时 | 上一步 |
| | 解决编译问题（PySide6、Playwright、数据文件） | 2~4 小时 | 上一步 |
| **Day 1 下午** | 验证 Nuitka 产物（运行、反编译测试） | 1 小时 | 上一步 |
| | 部署自有授权服务器（arm64） | 1 小时 | 云服务器 |
| | 编写 `licensing.py` 客户端模块 | 1.5 小时 | 上一步 |
| | 集成到 `main.py`，端到端测试 | 1 小时 | 上一步 |
| **Day 2 上午** | Cython 编译核心模块（可选增强） | 1 小时 | Day 1 |
| | 编写 EULA 弹窗 | 30 分钟 | |
| | 编写 `build_production.bat` 一键脚本 | 30 分钟 | |
| | 完整端到端测试（新机器、授权码、过期、撤销） | 2 小时 | |
| **合计** | | **~2 天** | |

---

## 9. 验证清单

### 9.1 Nuitka 编译验证

| # | 检查项 | 方法 | 预期结果 |
|---|--------|------|---------|
| 1 | 无 .pyc 残留 | `7z l xxx.exe \| findstr .pyc` | 无输出 |
| 2 | 无 python.dll | `7z l xxx.exe \| findstr python` | 无输出 |
| 3 | 无源码字符串 | `strings xxx.exe \| findstr "def build_criteria"` | 无输出 |
| 4 | 程序正常运行 | 双击 EXE | 正常启动 |
| 5 | 功能完整性 | 完整跑一次寻访流程 | 无报错 |

### 9.2 授权验证

| # | 检查项 | 方法 | 预期结果 |
|---|--------|------|---------|
| 1 | 首次启动需激活 | 新环境运行 EXE | 弹出激活窗口 |
| 2 | 无效授权码拒绝 | 输入 "123" | 提示无效 |
| 3 | 有效授权码通过 | 输入正确授权码 | 激活成功，进入主界面 |
| 4 | 重复激活拒绝 | 同一授权码在第二台机器 | 提示设备数已达上限 |
| 5 | 重启免激活 | 关闭后再次打开 | 直接进入主界面 |
| 6 | 离线运行 | 断网后启动 | 在宽限期内正常启动 |
| 7 | 过期失效 | 等待授权到期（或改系统时间） | 提示过期，要求重新激活 |
| 8 | 远程撤销 | 在管理后台将授权码 status 改为 revoked | 客户端下次验证失败 |

---

## 10. 风险与回退

| 风险 | 概率 | 影响 | 应对措施 |
|------|------|------|---------|
| Nuitka 编译失败 | 中 | 高 | 保留 PyInstaller 打包脚本，可瞬间回退 |
| Nuitka 与 PySide6 不兼容 | 低 | 高 | Nuitka 官方维护 pyside6 插件，社区活跃 |
| 授权服务器宕机 | 低 | 中 | 离线宽限期（7 天），服务器恢复后自动恢复验证 |
| 用户无网络环境 | 低 | 中 | 离线宽限期（7 天），首次激活后可离线运行 |
| Nuitka 编译产物过大 | 中 | 低 | 开启 UPX 压缩，产物约 150~200MB（可接受） |
| 编译速度过慢 | 中 | 低 | 使用 `--jobs=8`（根据 CPU 核心数），或使用 ccache |

---

## 11. 成本估算

| 项目 | 费用 | 说明 |
|------|------|------|
| 自有云服务器 | 已拥有 | arm64 云服务器，运行授权后端 |
| Visual Studio Build Tools | **免费** | 社区版 |
| Nuitka | **免费** | 开源 |
| Cython | **免费** | 开源 |
| **总计** | **0 元** | |

---

## 12. 下一步行动

1. **确认执行**：是否开始实施？
2. **服务端准备**：在 arm64 云服务器上部署授权后端（Flask/FastAPI）
3. **并行执行**：
   - 我编写 Nuitka 打包脚本 + 授权客户端代码
   - 你搭建授权服务器并插入测试授权码
4. **联调测试**：客户端 ↔ 服务端端到端验证
5. **交付**：一键打包脚本 + 使用文档

---

*文档版本: v1.0*
*创建日期: 2026-05-24*
*适用项目: liepin-agent-workbench*
