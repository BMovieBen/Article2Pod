@echo off
title Article2Pod
cd /d "%~dp0"

rem Article2Pod runs on its own Python environment (.venv), made on the first run.
set PY=.venv\Scripts\python.exe
if not exist "%PY%" (
    echo Setting up Article2Pod's Python environment - first run only...
    py -3.13 -m venv .venv || python -m venv .venv
    "%PY%" -m pip install -r requirements.txt
)
start /min "" cmd /c "%PY% -u serve.py & pause"

rem Give the server a moment to start, then open it in the browser.
timeout /t 3 /nobreak >nul
for /f "delims=" %%p in ('%PY% -c "import json;print(json.load(open('settings.json')).get('port',8080))" 2^>nul') do set PORT=%%p
if "%PORT%"=="" set PORT=8080
start "" http://localhost:%PORT%
