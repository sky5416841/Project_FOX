Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "D:\Project_FOX"
WshShell.Run """D:\Project_FOX\start_cnn_watchdog.bat""", 0, False