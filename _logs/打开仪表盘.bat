@echo off
chcp 65001 > nul
rem 确保仪表盘服务在跑
tasklist /fi "imagename eq python.exe" | findstr /i "python.exe" > nul
powershell -NoProfile -Command "if (-not (Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue)) { Start-Process -WindowStyle Hidden -FilePath 'C:\Users\SYH\AppData\Local\Programs\Python\Python311\python.exe' -ArgumentList 'serve_status.py' -WorkingDirectory 'E:\直播爬取\_logs' }"
timeout /t 2 /nobreak > nul
start "" "http://127.0.0.1:8765"