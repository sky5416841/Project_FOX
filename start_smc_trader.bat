@echo off
REM SMC 紙上交易員常駐啟動器:崩潰自動重啟、輸出寫 log。由啟動資料夾的 vbs 隱藏啟動。
cd /d D:\Project_FOX
set PYTHONUNBUFFERED=1
:loop
echo [%date% %time%] start smc_paper_trader.py >> smc_paper_trader.log
python smc_paper_trader.py >> smc_paper_trader.log 2>&1
echo [%date% %time%] exited (%errorlevel%), restart in 10s >> smc_paper_trader.log
timeout /t 10 /nobreak >nul
goto loop
