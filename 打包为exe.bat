@echo off
chcp 65001 >nul
echo ==========================================
echo  猎聘寻访 Agent 工作台 - 打包为 EXE
echo ==========================================
echo.

set VENV_DIR=.venv
set SPEC_FILE=liepin_agent_workbench.spec
set DIST_DIR=dist
set BUILD_DIR=build
set EXE_NAME=猎聘寻访Agent工作台.exe

REM 检查虚拟环境
if not exist "%VENV_DIR%\Scripts\activate.bat" (
    echo [错误] 未找到虚拟环境：%VENV_DIR%
    echo 请先运行：创建虚拟环境.bat
    pause
    exit /b 1
)

echo [1/4] 激活虚拟环境...
call %VENV_DIR%\Scripts\activate.bat

echo [2/4] 检查 PyInstaller...
python -c "import PyInstaller" >nul 2>&1
if errorlevel 1 (
    echo [错误] PyInstaller 未安装，正在安装...
    pip install pyinstaller
    if errorlevel 1 (
        echo [错误] PyInstaller 安装失败
        pause
        exit /b 1
    )
)

echo [3/4] 清理旧构建...
if exist "%DIST_DIR%" rmdir /s /q "%DIST_DIR%"
if exist "%BUILD_DIR%" rmdir /s /q "%BUILD_DIR%"

echo [4/4] 开始打包（这可能需要几分钟）...
pyinstaller %SPEC_FILE% --clean --noconfirm
if errorlevel 1 (
    echo.
    echo [错误] 打包失败，请查看上方日志
    pause
    exit /b 1
)

echo.
echo ==========================================
echo  打包成功！
echo ==========================================
echo.
echo 输出文件：%CD%\%DIST_DIR%\%EXE_NAME%
echo.
echo 使用说明：
echo   1. 将 %EXE_NAME% 复制到任意位置
echo   2. 双击 %EXE_NAME% 启动
echo.
echo 注意：
echo   - 首次启动会稍慢（需要解压依赖到临时目录）
echo   - config.json 和 .env 会在首次运行时自动生成
echo   - liepin_agent_workbench.db 数据库文件会在 exe 同级目录创建
echo   - browser_profile 目录也会在 exe 同级目录创建
echo   - 若目标机器已安装 Edge 或 Chrome，程序会自动调用系统浏览器
echo   - 仅在无 Edge/Chrome 时，才需运行：playwright install chromium
echo.
pause
