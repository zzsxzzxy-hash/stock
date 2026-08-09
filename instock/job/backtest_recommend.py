#!/usr/bin/env python3
"""推荐回测 — 精简版"""
import json, sys, os
sys.path.insert(0, os.path.dirname(__file__))

from instock.web import volumeHandler as vh
from instock.core.minute_bar_collector import get_redis

DATES = ['2026-08-04','2026-08-05','2026-08-06','2026-08-07']
SNAPSHOTS = ['09:35','09:40','09:45','09:50','09:55']


def find_price(bars, target):
    for b in bars:
        if b.get('time','') == target: return b['close']
    best = None
    for b in bars:
        if b.get('time','') <= target: best = b
    return best['close'] if best else None


def next_td(d):
    """下一个交易日"""
    from datetime import date, timedelta
    dt = date.fromisoformat(d)
    for delta in range(1,10):
        nd = dt + timedelta(days=delta)
        if nd.weekday()<5: return nd.strftime('%Y-%m-%d')
    return ''


def get_recs(date, snapshot):
    market_fn = vh._market_code_fn([])
    raw = vh._calc_mainline_core_rows_from_redis(
        date, snapshot, 8, 0, -3.0, 35.0, 0.3, 'auto', 5_000_000, include_all=True)
    env = vh._mainline_market_environment(raw, [], snapshot)
    if not env.get('trade_allowed',True): return []

    candidates = [r for r in raw
                  if vh._passes_mainline_candidate(r,8,0,-3,35,0.3,5_000_000)]
    candidates.sort(key=lambda r: -float(r.get('core_score') or 0))

    top_n = candidates[:50]
    codes = [r['code'] for r in top_n]
    yest = vh._prev_trade_date(date)
    today_map = vh._load_minute_bars_for_codes(date, codes)
    yest_map  = vh._load_minute_bars_for_codes(yest, codes)

    scored = []
    for row in top_n:
        code = row['code']
        ret = float(row.get('ret_vs_prevclose') or 0)
        if ret > 8: continue
        sr = int(row.get('sector_rank') or 999)
        ss = max(0, 35 - sr * 2)
        ar = float(row.get('amt_vs_prev') or 0)
        vs = min(30, ar * 12)
        dh = float(row.get('distance_to_30d_high') or 0)
        ps = min(20, max(0, dh * 0.8))
        tb = vh._bars_until(today_map.get(code, []), snapshot)
        yb = yest_map.get(code, [])
        cs = vh._recommend_continuity_score(tb, yb, snapshot, row)
        total = ss * 0.35 + vs * 0.30 + ps * 0.20 + cs * 0.15
        scored.append({
            'code': code, 'name': row.get('name', code),
            'recommend_score': round(total,1),
            'sector_rank': sr,
            'trade_theme': row.get('trade_theme',''),
            'prev_close': float(row.get('prev_close') or 0),
        })
    scored.sort(key=lambda x: -x['recommend_score'])
    return scored[:5]


def bulk_get_bars(date, codes):
    r = get_redis()
    bulk = {}
    for code in codes:
        key = f'minute_bar:{date}:{code}'
        data = r.get(key)
        if data:
            try: bulk[code] = json.loads(data)
            except: bulk[code] = []
        else: bulk[code] = []
    return bulk


def add_min(hhmm, d):
    h,m = map(int, hhmm.split(':'))
    t = h*60+m+d
    return f'{t//60:02d}:{t%60:02d}'


def median_10(bars):
    prices = [b['close'] for b in bars if '09:30' <= b.get('time','') <= '10:00']
    if not prices: return None
    prices.sort()
    n = len(prices)
    return prices[n//2] if n%2==1 else (prices[n//2-1]+prices[n//2])/2


print('='*70)
print('推荐回测 (2026-08-04~08-07, 每5分钟)')
print('='*70)

all_trades = []

for date in DATES:
    nd = next_td(date)
    print(f'\n{date} → {nd}')
    for ss in SNAPSHOTS:
        recs = get_recs(date, ss)
        if not recs: continue
        codes = [r['code'] for r in recs]
        today_bars  = bulk_get_bars(date, codes)
        next_bars   = bulk_get_bars(nd, codes)
        buy_time    = add_min(ss, 2)

        for i, rec in enumerate(recs):
            code   = rec['code']
            tb     = today_bars.get(code, [])
            nb     = next_bars.get(code, [])
            prev_c = rec.get('prev_close', 0)

            buy  = find_price(tb, buy_time)
            cls  = find_price(tb, '15:00')
            n10  = median_10(nb)

            s_ret = ((cls/buy-1)*100) if buy and cls else None
            n_ret = ((n10/buy-1)*100) if buy and n10 else None

            all_trades.append({
                'date':date,'snap':ss,'buy_time':buy_time,
                'code':code,'rank':i+1,'score':rec['recommend_score'],
                'theme':rec['trade_theme'],'same_ret':s_ret,'next_ret':n_ret,
            })

            tag = ''
            if s_ret is not None: tag += f' 同日{s_ret:+.1f}%'
            if n_ret is not None: tag += f' 次日{n_ret:+.1f}%'
            if tag: print(f'  {ss}+2min #{i+1} {code} {tag}')


def show_stats(trades, key, label):
    known = [t for t in trades if t[key] is not None]
    if not known: return
    vals = [t[key] for t in known]
    pos  = sum(1 for v in vals if v>0)
    print(f'\n--- {label} ---')
    print(f'  总交易:{len(vals)}  胜率:{pos/len(vals)*100:.1f}%')
    print(f'  平均:{sum(vals)/len(vals):+.2f}%  最大:{max(vals):+.2f}%  最小:{min(vals):+.2f}%')

    print(f'  {"日期":>10} | {"交易":>5} | {"胜率":>6} | {"平均":>8}')
    for d in DATES:
        dv = [t[key] for t in known if t['date']==d]
        if not dv: continue
        dp = sum(1 for v in dv if v>0)/len(dv)*100
        da = sum(dv)/len(dv)
        print(f'  {d:>10} | {len(dv):>5} | {dp:>5.1f}% | {da:>+7.2f}%')

    print(f'  {"排名":>5} | {"交易":>5} | {"胜率":>6} | {"平均":>8}')
    for rk in range(1,6):
        rv = [t[key] for t in known if t['rank']==rk]
        if not rv: continue
        rp = sum(1 for v in rv if v>0)/len(rv)*100
        ra = sum(rv)/len(rv)
        print(f'  #{rk:<4} | {len(rv):>5} | {rp:>5.1f}% | {ra:>+7.2f}%')


show_stats(all_trades, 'same_ret', '同日收盘收益')
show_stats(all_trades, 'next_ret', '次日10:00中位价收益')

both = [t for t in all_trades if t['same_ret'] is not None and t['next_ret'] is not None]
if both:
    sa = sum(t['same_ret'] for t in both)/len(both)
    na = sum(t['next_ret'] for t in both)/len(both)
    print(f'\n--- 对比 ---')
    print(f'  同日收盘平均: {sa:+.2f}%')
    print(f'  次日10:00平均: {na:+.2f}%')

    # 次日收益分布
    print(f'\n  {"次日收益区间":>15} | {"笔数":>5} | {"占比":>6}')
    ranges = [(-100,-5),(-5,-2),(-2,0),(0,2),(2,5),(5,10),(10,100)]
    for lo,hi in ranges:
        cnt = sum(1 for t in both if lo <= t['next_ret'] < hi)
        print(f'  {lo:>4}% ~ {hi:>+4}% | {cnt:>5} | {cnt/len(both)*100:>5.1f}%')

    # 高分推荐(>=80分)的表现
    hi_score = [t for t in both if t['score'] and t['score']>=80]
    if hi_score:
        hs_na = sum(t['next_ret'] for t in hi_score)/len(hi_score)
        hs_sa = sum(t['same_ret'] for t in hi_score)/len(hi_score)
        print(f'\n  高分(≥80)推荐: {len(hi_score)}笔, 同日{hs_sa:+.2f}%, 次日{hs_na:+.2f}%')

print(f'\n{"="*70}')
print('回测完成')
