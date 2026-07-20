@echo off
chcp 65001 >nul
cd /d "%~dp0"
".venv\Scripts\python.exe" -u auto_post_instagram_api.py >> instagram_post_log.txt 2>&1
