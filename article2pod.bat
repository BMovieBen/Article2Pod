@echo off
title Article2Pod
cd /d "%~dp0"
start /min "" cmd /c "python scripts\app.py & pause"

rem Give the server a moment to start, then open it in the browser.
timeout /t 2 /nobreak >nul
for /f "delims=" %%p in ('python -c "import json;print(json.load(open('config.json')).get('web_port',8080))" 2^>nul') do set PORT=%%p
if "%PORT%"=="" set PORT=8080
start "" http://localhost:%PORT%
