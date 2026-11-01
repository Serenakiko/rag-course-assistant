@echo off
chcp 65001 >nul
title 智答系统
cd /d "%~dp0"
echo 正在启动智答系统...
"%~dp0venv\Scripts\python.exe" run.py
if errorlevel 1 (
    echo.
    echo 启动失败，请确认已执行: pip install -r requirements.txt
    pause
)
