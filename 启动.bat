@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PYEXE=%~dp0.venv\Scripts\python.exe"

if not exist "%PYEXE%" (
    echo [1/2] 首次运行，正在创建运行环境……
    py -3.13 -m venv .venv 2>nul
    if not exist "%PYEXE%" py -3 -m venv .venv 2>nul
    if not exist "%PYEXE%" python -m venv .venv
    echo [2/2] 正在安装依赖，第一次需要几分钟……
    "%PYEXE%" -m pip install --upgrade pip
    "%PYEXE%" -m pip install -r requirements.txt
)

"%PYEXE%" main.py
if errorlevel 1 (
    echo.
    echo 程序异常退出，请把上面的错误信息发给开发者。
    pause
)
