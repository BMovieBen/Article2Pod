@echo off
title Article2Pod
cd /d "%~dp0"
start /min "" cmd /c "python scripts\app.py & pause"