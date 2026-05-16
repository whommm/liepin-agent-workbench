@echo off
chcp 65001 >nul
echo ==========================================
echo  猎聘寻访 Agent 工作台 - 创建虚拟环境
echo ==========================================
echo.

set VENV_DIR=.venv
set PYTHON=python

REM 检查 python 是否可用
%PYTHON% --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 python，请确保 Python 3.10+ 已安装并添加到 PATH
    pause
    exit /b 1
)

echo [1/4] 检测到 Python 版本：
%PYTHON% --version
echo.

REM 如果虚拟环境已存在，询问是否重建
if exist "%VENV_DIR%\Scripts\python.exe" (
    echo [提示] 虚拟环境已存在：%VENV_DIR%
    set /p REBUILD="是否重建？(y/N): "
    if /i "!REBUILD!"=="y" (
        echo [1/4] 删除旧虚拟环境...
        rmdir /s /q "%VENV_DIR%"
    ) else (
        echo [1/4] 使用现有虚拟环境
        goto :install_deps
    )
)

echo [1/4] 创建虚拟环境...
%PYTHON% -m venv %VENV_DIR%
if errorlevel 1 (
    echo [错误] 创建虚拟环境失败
    pause
    exit /b 1
)

:install_deps
echo.
echo [2/4] 安装项目依赖...
call %VENV_DIR%\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -e .[dev,build]
if errorlevel 1 (
    echo [错误] 安装依赖失败
    pause
    exit /b 1
)

echo.
echo [3/4] 安装 Playwright 浏览器（如系统已有 Edge/Chrome 可跳过）...
python -m playwright install chromium
if errorlevel 1 (
    echo [提示] Playwright 浏览器安装失败或已跳过。若系统已安装 Edge/Chrome，则无需此步骤。
)

echo.
echo [4/4] 完成！
echo.
echo ==========================================
echo  虚拟环境已准备就绪
echo ==========================================
echo.
echo 启动程序：     .venv\Scripts\python -m liepin_agent.main
echo 运行测试：     .venv\Scripts\python -m pytest
echo 打包为 exe：   打包为exe.bat
echo.
pause
