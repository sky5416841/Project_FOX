# -*- coding: utf-8 -*-
"""看門狗(單發):檢查監控程式是否在跑,沒跑就啟動,然後結束。
由 Windows 工作排程器每 5 分鐘呼叫一次 → 被清掉也會自動爬起來。
顧兩支:_cnn_watch_loop.py(BTC盯倉) + po3_bigtf_scan.py --loop(大時框訊號追蹤)。"""
import subprocess, sys, os, re

HERE = os.path.dirname(os.path.abspath(__file__))
PY = r"C:\Users\user\AppData\Local\Programs\Python\Python311\python.exe"
NOWIN = 0x08000000  # CREATE_NO_WINDOW:內部查詢不彈黑視窗
# (檔名關鍵字, 啟動參數)
TARGETS = [
    ("_cnn_watch_loop.py", []),
    ("po3_bigtf_scan.py", ["--loop"]),
    ("setup_scan.py", ["--loop"]),
    ("demo_mirror.py", []),      # 幣安Demo(假錢)固定規則自動交易,見 HYPOTHESES.md H3
]

def _snapshot():
    """回傳(所有 python 命令列文字, 是否成功查到)。查不到→(",False)保守處理。"""
    text = ""
    got = False
    for name in ("python.exe", "pythonw.exe"):
        try:
            out = subprocess.check_output(
                ['wmic', 'process', 'where', "name='%s'" % name, 'get', 'CommandLine'],
                stderr=subprocess.DEVNULL, text=True, errors='ignore', timeout=20,
                creationflags=NOWIN)
            got = True
            text += out
        except Exception:
            pass
    if not got:
        try:
            ps = 'Get-CimInstance Win32_Process | Select-Object -ExpandProperty CommandLine'
            out = subprocess.check_output(['powershell', '-NoProfile', '-Command', ps],
                                          stderr=subprocess.DEVNULL, text=True, errors='ignore',
                                          timeout=25, creationflags=NOWIN)
            got = True
            text += out
        except Exception:
            pass
    return text, got

def main():
    text, got = _snapshot()
    if not got:
        print("snapshot_failed_skip")   # 查不到進程狀態 → 保守不動,絕不盲目重開
        return
    pyw = PY.replace("python.exe", "pythonw.exe")
    exe = pyw if os.path.exists(pyw) else PY
    for target, args in TARGETS:
        if target in text:
            print("alive %s" % target)
            continue
        subprocess.Popen([exe, target] + args, cwd=HERE,
                         creationflags=0x00000008 | 0x00000200)  # DETACHED|NEW_GROUP
        print("restarted %s" % target)

if __name__ == "__main__":
    main()
