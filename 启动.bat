@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

set "PYEXE=%~dp0.venv\Scripts\python.exe"
set "PYCMD="

rem 依次尝试可用的解释器，只接受 3.9 及以上版本
call :try_python py -3.13
if not defined PYCMD call :try_python py -3
if not defined PYCMD call :try_python python
if not defined PYCMD (
    echo 没有找到 Python 3.9 或更高版本，请先安装 Python 3.13 后重试。
    echo 下载地址：https://www.python.org/downloads/
    pause
    exit /b 1
)

call :check_venv
if errorlevel 1 call :prepare_venv
if not exist "%PYEXE%" exit /b 1

"%PYEXE%" main.py %*
if errorlevel 1 (
    echo.
    echo 程序异常退出，请把上面的错误信息发给开发者。
    pause
)
exit /b 0

:try_python
rem 探测解释器是否存在且版本不低于 3.9
%* -c "import sys;sys.exit(0 if sys.version_info[:2]>=(3,9) else 1)" >nul 2>&1
if errorlevel 1 exit /b 1
set "PYCMD=%*"
exit /b 0

:check_venv
rem 运行环境可用：解释器存在，且关键依赖能正常导入
if not exist "%PYEXE%" exit /b 1
"%PYEXE%" -c "import zoneinfo,PySide6,websockets" >nul 2>&1
exit /b %errorlevel%

:prepare_venv
rem 虚拟环境版本错配时先尝试就地升级，缺依赖或升级无效则重建后安装
if exist "%PYEXE%" (
    echo 检测到运行环境不完整，正在修复……
    %PYCMD% -m venv --upgrade .venv >nul 2>&1
) else (
    echo 首次运行，正在创建运行环境……
    %PYCMD% -m venv .venv
)
if not exist "%PYEXE%" (
    echo 创建运行环境失败，请确认已安装 Python 3.13 后重试。
    pause
    exit /b 1
)
echo 正在安装依赖，第一次需要几分钟……
"%PYEXE%" -m pip install --upgrade pip
"%PYEXE%" -m pip install -r requirements.txt
call :check_venv
if errorlevel 1 (
    echo 运行环境仍不可用，请把上面的错误信息发给开发者。
    pause
    exit /b 1
)
exit /b 0
