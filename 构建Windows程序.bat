@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

set "PYEXE=%~dp0.venv\Scripts\python.exe"

if not exist "%PYEXE%" (
    echo 未找到运行环境，请先双击「启动.bat」完成首次环境安装。
    pause
    exit /b 1
)

echo 正在安装打包工具……
"%PYEXE%" -m pip install -r requirements-build.txt
if errorlevel 1 goto :error

echo 正在清理旧构建……
if exist "build\抖音自动消息" (
    attrib -R "build\抖音自动消息\*" /S /D >nul 2>&1
    rmdir /s /q "build\抖音自动消息"
)
if exist "dist\抖音自动消息" (
    attrib -R "dist\抖音自动消息\*" /S /D >nul 2>&1
    rmdir /s /q "dist\抖音自动消息"
)

echo 正在构建 Windows 程序……
"%PYEXE%" -m PyInstaller --noconfirm --clean "抖音自动消息.spec"
if errorlevel 1 goto :error

echo 正在清理构建目录中的账号数据……
if exist "dist\抖音自动消息\data" rmdir /s /q "dist\抖音自动消息\data"
if exist "dist\抖音自动消息\logs" rmdir /s /q "dist\抖音自动消息\logs"

echo.
echo 构建完成：
echo dist\抖音自动消息\抖音自动消息.exe
echo.
echo 直接双击该 exe 即可运行，首次启动会自动创建 data 和 logs 目录。
pause
exit /b 0

:error
echo.
echo 构建失败，请查看上方错误信息。
pause
exit /b 1
