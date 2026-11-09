@echo off
chcp 65001 >nul
title 智答系统
cd /d "%~dp0"

rem 优先用项目内虚拟环境；没建 venv 时回退到 PATH 里的 python
set "PY=python"
if exist "%~dp0venv\Scripts\python.exe" (
    "%~dp0venv\Scripts\python.exe" -c "import sys" >nul 2>&1
    if not errorlevel 1 set "PY=%~dp0venv\Scripts\python.exe"
)

echo 正在启动智答系统...
"%PY%" run.py %*
if errorlevel 1 (
    echo.
    echo 启动失败，请确认已执行: pip install -r requirements.txt
    echo 若使用项目内虚拟环境，先执行: python -m venv venv  ^&^&  venv\Scripts\pip install -r requirements.txt
    pause
)
