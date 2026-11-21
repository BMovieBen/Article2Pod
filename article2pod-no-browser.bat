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
