@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo 多开实例会使用独立的数据目录、浏览器登录态、日志和控制接口端口。
echo 同一个实例名不能重复启动，不同实例名可以同时启动。
echo.
set /p INSTANCE_NAME=请输入实例名称，例如：账号2：

if "%INSTANCE_NAME%"=="" (
    echo 实例名称不能为空。
    pause
    exit /b 1
)

call "%~dp0启动.bat" --instance "%INSTANCE_NAME%"
exit /b %errorlevel%
