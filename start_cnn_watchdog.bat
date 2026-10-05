@echo off
cd /d D:\Project_FOX
:loop
"C:\Users\user\AppData\Local\Programs\Python\Python311\python.exe" _cnn_watch_loop.py >> _cnn_watchdog.log 2>&1
echo [%date% %time%] watcher exited, restart in 10s >> _cnn_watchdog.log
timeout /t 10 /nobreak > nul
goto loop