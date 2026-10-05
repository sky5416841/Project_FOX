# -*- coding: utf-8 -*-
import warnings; warnings.filterwarnings("ignore")
import research.cv_predict as cp, ccxt

for tf in ['4h', '1h', '15m']:
    r = cp.classify('BTC/USDT', tf)
    L = r.get('levels') or {}
    print("%3s | %5s %5.1f%% | last %s entry %s sl %s tp %s rr %s" % (
        tf, r['cls'], r['conf']*100, L.get('last'), L.get('entry'),
        L.get('sl'), L.get('tp'), L.get('rr')))

ex = ccxt.binance({'options': {'defaultType': 'future'}})
o = ex.fetch_ohlcv('BTC/USDT', '15m', limit=4)
print('--- last 4x 15m (open/close) ---')
for c in o:
    print("  O%.0f C%.0f %s" % (c[1], c[4], 'GREEN' if c[4] >= c[1] else 'RED'))
