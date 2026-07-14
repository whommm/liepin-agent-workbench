@echo off
chcp 65001 >nul
echo ==========================================
echo  猎聘寻访 Agent 工作台 - 后端 Sidecar 打包
echo ==========================================
echo.

set VENV_DIR=.venv
set SPEC_FILE=liepin_agent_backend.spec
set DIST_DIR=dist
set BACKEND_EXE=liepin-agent-backend.exe
set TAURI_BIN_DIR=..\liepin-agent-tauri-workbench\src-tauri\binaries
set TAURI_BACKEND_EXE=liepin-agent-backend-x86_64-pc-windows-msvc.exe

if exist "%VENV_DIR%\Scripts\activate.bat" (
    echo [1/5] 激活虚拟环境...
    call %VENV_DIR%\Scripts\activate.bat
) else (
    echo [1/5] 未找到 .venv，使用当前 Python 环境...
)

echo [2/5] 检查 PyInstaller...
python -c "import PyInstaller" >nul 2>&1
if errorlevel 1 (
    echo [错误] PyInstaller 未安装，正在安装...
    pip install pyinstaller
    if errorlevel 1 exit /b 1
)

echo [3/5] 开始打包后端 sidecar...
pyinstaller %SPEC_FILE% --clean --noconfirm
if errorlevel 1 (
    echo [错误] 后端打包失败
    exit /b 1
)

if not exist "%DIST_DIR%\%BACKEND_EXE%" (
    echo [错误] 未找到输出文件：%DIST_DIR%\%BACKEND_EXE%
    exit /b 1
)

echo [4/5] 复制到 Tauri binaries...
if not exist "%TAURI_BIN_DIR%" mkdir "%TAURI_BIN_DIR%"
copy /Y "%DIST_DIR%\%BACKEND_EXE%" "%TAURI_BIN_DIR%\%TAURI_BACKEND_EXE%" >nul
if errorlevel 1 (
    echo [错误] 复制 sidecar 失败
    exit /b 1
)

echo [5/5] 完成
echo 输出：%CD%\%TAURI_BIN_DIR%\%TAURI_BACKEND_EXE%
