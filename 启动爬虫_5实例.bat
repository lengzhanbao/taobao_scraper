@echo off
cd /d "%~dp0"
start "" wscript.exe "%~dp0start_crawlers_hidden.vbs"
exit /b
