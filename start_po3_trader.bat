@echo off
REM PO3 紙上交易員常駐啟動器:崩潰自動重啟、輸出寫進 log。
REM 由啟動資料夾的 FOX_PO3_PaperTrader.vbs 隱藏啟動。
cd /d D:\Project_FOX
set PYTHONUNBUFFERED=1
:loop
echo [%date% %time%] start po3_paper_trader.py >> po3_paper_trader.log
python po3_paper_trader.py >> po3_paper_trader.log 2>&1
echo [%date% %time%] exited (%errorlevel%), restart in 10s >> po3_paper_trader.log
timeout /t 10 /nobreak >nul
goto loop
