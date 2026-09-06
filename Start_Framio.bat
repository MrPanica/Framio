@echo off
if exist "%~dp0dist\Framio\Framio.exe" (
    start "" "%~dp0dist\Framio\Framio.exe"
) else (
    start pythonw "%~dp0main.py"
)
