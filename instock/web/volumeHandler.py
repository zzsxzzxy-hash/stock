#!/usr/bin/env python3
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
量能监控 API Handler
注册到 web_service.py

接口列表：
  GET  /api/volume_rank          → 排行榜
  GET  /api/volume_detail        → 个股深度数据（右栏）
  GET  /api/sector_list          → 板块列表
  GET  /api/sector_stocks        → 板块下的股票列表
  POST /api/sector_map           → 新增股票-板块映射
  DELETE /api/sector_map         → 删除股票-板块映射
  PUT  /api/sector_map/batch     → 批量设置某股票的全部板块
  POST /api/score_single         → 单股票因子得分计算
"""
import json
import datetime
import hashlib
import logging
import uuid
from abc import ABC
from decimal import Decimal

import psycopg2.extras
from tornado import gen
import instock.web.base as webBase
import instock.lib.database as mdb
from instock.core.minute_bar_collector import (
    get_day_bars_until, get_day_bars_from, get_day_bars, get_redis,
    get_all_codes_for_date, redis_key
)
from instock.core.volume_pre_calc import get_pre_calc, get_sectors, _cache_sectors
from instock.core.volume_rank_engine import (
    refresh_rank_cache, get_cached_rank, VOL_RATIO_TH,
    calc_price_slope, gen_volume_label, _last_n_trade_dates, _prev_trade_date
)
from instock.job.morning_signal_replay import (
    Thresholds as MorningSignalThresholds,
    score_row as score_morning_signal_row,
    is_candidate as is_morning_signal_candidate,
)

log = logging.getLogger(__name__)
_MAINLINE_RECOMMEND_TABLE_READY = False
_INTRADAY_BURST_STRATEGY_ID = 'intraday_burst_capture_v2'
_INTRADAY_BURST_VERSION = 'v2.0'
_INTRADAY_BURST_V3_STRATEGY_ID = 'intraday_burst_capture_v3'
_INTRADAY_BURST_V3_VERSION = 'v3.0'


def _today() -> str:
    return datetime.date.today().strftime('%Y-%m-%d')


def _current_time() -> str:
    return datetime.datetime.now().strftime('%H:%M')


def _latest_data_date() -> str:
    """返回 cn_stock_minute_bar 中最近有数据的日期"""
    try:
        rows = mdb.executeSqlFetch(
            'SELECT MAX(date) FROM cn_stock_minute_bar'
        )
        if rows and rows[0][0]:
            d = rows[0][0]
            return d.strftime('%Y-%m-%d') if hasattr(d, 'strftime') else str(d)
    except Exception:
        pass
    return _today()


def _latest_minute_time(date: str) -> str:
    """返回指定日期已入库的最新分钟。"""
    try:
        rows = mdb.executeSqlFetch(
            'SELECT MAX(time) FROM cn_stock_minute_bar WHERE date=%s',
            (date,)
        )
        if rows and rows[0][0]:
            t = rows[0][0]
            return t.strftime('%H:%M') if hasattr(t, 'strftime') else str(t)[:5]
    except Exception:
        pass
    return '09:45'


def _default_leader_snapshot(date: str) -> str:
    """龙头强势默认只看 10 点前；盘中早于 10 点时跟随最新入库分钟。"""
    latest = _latest_minute_time(date)
    return latest if latest <= '10:00' else '10:00'


def _default_mainline_snapshot(date: str) -> str:
    """主线核心观察按刷新时最新入库分钟计算；回放时由前端显式传 snapshot。"""
    return _latest_minute_time(date)


def _json_number(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (datetime.date, datetime.datetime)):
        return v.isoformat()
    return v


def _json_safe(v):
    if isinstance(v, dict):
        return {str(k): _json_safe(value) for k, value in v.items()}
    if isinstance(v, (list, tuple, set)):
        return [_json_safe(value) for value in v]
    return _json_number(v)


def _float_or_default(v, default: float = 0.0) -> float:
    if v is None:
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _row_value(row, key, default=None):
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[key]
    except Exception:
        return default


def _minute_bars_from_pg(date: str, code: str) -> list[dict]:
    rows = mdb.executeSqlFetch(
        '''SELECT time, open, close, high, low, volume, amount, pre_close
           FROM cn_stock_minute_bar
           WHERE date=%s AND code=%s
           ORDER BY time''',
        (date, code)
    )
    bars = []
    for r in rows or []:
        t = r[0].strftime('%H:%M') if hasattr(r[0], 'strftime') else str(r[0])[:5]
        bars.append({
            'time': t,
            'open': float(r[1] or 0),
            'close': float(r[2] or 0),
            'high': float(r[3] or 0),
            'low': float(r[4] or 0),
            'volume': float(r[5] or 0),
            'amount': float(r[6] or 0),
            'pre_close': float(r[7] or 0),
        })
    return bars


def _minute_bars(date: str, code: str) -> list[dict]:
    bars = get_day_bars(date, code)
    return bars if bars else _minute_bars_from_pg(date, code)


def _stock_names(codes: list[str]) -> dict[str, str]:
    if not codes:
        return {}
    placeholders = ','.join(['%s'] * len(codes))
    rows = mdb.executeSqlFetch(
        f'''SELECT DISTINCT ON (code) code, name
            FROM cn_stock_spot
            WHERE code IN ({placeholders})
            ORDER BY code, date DESC''',
        tuple(codes)
    )
    return {r[0]: r[1] or r[0] for r in rows or []}


def _stock_daily_metrics(codes: list[str], date: str) -> dict[str, dict]:
    """
    批量查询全天量比和换手率（从 cn_stock_spot）
    返回: {code: {'daily_volume_ratio': float, 'turnoverrate': float}}
    """
    if not codes:
        return {}
    placeholders = ','.join(['%s'] * len(codes))
    rows = mdb.executeSqlFetch(
        f'''SELECT code, volume_ratio, turnoverrate
            FROM cn_stock_spot
            WHERE date = %s AND code IN ({placeholders})''',
        (date, *codes)
    )
    result = {}
    for r in rows or []:
        code = r[0]
        result[code] = {
            'daily_volume_ratio': float(r[1]) if r[1] else 0.0,
            'turnoverrate': float(r[2]) if r[2] else 0.0,
        }
    return result


def _calc_prob_label(snapshot_ratio: float, daily_ratio: float, turnover: float) -> dict:
    """
    根据快照量比、全天量比、换手率，计算概率标签
    返回: {'label': 'high'|'medium'|'low', 'color': 'green'|'neutral'|'red', 'tip': '提示文案'}

    规则（基于历史胜率统计）:
    - 高概率: 快照量比<0.7 且 换手率≥10% (历史胜率100%)
    - 低概率: 快照量比≥2.5 或 换手率<2% (历史胜率≤12.5%)
    - 中性: 其他
    """
    # 红色警告（低概率）
    if snapshot_ratio >= 2.5:
        return {
            'label': 'low',
            'color': 'red',
            'icon': '⚠️',
            'tip': '巨量分歧（历史胜率0%）',
        }
    if turnover > 0 and turnover < 2.0:
        return {
            'label': 'low',
            'color': 'red',
            'icon': '⚠️',
            'tip': '换手率过低（历史胜率12.5%）',
        }

    # 绿色推荐（高概率）
    if snapshot_ratio < 0.7 and turnover >= 10.0:
        return {
            'label': 'high',
            'color': 'green',
            'icon': '✓',
            'tip': '早盘低吸窗口（历史胜率100%）',
        }

    # 黄色提示（次优但可接受）
    if 1.0 <= snapshot_ratio < 2.0 and turnover >= 10.0:
        return {
            'label': 'medium',
            'color': 'yellow',
            'icon': '',
            'tip': '适中放量（历史胜率43%）',
        }

    # 中性（无明显规律）
    return {
        'label': 'medium',
        'color': 'neutral',
        'icon': '',
        'tip': '',
    }


def _is_st_stock(name: str) -> bool:
    name = (name or '').upper().strip()
    return name.startswith('ST') or name.startswith('*ST') or name.startswith('SST')


def _clear_rank_caches():
    """交易主线变更后，清理依赖主线的实时榜缓存。"""
    try:
        r = get_redis()
        keys = []
        for pattern in ('leader_strength:*', 'volume_rank:*'):
            cursor = 0
            while True:
                cursor, batch = r.scan(cursor, match=pattern, count=200)
                keys.extend(k for k in batch if ':hist:' not in k)
                if cursor == 0:
                    break
        if keys:
            r.delete(*keys)
    except Exception as e:
        log.warning("清理板块相关缓存失败: %s", e)


def _refresh_sector_runtime_cache():
    _cache_sectors()
    _clear_rank_caches()


def _stock_name(code: str) -> str:
    return _stock_names([code]).get(code, code)


def _latest_minute_prices(date: str, codes: list[str]) -> dict[str, dict]:
    if not date or not codes:
        return {}
    placeholders = ','.join(['%s'] * len(codes))
    rows = mdb.executeSqlFetch(
        f'''SELECT DISTINCT ON (code) code, time, close
            FROM cn_stock_minute_bar
            WHERE date=%s
              AND code IN ({placeholders})
              AND close IS NOT NULL
            ORDER BY code, time DESC''',
        tuple([date] + codes)
    )
    out = {}
    for code, time_value, close in rows or []:
        t = time_value.strftime('%H:%M') if hasattr(time_value, 'strftime') else str(time_value)[:5]
        out[str(code)] = {
            'current_time': t,
            'current_price': float(close or 0),
        }
    return out


def _leader_cache_key(date: str, snapshot: str, min_score: float,
                      max_sector_rank: int, min_sector_strong: int,
                      limit: int) -> str:
    return (
        f'leader_strength:v5:{date}:{snapshot}:'
        f'{min_score}:{max_sector_rank}:{min_sector_strong}:{limit}'
    )


def _mainline_cache_key(date: str, snapshot: str, max_sector_rank: int,
                        min_sector_strong: int, min_ret: float,
                        max_ret: float, min_amt_ratio: float,
                        min_amount: float, theme: str, limit: int,
                        include_bars: bool, market: str = 'all',
                        high_prob_only: bool = False,
                        strict_overnight: bool = False) -> str:
    return (
        f'mainline_core:v23:{date}:{snapshot}:{max_sector_rank}:'
        f'{min_sector_strong}:{min_ret}:{max_ret}:{min_amt_ratio}:'
        f'{min_amount}:{theme}:{limit}:{int(include_bars)}:{market}:'
        f'{int(high_prob_only)}:{int(strict_overnight)}'
    )


def _leader_hist_key(date: str) -> str:
    return f'leader_strength:hist:v3:{date}'


def _prev_minute_closes(date: str, codes: list[str] | None = None) -> dict[str, float]:
    """用前一交易日分钟线最后一根 close 作为昨收，避免日线/分钟线复权口径不一致。"""
    prev_d = _prev_trade_date(date)
    if not prev_d:
        return {}
    params = [prev_d]
    code_filter = ''
    if codes:
        placeholders = ','.join(['%s'] * len(codes))
        code_filter = f' AND code IN ({placeholders})'
        params.extend(codes)
    rows = mdb.executeSqlFetch(
        f'''SELECT DISTINCT ON (code) code, close
            FROM cn_stock_minute_bar
            WHERE date=%s
              AND time >= '09:31'
              AND close IS NOT NULL
              {code_filter}
            ORDER BY code, time DESC''',
        tuple(params)
    )
    return {str(code): float(close or 0) for code, close in rows or []}


def _is_valid_leader_sector(sector: str, member_count: int) -> bool:
    if not sector or member_count < 3 or member_count > 80:
        return False
    bad_contains = ['板块', '重仓', '风格', '新高', '破', '股']
    if any(x in sector for x in bad_contains):
        return False
    bad_prefix = ('MSCI', '中证', '深成', '上证', '创业板')
    return not sector.startswith(bad_prefix)


def _load_leader_hist(date: str) -> dict[str, dict]:
    """加载并缓存早盘信号需要的历史辅助指标，不读分钟表。"""
    r = get_redis()
    key = _leader_hist_key(date)
    raw = r.get(key)
    if raw:
        return json.loads(raw)

    sql = """
        WITH ranked AS (
            SELECT code, close, high, amount, quote_change,
                   row_number() OVER (PARTITION BY code ORDER BY date DESC) AS rn
            FROM cn_stock_hist_data
            WHERE date < %s
        )
        SELECT code,
               max(close) FILTER (WHERE rn = 1) AS prev_close,
               avg(close) FILTER (WHERE rn <= 5) AS ma5,
               avg(close) FILTER (WHERE rn <= 10) AS ma10,
               avg(close) FILTER (WHERE rn <= 15) AS ma15,
               max(high) FILTER (WHERE rn <= 10) AS high_10d,
               max(high) FILTER (WHERE rn <= 30) AS high_30d,
               avg(amount) FILTER (WHERE rn <= 5) AS avg_amount_5d,
               max(close) FILTER (WHERE rn = 3) AS close_3d,
               max(quote_change) FILTER (WHERE rn = 1) AS prev_day_change_pct
        FROM ranked
        WHERE rn <= 30
        GROUP BY code
    """
    rows = mdb.executeSqlFetch(sql, (date,))
    data = {}
    for row in rows or []:
        code = row[0]
        data[code] = {
            'prev_close': _json_number(row[1]),
            'hist_prev_close': _json_number(row[1]),
            'prev_close_source': 'hist',
            'ma5': _json_number(row[2]),
            'ma10': _json_number(row[3]),
            'ma15': _json_number(row[4]),
            'high_10d': _json_number(row[5]),
            'high_30d': _json_number(row[6]),
            'avg_amount_5d': _json_number(row[7]),
            'close_3d': _json_number(row[8]),
            'prev_day_change_pct': _json_number(row[9]),
        }
    minute_prev = _prev_minute_closes(date, list(data.keys()))
    for code, close in minute_prev.items():
        if code in data and close > 0:
            data[code]['prev_close'] = close
            data[code]['prev_close_source'] = 'minute'
    r.set(key, json.dumps(data, ensure_ascii=False), ex=6 * 3600)
    return data


def _load_minute_bars_for_codes(date: str, codes: list[str]) -> dict[str, list[dict]]:
    if not date or not codes:
        return {}
    r = get_redis()
    pipe = r.pipeline(transaction=False)
    for code in codes:
        pipe.get(redis_key(date, code))
    values = pipe.execute()
    out = {}
    for code, raw in zip(codes, values):
        out[code] = json.loads(raw) if raw else []
    return out


def _bars_until(bars: list[dict], snapshot: str) -> list[dict]:
    return [
        b for b in bars
        if '09:31' <= b.get('time', '') <= snapshot
        and (b.get('time', '') <= '11:30' or b.get('time', '') >= '13:00')
    ]


def _sum_bars(bars: list[dict], key: str) -> float:
    return sum(float(b.get(key) or 0) for b in bars)


def _calc_leader_rows_from_redis(date: str, snapshot: str,
                                 th: MorningSignalThresholds,
                                 include_all: bool = False) -> list[dict]:
    """用 Redis 分钟线计算龙头强势候选，避免每次扫描 PostgreSQL 分钟表。"""
    yest_date = _prev_trade_date(date)
    codes = get_all_codes_for_date(date)
    if not codes:
        return []

    hist = _load_leader_hist(date)
    today_map = _load_minute_bars_for_codes(date, codes)
    prev_map = _load_minute_bars_for_codes(yest_date, codes)

    base_rows = []
    ret_by_code = {}
    for code in codes:
        h = hist.get(code)
        if not h:
            continue
        today_bars = _bars_until(today_map.get(code, []), snapshot)
        prev_bars = _bars_until(prev_map.get(code, []), snapshot)
        if len(today_bars) < 5 or not prev_bars:
            continue

        buy_price = float(today_bars[-1].get('close') or 0)
        open_0930 = float(today_bars[0].get('open') or today_bars[0].get('close') or 0)
        # XTick 分钟线的 pre_close 在 09:31 后可能变成上一分钟价，
        # 今日涨幅必须固定使用昨收口径。
        prev_close = float(h.get('prev_close') or today_bars[0].get('pre_close') or 0)
        if buy_price <= 0 or prev_close <= 0:
            continue

        high_so_far = max(float(b.get('high') or 0) for b in today_bars)
        low_so_far = min(float(b.get('low') or 0) for b in today_bars)
        amount_so_far = _sum_bars(today_bars, 'amount')
        prev_amount = _sum_bars(prev_bars, 'amount')
        volume_so_far = _sum_bars(today_bars, 'volume')
        prev_volume = _sum_bars(prev_bars, 'volume')
        if prev_amount <= 0:
            continue

        ret = (buy_price / prev_close - 1) * 100
        ret_by_code[code] = ret
        range_span = high_so_far - low_so_far
        ma5, ma10, ma15 = (float(h.get(k) or 0) for k in ('ma5', 'ma10', 'ma15'))
        ma_vals = [v for v in (ma5, ma10, ma15) if v > 0]
        high_30d = float(h.get('high_30d') or 0)
        close_3d = float(h.get('close_3d') or 0)
        high_time = max(today_bars, key=lambda b: float(b.get('high') or 0)).get('time')

        base_rows.append({
            'date': date,
            'code': code,
            'prev_d': yest_date,
            'open_0930': open_0930,
            'buy_price': buy_price,
            'high_so_far': high_so_far,
            'low_so_far': low_so_far,
            'amount_so_far': amount_so_far,
            'volume_so_far': volume_so_far,
            'green_bars': sum(1 for b in today_bars if float(b.get('close') or 0) >= float(b.get('open') or 0)),
            'bars': len(today_bars),
            'high_time': high_time,
            'prev_amount_so_far': prev_amount,
            'prev_volume_so_far': prev_volume,
            'prev_close': prev_close,
            'ma5': ma5,
            'ma10': ma10,
            'ma15': ma15,
            'high_10d': h.get('high_10d'),
            'high_30d': high_30d,
            'avg_amount_5d': h.get('avg_amount_5d'),
            'close_3d': close_3d,
            'prev_day_change_pct': h.get('prev_day_change_pct'),
            'pressure_count': 0,
            'day_high_after_buy': None,
            'day_low_after_buy': None,
            'day_close': None,
            'day_last_time': None,
            'next_d': None,
            'next_open': None,
            'next_1000_close': None,
            'next_1000_high': None,
            'next_1000_low': None,
            'ret_vs_prevclose': ret,
            'ret_from_open': (buy_price / open_0930 - 1) * 100 if open_0930 > 0 else None,
            'pos_in_range': (buy_price - low_so_far) / range_span * 100 if range_span > 0 else 100,
            'pullback': (high_so_far - buy_price) / high_so_far * 100 if high_so_far > 0 else 0,
            'amt_vs_prev': amount_so_far / prev_amount,
            'vol_vs_prev': volume_so_far / prev_volume if prev_volume > 0 else 0,
            'push_efficiency': ret / (amount_so_far / 100000000) if amount_so_far > 0 else 0,
            'distance_to_30d_high': (high_30d / buy_price - 1) * 100 if high_30d > 0 else 0,
            'break_30d_high': (buy_price / high_30d - 1) * 100 if high_30d > 0 else 0,
            'ma_converge_pct': (max(ma_vals) - min(ma_vals)) / buy_price * 100 if ma_vals else 99,
            'ma_bull': ma5 >= ma10 >= ma15 if ma_vals else False,
            'prior_3d_return_pct': (prev_close / close_3d - 1) * 100 if close_3d > 0 else 0,
            'day_max_up_pct': None,
            'day_max_down_pct': None,
            'day_close_return_pct': None,
            'next_open_return_pct': None,
            'next_1000_return_pct': None,
            'next_1000_max_up_pct': None,
            'next_1000_max_down_pct': None,
        })

    sectors = get_sectors()
    member_count = {}
    for sector_list in sectors.values():
        for s in sector_list:
            member_count[s] = member_count.get(s, 0) + 1

    sector_items = {}
    for code, ret in ret_by_code.items():
        for s in sectors.get(code, []):
            cnt = member_count.get(s, 0)
            if _is_valid_leader_sector(s, cnt):
                sector_items.setdefault(s, []).append((code, ret))

    best_by_code = {}
    for sector, items in sector_items.items():
        items.sort(key=lambda x: x[1], reverse=True)
        strong_count = sum(1 for _, ret in items if ret >= 2)
        top3_avg = sum(ret for _, ret in items[:3]) / min(len(items), 3)
        for rank, (code, _ret) in enumerate(items, start=1):
            info = {
                'best_sector': sector,
                'trade_theme': sector,
                'sector_member_count': member_count.get(sector, 0),
                'sector_rank': rank,
                'sector_strong_count': strong_count,
                'sector_top3_avg_ret': top3_avg,
            }
            cur = best_by_code.get(code)
            cur_score = (
                (100 if cur and cur['sector_rank'] <= 3 else 0)
                + (cur['sector_strong_count'] * 5 if cur else 0)
                + (cur['sector_top3_avg_ret'] if cur else 0)
            )
            new_score = (100 if rank <= 3 else 0) + strong_count * 5 + top3_avg
            if cur is None or new_score > cur_score:
                best_by_code[code] = info

    out = []
    for row in base_rows:
        row.update(best_by_code.get(row['code'], {
            'best_sector': None,
            'trade_theme': None,
            'sector_member_count': 0,
            'sector_rank': None,
            'sector_strong_count': 0,
            'sector_top3_avg_ret': 0,
        }))
        score, signal_type, tags = score_morning_signal_row(row)
        row['score'] = score
        row['signal_type'] = signal_type
        row['tags'] = ','.join(tags[:6])
        row['candidate'] = is_morning_signal_candidate(row, th)
        if include_all or row['candidate']:
            out.append(row)

    out.sort(key=lambda r: (
        -float(r.get('score') or 0),
        int(r.get('sector_rank') or 999),
        -int(r.get('sector_strong_count') or 0),
        -float(r.get('pos_in_range') or 0),
        _float_or_default(r.get('pullback'), 99),
    ))
    return out


def _mainline_trade_mode(row: dict) -> str:
    ret = float(row.get('ret_vs_prevclose') or 0)
    pos = float(row.get('pos_in_range') or 0)
    pullback = float(row.get('pullback') or 0)
    rank = int(row.get('sector_rank') or 999)
    strong = int(row.get('sector_strong_count') or 0)
    dist_high = float(row.get('distance_to_30d_high') or 0)
    ma_conv = float(row.get('ma_converge_pct') or 99)
    ma_bull = bool(row.get('ma_bull'))

    if rank <= 8 and strong >= 3 and ret >= 5 and pos >= 75 and pullback <= 3:
        return '主线核心追强'
    if rank <= 6 and strong >= 1 and ret >= 0 and pos >= 20 and pullback <= 4.8:
        return '核心中位承接'
    if rank <= 10 and strong >= 2 and -2 <= ret <= 7 and dist_high >= 6 and ma_conv <= 10:
        return '主线低位突破' if ma_bull else '核心中位承接'
    if rank <= 12 and -6 <= ret <= 4 and pos >= 30 and pullback <= 5:
        return '修复反包观察'
    return '主线观察'


def _mainline_mode_group(mode: str) -> str:
    if mode in ('主线核心追强', '核心中位承接'):
        return 'A'
    if mode in ('主线低位突破', '修复反包观察', MAINLINE_LATE_RUSH_MODE):
        return 'B'
    return '观察'


MAINLINE_REVIEW_CORE_MODES = ('主线核心追强', '核心中位承接')
MAINLINE_GUARD_CUTOFF = '09:45'
MAINLINE_RECOMMEND_START = '09:35'
MAINLINE_RECOMMEND_END = '09:50'
MAINLINE_LATE_RUSH_MODE = '09:45后急拉观察'
MAINLINE_REVIEW_WATCH_MODES = ('主线低位突破', '修复反包观察', MAINLINE_LATE_RUSH_MODE)


def _snapshot_minutes(snapshot: str) -> int | None:
    try:
        parts = str(snapshot or '').split(':')
        if len(parts) < 2:
            return None
        return int(parts[0]) * 60 + int(parts[1])
    except Exception:
        return None


def _mainline_list_mode(snapshot: str) -> str:
    minutes = _snapshot_minutes(snapshot)
    cutoff = _snapshot_minutes(MAINLINE_GUARD_CUTOFF)
    if minutes is not None and cutoff is not None and minutes <= cutoff:
        return 'early'
    return 'review'


def _minute_gap(later: str, earlier: str) -> int | None:
    later_min = _snapshot_minutes(later)
    earlier_min = _snapshot_minutes(earlier)
    if later_min is None or earlier_min is None:
        return None
    return later_min - earlier_min


def _ensure_mainline_recommend_tables() -> None:
    """候选池分钟快照留痕表。只建表/补索引，不做破坏性迁移。"""
    global _MAINLINE_RECOMMEND_TABLE_READY
    if _MAINLINE_RECOMMEND_TABLE_READY:
        return
    try:
        mdb.executeSql(
            '''
            CREATE TABLE IF NOT EXISTS cn_mainline_recommend_snapshot_meta (
                trade_date DATE NOT NULL,
                snapshot_time VARCHAR(5) NOT NULL,
                param_key VARCHAR(40) NOT NULL,
                param_text TEXT,
                candidate_count INTEGER DEFAULT 0,
                snapshot_confirmed BOOLEAN DEFAULT FALSE,
                batch_id VARCHAR(36) NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (trade_date, snapshot_time, param_key)
            )
            '''
        )
        mdb.executeSql(
            '''
            CREATE TABLE IF NOT EXISTS cn_mainline_recommend_snapshot (
                trade_date DATE NOT NULL,
                snapshot_time VARCHAR(5) NOT NULL,
                param_key VARCHAR(40) NOT NULL,
                code VARCHAR(6) NOT NULL,
                batch_id VARCHAR(36) NOT NULL,
                page_rank INTEGER,
                core_score NUMERIC,
                trade_mode VARCHAR(50),
                mainline_theme VARCHAR(120),
                risk_tags TEXT,
                recommend_count INTEGER DEFAULT 0,
                max_consecutive_count INTEGER DEFAULT 0,
                recommend_snapshots TEXT,
                payload_json JSONB,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (trade_date, snapshot_time, param_key, code)
            )
            '''
        )
        for sql in (
            'ALTER TABLE cn_mainline_recommend_snapshot_meta ADD COLUMN IF NOT EXISTS snapshot_confirmed BOOLEAN DEFAULT FALSE',
            'ALTER TABLE cn_mainline_recommend_snapshot ADD COLUMN IF NOT EXISTS recommend_count INTEGER DEFAULT 0',
            'ALTER TABLE cn_mainline_recommend_snapshot ADD COLUMN IF NOT EXISTS max_consecutive_count INTEGER DEFAULT 0',
            'ALTER TABLE cn_mainline_recommend_snapshot ADD COLUMN IF NOT EXISTS recommend_snapshots TEXT',
            'ALTER TABLE cn_mainline_recommend_snapshot ADD COLUMN IF NOT EXISTS payload_json JSONB',
        ):
            mdb.executeSql(sql)
        mdb.executeSql(
            '''
            CREATE INDEX IF NOT EXISTS idx_mainline_recommend_snapshot_code
            ON cn_mainline_recommend_snapshot (trade_date, code, snapshot_time)
            '''
        )
        mdb.executeSql(
            '''
            CREATE TABLE IF NOT EXISTS cn_mainline_market_env_snapshot (
                trade_date DATE NOT NULL,
                snapshot_time VARCHAR(5) NOT NULL,
                market_key VARCHAR(40) NOT NULL,
                market_env JSONB NOT NULL,
                status VARCHAR(30),
                action VARCHAR(80),
                trade_allowed VARCHAR(20),
                raw_count INTEGER DEFAULT 0,
                candidate_count INTEGER DEFAULT 0,
                source VARCHAR(20) DEFAULT 'live',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (trade_date, snapshot_time, market_key)
            )
            '''
        )
        mdb.executeSql(
            '''
            CREATE INDEX IF NOT EXISTS idx_mainline_market_env_snapshot_date
            ON cn_mainline_market_env_snapshot (trade_date, snapshot_time, market_key)
            '''
        )
        mdb.executeSql(
            '''
            CREATE TABLE IF NOT EXISTS cn_intraday_burst_signal (
                strategy_id VARCHAR(80) NOT NULL,
                version VARCHAR(20) NOT NULL,
                trade_date DATE NOT NULL,
                snapshot_time VARCHAR(5) NOT NULL,
                code VARCHAR(6) NOT NULL,
                page_rank INTEGER NOT NULL,
                burst_score NUMERIC,
                limitup_gene_score INTEGER DEFAULT 0,
                market_status VARCHAR(30),
                factor_json JSONB NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (strategy_id, version, trade_date, snapshot_time, code)
            )
            '''
        )
        mdb.executeSql(
            '''
            CREATE INDEX IF NOT EXISTS idx_intraday_burst_signal_date
            ON cn_intraday_burst_signal (trade_date, snapshot_time, page_rank)
            '''
        )
        _MAINLINE_RECOMMEND_TABLE_READY = True
    except Exception as e:
        log.warning("初始化主线推荐快照表失败: %s", e)


def _mainline_recommend_param_key(max_sector_rank: int, min_sector_strong: int,
                                  min_ret: float, max_ret: float,
                                  min_amt_ratio: float, theme: str,
                                  min_amount: float, markets: list[str],
                                  limit: int) -> tuple[str, str]:
    market_key = ','.join(sorted(markets)) if markets else 'all'
    param_text = (
        f'max_rank={max_sector_rank}|min_strong={min_sector_strong}|'
        f'ret={min_ret}:{max_ret}|amt={min_amt_ratio}|'
        f'min_amount={min_amount}|theme={theme}|market={market_key}|limit={limit}'
    )
    return hashlib.sha1(param_text.encode('utf-8')).hexdigest()[:40], param_text


def _mainline_market_env_key(markets: list[str]) -> str:
    return ','.join(sorted(markets)) if markets else 'all'


def _save_mainline_market_env_snapshot(date: str, snapshot: str,
                                       markets: list[str], market_env: dict,
                                       raw_count: int = 0,
                                       candidate_count: int = 0,
                                       source: str = 'live') -> None:
    """保存市场环境，即使候选池被风控关闭也必须留痕。"""
    try:
        _ensure_mainline_recommend_tables()
        trade_allowed = market_env.get('trade_allowed')
        if trade_allowed is True:
            trade_allowed_text = 'true'
        elif trade_allowed is False:
            trade_allowed_text = 'false'
        else:
            trade_allowed_text = str(trade_allowed or '')
        mdb.executeSql(
            '''
            INSERT INTO cn_mainline_market_env_snapshot
                (trade_date, snapshot_time, market_key, market_env, status,
                 action, trade_allowed, raw_count, candidate_count, source, updated_at)
            VALUES (%s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (trade_date, snapshot_time, market_key)
            DO UPDATE SET market_env=EXCLUDED.market_env,
                          status=EXCLUDED.status,
                          action=EXCLUDED.action,
                          trade_allowed=EXCLUDED.trade_allowed,
                          raw_count=EXCLUDED.raw_count,
                          candidate_count=EXCLUDED.candidate_count,
                          source=EXCLUDED.source,
                          updated_at=CURRENT_TIMESTAMP
            ''',
            (
                date,
                snapshot,
                _mainline_market_env_key(markets),
                json.dumps(_json_safe(market_env), ensure_ascii=False),
                market_env.get('status') or '',
                market_env.get('action') or '',
                trade_allowed_text,
                int(raw_count or 0),
                int(candidate_count or 0),
                source,
            )
        )
    except Exception as e:
        log.warning("保存市场环境快照失败 %s %s: %s", date, snapshot, e)


def _load_mainline_market_env_snapshot(date: str, snapshot: str,
                                       markets: list[str]) -> dict | None:
    try:
        _ensure_mainline_recommend_tables()
        rows = mdb.executeSqlFetch(
            '''
            SELECT market_env
            FROM cn_mainline_market_env_snapshot
            WHERE trade_date=%s AND snapshot_time=%s AND market_key=%s
            ''',
            (date, snapshot, _mainline_market_env_key(markets))
        ) or []
        if not rows or not isinstance(rows[0][0], dict):
            return None
        return rows[0][0]
    except Exception as e:
        log.warning("读取市场环境快照失败 %s %s: %s", date, snapshot, e)
        return None


def _load_mainline_recommend_snapshot(date: str, snapshot: str,
                                      param_key: str) -> list[dict] | None:
    try:
        _ensure_mainline_recommend_tables()
        meta = mdb.executeSqlFetch(
            '''
            SELECT batch_id, candidate_count
            FROM cn_mainline_recommend_snapshot_meta
            WHERE trade_date=%s AND snapshot_time=%s AND param_key=%s
            ''',
            (date, snapshot, param_key)
        ) or []
        if not meta:
            return None
        batch_id = str(meta[0][0])
        rows = mdb.executeSqlFetch(
            '''
            SELECT code, page_rank, core_score, trade_mode, mainline_theme,
                   risk_tags, recommend_count, max_consecutive_count,
                   recommend_snapshots, payload_json
            FROM cn_mainline_recommend_snapshot
            WHERE trade_date=%s AND snapshot_time=%s AND param_key=%s AND batch_id=%s
            ORDER BY page_rank NULLS LAST, code
            ''',
            (date, snapshot, param_key, batch_id)
        ) or []
        return [
            {
                'code': str(r[0]).zfill(6),
                'page_rank': r[1],
                'core_score': float(r[2]) if r[2] is not None else None,
                'trade_mode': r[3] or '',
                'mainline_theme': r[4] or '',
                'risk_tags': r[5] or '',
                'recommend_count': int(r[6] or 0),
                'max_consecutive_count': int(r[7] or 0),
                'recommend_snapshots': r[8] or '',
                'payload_json': r[9] or {},
            }
            for r in rows
        ]
    except Exception as e:
        log.warning("读取主线推荐快照失败 %s %s: %s", date, snapshot, e)
        return None


def _mainline_snapshot_payload_rows(snapshot_rows: list[dict]) -> list[dict]:
    """把已落库的完整候选快照还原为主线列表的原始行。"""
    rows = []
    for saved in snapshot_rows or []:
        payload = saved.get('payload_json') or {}
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (TypeError, ValueError):
                payload = {}
        if not isinstance(payload, dict):
            continue
        item = payload.copy()
        code = str(saved.get('code') or item.get('code') or '').zfill(6)
        if not code:
            continue
        item['code'] = code
        # 旧批次可能缺少部分展示字段，以快照主表中的列作为兜底。
        for field in ('page_rank', 'core_score', 'trade_mode', 'mainline_theme',
                      'recommend_count', 'max_consecutive_count',
                      'recommend_snapshots'):
            if item.get(field) is None and saved.get(field) is not None:
                item[field] = saved[field]
        if not item.get('risk_tags') and saved.get('risk_tags'):
            item['risk_tags'] = saved['risk_tags']
        rows.append(item)
    return rows


def _mainline_recommend_snapshot_confirmed(date: str, snapshot: str,
                                           param_key: str) -> bool:
    """空候选池也可能是已计算的真实结论，避免每次查询都重复回放。"""
    try:
        _ensure_mainline_recommend_tables()
        rows = mdb.executeSqlFetch(
            '''
            SELECT snapshot_confirmed
            FROM cn_mainline_recommend_snapshot_meta
            WHERE trade_date=%s AND snapshot_time=%s AND param_key=%s
            ''',
            (date, snapshot, param_key)
        ) or []
        return bool(rows and rows[0][0])
    except Exception as e:
        log.warning("读取主线推荐快照确认状态失败 %s %s: %s", date, snapshot, e)
        return False


def _save_mainline_recommend_snapshot(date: str, snapshot: str,
                                      param_key: str, param_text: str,
                                      rows: list[dict],
                                      snapshot_confirmed: bool | None = None) -> None:
    try:
        _ensure_mainline_recommend_tables()
        if snapshot_confirmed is None:
            # 历史日期的数据已经定格；当天则只有对应分钟线到位后才确认。
            latest_time = _latest_minute_time(date) if date == _today() else ''
            snapshot_confirmed = date != _today() or bool(latest_time and snapshot <= latest_time)
        batch_id = str(uuid.uuid4())
        values = []
        for row in rows:
            code = str(row.get('code') or '').zfill(6)
            if not code:
                continue
            risks = row.get('risk_tags') or []
            if isinstance(risks, list):
                risk_text = ' / '.join(str(v) for v in risks if v)
            else:
                risk_text = str(risks or '')
            values.append(
                (
                    date,
                    snapshot,
                    param_key,
                    code,
                    batch_id,
                    row.get('page_rank'),
                    row.get('core_score'),
                    row.get('trade_mode') or '',
                    row.get('mainline_theme') or row.get('trade_theme') or row.get('best_sector') or '',
                    risk_text,
                    int(row.get('recommend_count') or 0),
                    int(row.get('max_consecutive_count') or 0),
                    row.get('recommend_snapshots') or row.get('recommend_snapshots_text') or '',
                    psycopg2.extras.Json(_json_safe(row.get('payload_json') or row)),
                )
            )
        with mdb.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    '''
                    INSERT INTO cn_mainline_recommend_snapshot_meta
                        (trade_date, snapshot_time, param_key, param_text,
                         candidate_count, snapshot_confirmed, batch_id, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                    ON CONFLICT (trade_date, snapshot_time, param_key)
                    DO UPDATE SET param_text=EXCLUDED.param_text,
                                  candidate_count=EXCLUDED.candidate_count,
                                  snapshot_confirmed=EXCLUDED.snapshot_confirmed,
                                  batch_id=EXCLUDED.batch_id,
                                  updated_at=CURRENT_TIMESTAMP
                    ''',
                    (date, snapshot, param_key, param_text, len(values), bool(snapshot_confirmed), batch_id)
                )
                if values:
                    psycopg2.extras.execute_values(
                        cur,
                        '''
                        INSERT INTO cn_mainline_recommend_snapshot
                            (trade_date, snapshot_time, param_key, code, batch_id,
                             page_rank, core_score, trade_mode, mainline_theme, risk_tags,
                             recommend_count, max_consecutive_count, recommend_snapshots,
                             payload_json,
                             updated_at)
                        VALUES %s
                        ON CONFLICT (trade_date, snapshot_time, param_key, code)
                        DO UPDATE SET batch_id=EXCLUDED.batch_id,
                                      page_rank=EXCLUDED.page_rank,
                                      core_score=EXCLUDED.core_score,
                                      trade_mode=EXCLUDED.trade_mode,
                                      mainline_theme=EXCLUDED.mainline_theme,
                                      risk_tags=EXCLUDED.risk_tags,
                                      recommend_count=EXCLUDED.recommend_count,
                                      max_consecutive_count=EXCLUDED.max_consecutive_count,
                                      recommend_snapshots=EXCLUDED.recommend_snapshots,
                                      payload_json=EXCLUDED.payload_json,
                                      updated_at=CURRENT_TIMESTAMP
                        ''',
                        values,
                        template='(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,CURRENT_TIMESTAMP)',
                        page_size=500,
                    )
    except Exception as e:
        log.warning("保存主线推荐快照失败 %s %s: %s", date, snapshot, e)


def _mainline_recommend_snapshots(snapshot: str) -> list[str]:
    start = _snapshot_minutes(MAINLINE_RECOMMEND_START)
    end_limit = _snapshot_minutes(MAINLINE_RECOMMEND_END)
    end = _snapshot_minutes(snapshot)
    if start is None or end_limit is None or end is None:
        return []
    end = min(end, end_limit)
    if end < start:
        return []
    return [f'{m // 60:02d}:{m % 60:02d}' for m in range(start, end + 1)]


def _mainline_max_streak(snapshots: list[str]) -> int:
    indexes = sorted(i for i in (_snapshot_minutes(s) for s in snapshots) if i is not None)
    if not indexes:
        return 0
    best = 1
    current = 1
    for prev, cur in zip(indexes, indexes[1:]):
        if cur == prev + 1:
            current += 1
        else:
            current = 1
        best = max(best, current)
    return best


def _minute_add(time_text: str, minutes: int) -> str:
    base = _snapshot_minutes(time_text)
    if base is None:
        return time_text
    target = base + int(minutes or 0)
    return f'{target // 60:02d}:{target % 60:02d}'


def _next_trade_date(date: str) -> str:
    rows = mdb.executeSqlFetch(
        'SELECT MIN("date") FROM cn_stock_minute_bar WHERE "date" > %s',
        (date,)
    ) or []
    return str(rows[0][0]) if rows and rows[0][0] else ''


def _pct_return(price: float | None, base: float | None) -> float | None:
    if price is None or base is None:
        return None
    try:
        price_f = float(price)
        base_f = float(base)
        if base_f <= 0:
            return None
        return round((price_f / base_f - 1) * 100, 2)
    except Exception:
        return None


def _latest_snapshot_trade_date() -> str:
    rows = mdb.executeSqlFetch(
        '''
        SELECT MAX(trade_date)
        FROM cn_mainline_recommend_snapshot_meta
        WHERE param_text LIKE %s
        ''',
        ('%limit=300%',)
    ) or []
    return str(rows[0][0]) if rows and rows[0][0] else _today()


def _daily_close_map(date: str, codes: list[str]) -> dict[str, float]:
    if not date or not codes:
        return {}
    rows = mdb.executeSqlFetch(
        '''
        SELECT code, close
        FROM cn_stock_hist_data
        WHERE "date"=%s AND code = ANY(%s)
        ''',
        (date, codes)
    ) or []
    return {str(r[0]).zfill(6): float(r[1]) for r in rows if r[1] is not None}


def _minute_rows_map(date: str, codes: list[str], start: str = '09:30',
                     end: str = '15:00') -> dict[str, list[dict]]:
    if not date or not codes:
        return {}
    rows = mdb.executeSqlFetch(
        '''
        SELECT code, time, close, high
        FROM cn_stock_minute_bar
        WHERE "date"=%s AND code = ANY(%s) AND time >= %s AND time <= %s
        ORDER BY code, time
        ''',
        (date, codes, start, end)
    ) or []
    out: dict[str, list[dict]] = {}
    for code, time_text, close, high in rows:
        out.setdefault(str(code).zfill(6), []).append({
            'time': str(time_text)[:5],
            'close': float(close) if close is not None else None,
            'high': float(high) if high is not None else None,
        })
    return out


def _first_bar_at_or_after(bars: list[dict], time_text: str) -> dict | None:
    for bar in bars or []:
        if str(bar.get('time') or '') >= time_text:
            return bar
    return None


def _last_bar_at_or_before(bars: list[dict], time_text: str) -> dict | None:
    last = None
    for bar in bars or []:
        if str(bar.get('time') or '') <= time_text:
            last = bar
        else:
            break
    return last


def _max_high_at_or_before(bars: list[dict], time_text: str) -> float | None:
    highs = [
        float(bar['high']) for bar in bars or []
        if str(bar.get('time') or '') <= time_text and bar.get('high') is not None
    ]
    return max(highs) if highs else None


def _ensure_strategy_validation_tables() -> None:
    mdb.executeSql(
        '''
        CREATE TABLE IF NOT EXISTS cn_strategy_validation_result (
            strategy_name VARCHAR(50) NOT NULL,
            trade_date DATE NOT NULL,
            buy_delay INTEGER NOT NULL,
            next_cutoff VARCHAR(5) NOT NULL,
            code VARCHAR(6) NOT NULL,
            name VARCHAR(80),
            first_snapshot VARCHAR(5),
            buy_time VARCHAR(5),
            buy_actual_time VARCHAR(5),
            buy_price NUMERIC,
            recommend_count INTEGER DEFAULT 0,
            recommend_snapshots TEXT,
            ever_buyable BOOLEAN DEFAULT FALSE,
            max_core_score NUMERIC,
            modes TEXT,
            buy_statuses TEXT,
            mainlines TEXT,
            risk_tags TEXT,
            same_day_close NUMERIC,
            same_day_return_pct NUMERIC,
            next_date DATE,
            next_0940_price NUMERIC,
            next_0940_return_pct NUMERIC,
            next_0940_max_return_pct NUMERIC,
            next_close NUMERIC,
            next_close_return_pct NUMERIC,
            judged_mainline_hit BOOLEAN DEFAULT FALSE,
            actual_mainline_hit BOOLEAN DEFAULT FALSE,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (strategy_name, trade_date, buy_delay, next_cutoff, code)
        )
        '''
    )
    for sql in (
        'ALTER TABLE cn_strategy_validation_result ADD COLUMN IF NOT EXISTS judged_mainline_hit BOOLEAN DEFAULT FALSE',
        'ALTER TABLE cn_strategy_validation_result ADD COLUMN IF NOT EXISTS actual_mainline_hit BOOLEAN DEFAULT FALSE',
    ):
        mdb.executeSql(sql)
    mdb.executeSql(
        '''
        CREATE INDEX IF NOT EXISTS idx_strategy_validation_result_date
        ON cn_strategy_validation_result (trade_date, buy_delay, next_cutoff)
        '''
    )
    mdb.executeSql(
        '''
        CREATE TABLE IF NOT EXISTS cn_strategy_validation_mainline (
            strategy_name VARCHAR(50) NOT NULL,
            trade_date DATE NOT NULL,
            judged_mainlines TEXT,
            judged_detail JSONB,
            actual_mainlines TEXT,
            actual_detail JSONB,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (strategy_name, trade_date)
        )
        '''
    )
    mdb.executeSql(
        '''
        CREATE TABLE IF NOT EXISTS cn_strategy_validation_limitup_detail (
            strategy_name VARCHAR(50) NOT NULL,
            trade_date DATE NOT NULL,
            buy_delay INTEGER NOT NULL,
            next_cutoff VARCHAR(5) NOT NULL,
            code VARCHAR(6) NOT NULL,
            name VARCHAR(80),
            board VARCHAR(20),
            trade_theme VARCHAR(120),
            close_price NUMERIC,
            quote_change NUMERIC,
            amount NUMERIC,
            limitup_reason TEXT,
            in_candidate_pool BOOLEAN DEFAULT FALSE,
            candidate_reason TEXT,
            miss_reason TEXT,
            recommend_count INTEGER DEFAULT 0,
            first_snapshot VARCHAR(5),
            buy_statuses TEXT,
            modes TEXT,
            risk_tags TEXT,
            judged_mainline_hit BOOLEAN DEFAULT FALSE,
            actual_mainline_hit BOOLEAN DEFAULT FALSE,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (strategy_name, trade_date, buy_delay, next_cutoff, code)
        )
        '''
    )
    mdb.executeSql(
        '''
        CREATE TABLE IF NOT EXISTS cn_strategy_validation_limitup_theme (
            strategy_name VARCHAR(50) NOT NULL,
            trade_date DATE NOT NULL,
            buy_delay INTEGER NOT NULL,
            next_cutoff VARCHAR(5) NOT NULL,
            trade_theme VARCHAR(120) NOT NULL,
            limitup_count INTEGER DEFAULT 0,
            in_pool_count INTEGER DEFAULT 0,
            avg_quote_change NUMERIC,
            total_amount NUMERIC,
            sample_codes TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (strategy_name, trade_date, buy_delay, next_cutoff, trade_theme)
        )
        '''
    )
    mdb.executeSql(
        '''
        CREATE TABLE IF NOT EXISTS cn_strategy_validation_experiment_config (
            strategy_id VARCHAR(100) PRIMARY KEY,
            strategy_name VARCHAR(80) NOT NULL,
            version VARCHAR(40) NOT NULL,
            rule_json JSONB NOT NULL,
            description TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        '''
    )


def _theme_tokens(text: str) -> set[str]:
    return {
        item.strip() for item in str(text or '').replace('、', '/').split('/')
        if item.strip()
    }


def _mainline_hit(row_mainlines: str, target_themes: list[str]) -> bool:
    row_tokens = _theme_tokens(row_mainlines)
    target_tokens = {str(t).strip() for t in target_themes if str(t).strip()}
    return bool(row_tokens & target_tokens)


def _validation_judged_mainlines(date: str) -> list[dict]:
    rows = mdb.executeSqlFetch(
        '''
        SELECT
            s.mainline_theme,
            COUNT(*) AS appear_count,
            COUNT(DISTINCT s.code) AS stock_count,
            ROUND(AVG(s.core_score)::numeric, 1) AS avg_core_score,
            ROUND(MAX(s.core_score)::numeric, 1) AS max_core_score
        FROM cn_mainline_recommend_snapshot s
        JOIN cn_mainline_recommend_snapshot_meta m
          ON m.trade_date=s.trade_date
         AND m.snapshot_time=s.snapshot_time
         AND m.param_key=s.param_key
         AND m.batch_id=s.batch_id
        WHERE s.trade_date=%s
          AND s.snapshot_time < '10:00'
          AND m.param_text LIKE %s
          AND COALESCE(s.mainline_theme, '') <> ''
        GROUP BY s.mainline_theme
        HAVING COUNT(DISTINCT s.code) >= 3
        ORDER BY COUNT(*) DESC, MAX(s.core_score) DESC NULLS LAST
        LIMIT 12
        ''',
        (date, '%limit=300%')
    ) or []
    if not rows:
        return []
    top_count = max(int(r[1] or 0) for r in rows)
    out = []
    for r in rows:
        appear_count = int(r[1] or 0)
        if len(out) >= 5:
            break
        if appear_count < max(8, top_count * 0.28):
            continue
        out.append({
            'theme': r[0],
            'appear_count': appear_count,
            'stock_count': int(r[2] or 0),
            'avg_core_score': float(r[3]) if r[3] is not None else None,
            'max_core_score': float(r[4]) if r[4] is not None else None,
        })
    return out


def _validation_actual_mainlines(date: str) -> list[dict]:
    rows = mdb.executeSqlFetch(
        '''
        SELECT
            t.trade_theme,
            COUNT(*) AS stock_count,
            ROUND(AVG(h.quote_change)::numeric, 2) AS avg_ret,
            ROUND(MAX(h.quote_change)::numeric, 2) AS max_ret,
            COUNT(*) FILTER (WHERE h.quote_change > 0) AS up_count,
            COUNT(*) FILTER (WHERE h.quote_change >= 5) AS ret5_count,
            COUNT(*) FILTER (WHERE h.quote_change >= 8) AS ret8_count,
            ROUND(SUM(COALESCE(h.amount, 0))::numeric, 2) AS total_amount
        FROM cn_stock_hist_data h
        JOIN cn_stock_trade_theme t ON t.code = h.code
        WHERE h."date"=%s
          AND (h.code LIKE '30%%' OR h.code LIKE '68%%' OR h.code LIKE '92%%')
          AND COALESCE(t.trade_theme, '') <> ''
        GROUP BY t.trade_theme
        HAVING COUNT(*) >= 3
        ORDER BY
            (COUNT(*) FILTER (WHERE h.quote_change >= 8)) DESC,
            (COUNT(*) FILTER (WHERE h.quote_change >= 5)) DESC,
            AVG(h.quote_change) DESC,
            SUM(COALESCE(h.amount, 0)) DESC
        LIMIT 18
        ''',
        (date,)
    ) or []
    scored = []
    for r in rows:
        stock_count = int(r[1] or 0)
        avg_ret = float(r[2]) if r[2] is not None else 0.0
        max_ret = float(r[3]) if r[3] is not None else 0.0
        up_count = int(r[4] or 0)
        ret5_count = int(r[5] or 0)
        ret8_count = int(r[6] or 0)
        amount = float(r[7]) if r[7] is not None else 0.0
        up_rate = up_count / stock_count * 100 if stock_count else 0.0
        score = ret8_count * 8 + ret5_count * 5 + avg_ret * 2 + up_rate * 0.08 + max_ret * 0.5
        if ret5_count < 2 and avg_ret < 2.5:
            continue
        scored.append({
            'theme': r[0],
            'stock_count': stock_count,
            'avg_ret': round(avg_ret, 2),
            'max_ret': round(max_ret, 2),
            'up_rate': round(up_rate, 2),
            'ret5_count': ret5_count,
            'ret8_count': ret8_count,
            'total_amount': round(amount, 2),
            'score': round(score, 2),
        })
    scored.sort(key=lambda x: (-x['score'], -x['ret8_count'], -x['ret5_count'], -x['avg_ret']))
    if not scored:
        return []
    top_score = scored[0]['score']
    return [item for item in scored[:8] if item['score'] >= top_score * 0.45][:5]


def _strategy_validation_mainlines(date: str, refresh: bool = False) -> dict:
    _ensure_strategy_validation_tables()
    if not refresh:
        rows = mdb.executeSqlFetch(
            '''
            SELECT judged_mainlines, judged_detail, actual_mainlines, actual_detail
            FROM cn_strategy_validation_mainline
            WHERE strategy_name=%s AND trade_date=%s
            ''',
            ('mainline_relay', date)
        ) or []
        if rows:
            return {
                'judged_mainlines': rows[0][0] or '',
                'judged_detail': rows[0][1] or [],
                'actual_mainlines': rows[0][2] or '',
                'actual_detail': rows[0][3] or [],
                'cached': True,
            }

    judged_detail = _validation_judged_mainlines(date)
    actual_detail = _validation_actual_mainlines(date)
    judged_mainlines = ' / '.join(item['theme'] for item in judged_detail)
    actual_mainlines = ' / '.join(item['theme'] for item in actual_detail)
    with mdb.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                '''
                INSERT INTO cn_strategy_validation_mainline
                    (strategy_name, trade_date, judged_mainlines, judged_detail,
                     actual_mainlines, actual_detail, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (strategy_name, trade_date)
                DO UPDATE SET judged_mainlines=EXCLUDED.judged_mainlines,
                              judged_detail=EXCLUDED.judged_detail,
                              actual_mainlines=EXCLUDED.actual_mainlines,
                              actual_detail=EXCLUDED.actual_detail,
                              updated_at=CURRENT_TIMESTAMP
                ''',
                (
                    'mainline_relay',
                    date,
                    judged_mainlines,
                    psycopg2.extras.Json(_json_safe(judged_detail)),
                    actual_mainlines,
                    psycopg2.extras.Json(_json_safe(actual_detail)),
                )
            )
    return {
        'judged_mainlines': judged_mainlines,
        'judged_detail': judged_detail,
        'actual_mainlines': actual_mainlines,
        'actual_detail': actual_detail,
        'cached': False,
    }


def _annotate_validation_mainline_hits(data: list[dict], mainline_info: dict) -> bool:
    judged = [item.get('theme') for item in mainline_info.get('judged_detail') or []]
    actual = [item.get('theme') for item in mainline_info.get('actual_detail') or []]
    changed = False
    for row in data:
        judged_hit = _mainline_hit(row.get('mainlines') or '', judged)
        actual_hit = _mainline_hit(row.get('mainlines') or '', actual)
        if row.get('judged_mainline_hit') != judged_hit or row.get('actual_mainline_hit') != actual_hit:
            changed = True
        row['judged_mainline_hit'] = judged_hit
        row['actual_mainline_hit'] = actual_hit
    return changed


def _limitup_board(code: str) -> str:
    code = str(code or '').zfill(6)
    if code.startswith('30'):
        return '创业板'
    if code.startswith('68'):
        return '科创板'
    if code.startswith('92'):
        return '北交所'
    if code.startswith('60'):
        return '沪市主板'
    if code.startswith('00'):
        return '深市主板'
    return '其他'


def _limitup_threshold(code: str) -> float:
    code = str(code or '').zfill(6)
    if code.startswith('92'):
        return 29.0
    if code.startswith('30') or code.startswith('68'):
        return 19.0
    return 9.5


def _limitup_upper_bound(code: str) -> float:
    code = str(code or '').zfill(6)
    if code.startswith('92'):
        return 31.5
    if code.startswith('30') or code.startswith('68'):
        return 21.5
    return 11.5


def _load_strategy_validation_limitups(date: str, buy_delay: int,
                                       next_cutoff: str) -> dict | None:
    _ensure_strategy_validation_tables()
    detail_rows = mdb.executeSqlFetch(
        '''
        SELECT code, name, board, trade_theme, close_price, quote_change, amount,
               limitup_reason, in_candidate_pool, candidate_reason, miss_reason,
               recommend_count, first_snapshot, buy_statuses, modes, risk_tags,
               judged_mainline_hit, actual_mainline_hit
        FROM cn_strategy_validation_limitup_detail
        WHERE strategy_name=%s AND trade_date=%s AND buy_delay=%s AND next_cutoff=%s
        ORDER BY in_candidate_pool DESC, quote_change DESC NULLS LAST, amount DESC NULLS LAST, code
        ''',
        ('mainline_relay', date, buy_delay, next_cutoff)
    ) or []
    theme_rows = mdb.executeSqlFetch(
        '''
        SELECT trade_theme, limitup_count, in_pool_count, avg_quote_change,
               total_amount, sample_codes
        FROM cn_strategy_validation_limitup_theme
        WHERE strategy_name=%s AND trade_date=%s AND buy_delay=%s AND next_cutoff=%s
        ORDER BY limitup_count DESC, in_pool_count DESC, avg_quote_change DESC NULLS LAST
        ''',
        ('mainline_relay', date, buy_delay, next_cutoff)
    ) or []
    if not detail_rows and not theme_rows:
        return None
    detail = []
    for r in detail_rows:
        detail.append({
            'code': str(r[0]).zfill(6),
            'name': r[1] or str(r[0]).zfill(6),
            'board': r[2] or '',
            'trade_theme': r[3] or '',
            'close_price': float(r[4]) if r[4] is not None else None,
            'quote_change': float(r[5]) if r[5] is not None else None,
            'amount': float(r[6]) if r[6] is not None else None,
            'limitup_reason': r[7] or '',
            'in_candidate_pool': bool(r[8]),
            'candidate_reason': r[9] or '',
            'miss_reason': r[10] or '',
            'recommend_count': int(r[11] or 0),
            'first_snapshot': r[12] or '',
            'buy_statuses': r[13] or '',
            'modes': r[14] or '',
            'risk_tags': r[15] or '',
            'judged_mainline_hit': bool(r[16]),
            'actual_mainline_hit': bool(r[17]),
        })
    themes = []
    for r in theme_rows:
        themes.append({
            'trade_theme': r[0] or '',
            'limitup_count': int(r[1] or 0),
            'in_pool_count': int(r[2] or 0),
            'avg_quote_change': float(r[3]) if r[3] is not None else None,
            'total_amount': float(r[4]) if r[4] is not None else None,
            'sample_codes': r[5] or '',
        })
    return {
        'summary': {
            'limitup_count': len(detail),
            'in_pool_count': sum(1 for item in detail if item['in_candidate_pool']),
            'missed_count': sum(1 for item in detail if not item['in_candidate_pool']),
            'theme_count': len(themes),
        },
        'themes': themes,
        'detail': detail,
        'cached': True,
    }


def _candidate_limitup_reason(row: dict) -> str:
    parts = [
        f"首次{row.get('first_snapshot') or '-'}",
        f"出现{int(row.get('recommend_count') or 0)}次",
    ]
    if row.get('buy_statuses'):
        parts.append(f"买入状态:{row.get('buy_statuses')}")
    if row.get('modes'):
        parts.append(f"模式:{row.get('modes')}")
    if row.get('risk_tags'):
        parts.append(f"风险:{row.get('risk_tags')}")
    return '；'.join(parts)


def _limitup_miss_reason(code: str, theme: str, mainline_info: dict) -> str:
    board = _limitup_board(code)
    parts = ['未进入09:35-09:50候选池']
    if code.startswith('60') or code.startswith('00'):
        parts.append('非当前偏好市场(主板)')
    judged = [item.get('theme') for item in mainline_info.get('judged_detail') or []]
    actual = [item.get('theme') for item in mainline_info.get('actual_detail') or []]
    if theme and theme not in judged:
        parts.append('不属于10点前判定主线')
    if theme and theme in actual:
        parts.append('收盘证明属于实际主线，属于重点漏检')
    elif theme and theme not in actual:
        parts.append('不属于收盘实际主线')
    parts.append(f'市场:{board}')
    return '；'.join(parts)


def _calc_strategy_validation_limitups(date: str, buy_delay: int, next_cutoff: str,
                                       data: list[dict], mainline_info: dict) -> dict:
    rows = mdb.executeSqlFetch(
        '''
        SELECT h.code,
               COALESCE(l.name, t.name, h.code) AS name,
               COALESCE(t.trade_theme, '') AS trade_theme,
               h.close,
               h.quote_change,
               h.amount,
               COALESCE(l.reason, l.title, '') AS limitup_reason
        FROM cn_stock_hist_data h
        LEFT JOIN cn_stock_trade_theme t ON t.code = h.code
        LEFT JOIN cn_stock_limitup_reason l ON l.date = h."date" AND l.code = h.code
        WHERE h."date"=%s
          AND h.quote_change IS NOT NULL
          AND (
              (h.code LIKE '92%%' AND h.quote_change BETWEEN 29.0 AND 31.5)
              OR ((h.code LIKE '30%%' OR h.code LIKE '68%%') AND h.quote_change BETWEEN 19.0 AND 21.5)
              OR ((h.code LIKE '00%%' OR h.code LIKE '60%%') AND h.quote_change BETWEEN 9.5 AND 11.5)
          )
        ORDER BY h.quote_change DESC, h.amount DESC NULLS LAST
        ''',
        (date,)
    ) or []
    candidates = {str(row.get('code')).zfill(6): row for row in data}
    detail = []
    for r in rows:
        code = str(r[0]).zfill(6)
        quote_change = float(r[4]) if r[4] is not None else 0.0
        if quote_change < _limitup_threshold(code) or quote_change > _limitup_upper_bound(code):
            continue
        cand = candidates.get(code)
        theme = r[2] or ''
        item = {
            'code': code,
            'name': r[1] or code,
            'board': _limitup_board(code),
            'trade_theme': theme,
            'close_price': float(r[3]) if r[3] is not None else None,
            'quote_change': quote_change,
            'amount': float(r[5]) if r[5] is not None else None,
            'limitup_reason': r[6] or '',
            'in_candidate_pool': bool(cand),
            'candidate_reason': _candidate_limitup_reason(cand) if cand else '',
            'miss_reason': '' if cand else _limitup_miss_reason(code, theme, mainline_info),
            'recommend_count': int(cand.get('recommend_count') or 0) if cand else 0,
            'first_snapshot': cand.get('first_snapshot') if cand else '',
            'buy_statuses': cand.get('buy_statuses') if cand else '',
            'modes': cand.get('modes') if cand else '',
            'risk_tags': cand.get('risk_tags') if cand else '',
            'judged_mainline_hit': bool(cand.get('judged_mainline_hit')) if cand else _mainline_hit(theme, [x.get('theme') for x in mainline_info.get('judged_detail') or []]),
            'actual_mainline_hit': bool(cand.get('actual_mainline_hit')) if cand else _mainline_hit(theme, [x.get('theme') for x in mainline_info.get('actual_detail') or []]),
        }
        detail.append(item)

    theme_map: dict[str, dict] = {}
    for item in detail:
        theme = item.get('trade_theme') or '未归类'
        stat = theme_map.setdefault(theme, {
            'trade_theme': theme,
            'limitup_count': 0,
            'in_pool_count': 0,
            'quote_changes': [],
            'total_amount': 0.0,
            'codes': [],
        })
        stat['limitup_count'] += 1
        if item.get('in_candidate_pool'):
            stat['in_pool_count'] += 1
        if item.get('quote_change') is not None:
            stat['quote_changes'].append(float(item['quote_change']))
        if item.get('amount') is not None:
            stat['total_amount'] += float(item['amount'])
        stat['codes'].append(f"{item['code']} {item['name']}")
    themes = []
    for stat in theme_map.values():
        vals = stat.pop('quote_changes')
        codes = stat.pop('codes')
        stat['avg_quote_change'] = round(sum(vals) / len(vals), 2) if vals else None
        stat['total_amount'] = round(stat['total_amount'], 2)
        stat['sample_codes'] = ' / '.join(codes[:6])
        themes.append(stat)
    themes.sort(key=lambda x: (-x['limitup_count'], -x['in_pool_count'], -(x.get('avg_quote_change') or 0)))
    return {
        'summary': {
            'limitup_count': len(detail),
            'in_pool_count': sum(1 for item in detail if item['in_candidate_pool']),
            'missed_count': sum(1 for item in detail if not item['in_candidate_pool']),
            'theme_count': len(themes),
        },
        'themes': themes,
        'detail': detail,
        'cached': False,
    }


def _save_strategy_validation_limitups(date: str, buy_delay: int, next_cutoff: str,
                                       info: dict) -> None:
    _ensure_strategy_validation_tables()
    with mdb.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                '''
                DELETE FROM cn_strategy_validation_limitup_detail
                WHERE strategy_name=%s AND trade_date=%s AND buy_delay=%s AND next_cutoff=%s
                ''',
                ('mainline_relay', date, buy_delay, next_cutoff)
            )
            cur.execute(
                '''
                DELETE FROM cn_strategy_validation_limitup_theme
                WHERE strategy_name=%s AND trade_date=%s AND buy_delay=%s AND next_cutoff=%s
                ''',
                ('mainline_relay', date, buy_delay, next_cutoff)
            )
            detail_values = []
            for item in info.get('detail') or []:
                detail_values.append((
                    'mainline_relay', date, buy_delay, next_cutoff,
                    item.get('code'), item.get('name'), item.get('board'),
                    item.get('trade_theme'), item.get('close_price'),
                    item.get('quote_change'), item.get('amount'),
                    item.get('limitup_reason') or '',
                    bool(item.get('in_candidate_pool')),
                    item.get('candidate_reason') or '',
                    item.get('miss_reason') or '',
                    int(item.get('recommend_count') or 0),
                    item.get('first_snapshot') or '',
                    item.get('buy_statuses') or '',
                    item.get('modes') or '',
                    item.get('risk_tags') or '',
                    bool(item.get('judged_mainline_hit')),
                    bool(item.get('actual_mainline_hit')),
                ))
            if detail_values:
                psycopg2.extras.execute_values(
                    cur,
                    '''
                    INSERT INTO cn_strategy_validation_limitup_detail
                        (strategy_name, trade_date, buy_delay, next_cutoff, code,
                         name, board, trade_theme, close_price, quote_change,
                         amount, limitup_reason, in_candidate_pool,
                         candidate_reason, miss_reason, recommend_count,
                         first_snapshot, buy_statuses, modes, risk_tags,
                         judged_mainline_hit, actual_mainline_hit, updated_at)
                    VALUES %s
                    ON CONFLICT (strategy_name, trade_date, buy_delay, next_cutoff, code)
                    DO UPDATE SET name=EXCLUDED.name,
                                  board=EXCLUDED.board,
                                  trade_theme=EXCLUDED.trade_theme,
                                  close_price=EXCLUDED.close_price,
                                  quote_change=EXCLUDED.quote_change,
                                  amount=EXCLUDED.amount,
                                  limitup_reason=EXCLUDED.limitup_reason,
                                  in_candidate_pool=EXCLUDED.in_candidate_pool,
                                  candidate_reason=EXCLUDED.candidate_reason,
                                  miss_reason=EXCLUDED.miss_reason,
                                  recommend_count=EXCLUDED.recommend_count,
                                  first_snapshot=EXCLUDED.first_snapshot,
                                  buy_statuses=EXCLUDED.buy_statuses,
                                  modes=EXCLUDED.modes,
                                  risk_tags=EXCLUDED.risk_tags,
                                  judged_mainline_hit=EXCLUDED.judged_mainline_hit,
                                  actual_mainline_hit=EXCLUDED.actual_mainline_hit,
                                  updated_at=CURRENT_TIMESTAMP
                    ''',
                    detail_values,
                    template='(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,CURRENT_TIMESTAMP)',
                    page_size=500,
                )
            theme_values = []
            for item in info.get('themes') or []:
                theme_values.append((
                    'mainline_relay', date, buy_delay, next_cutoff,
                    item.get('trade_theme') or '未归类',
                    int(item.get('limitup_count') or 0),
                    int(item.get('in_pool_count') or 0),
                    item.get('avg_quote_change'),
                    item.get('total_amount'),
                    item.get('sample_codes') or '',
                ))
            if theme_values:
                psycopg2.extras.execute_values(
                    cur,
                    '''
                    INSERT INTO cn_strategy_validation_limitup_theme
                        (strategy_name, trade_date, buy_delay, next_cutoff,
                         trade_theme, limitup_count, in_pool_count,
                         avg_quote_change, total_amount, sample_codes, updated_at)
                    VALUES %s
                    ON CONFLICT (strategy_name, trade_date, buy_delay, next_cutoff, trade_theme)
                    DO UPDATE SET limitup_count=EXCLUDED.limitup_count,
                                  in_pool_count=EXCLUDED.in_pool_count,
                                  avg_quote_change=EXCLUDED.avg_quote_change,
                                  total_amount=EXCLUDED.total_amount,
                                  sample_codes=EXCLUDED.sample_codes,
                                  updated_at=CURRENT_TIMESTAMP
                    ''',
                    theme_values,
                    template='(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,CURRENT_TIMESTAMP)',
                    page_size=500,
                )


def _strategy_validation_limitups(date: str, buy_delay: int, next_cutoff: str,
                                  data: list[dict], mainline_info: dict,
                                  refresh: bool = False) -> dict:
    if not refresh:
        cached = _load_strategy_validation_limitups(date, buy_delay, next_cutoff)
        if cached is not None:
            return cached
    info = _calc_strategy_validation_limitups(date, buy_delay, next_cutoff, data, mainline_info)
    _save_strategy_validation_limitups(date, buy_delay, next_cutoff, info)
    return info


def _strategy_validation_summary(data: list[dict]) -> dict:
    def stat_for(field: str) -> dict:
        vals = [item[field] for item in data if item.get(field) is not None]
        if not vals:
            return {'count': 0, 'win': 0, 'win_rate': None, 'avg': None}
        wins = sum(1 for v in vals if v > 0)
        return {
            'count': len(vals),
            'win': wins,
            'win_rate': round(wins / len(vals) * 100, 2),
            'avg': round(sum(vals) / len(vals), 2),
        }

    return {
        'candidate_count': len(data),
        'priced_count': sum(1 for item in data if item.get('buy_price') is not None),
        'same_day': stat_for('same_day_return_pct'),
        'next_0940': stat_for('next_0940_return_pct'),
        'next_0940_max': stat_for('next_0940_max_return_pct'),
        'next_close': stat_for('next_close_return_pct'),
    }


_V1_EXPERIMENT_ID = 'mainline_burst_capture_v1_accel_4'
_V1_EXPERIMENT_RULE = {
    'markets': ['创业板', '科创板', '京市A股'],
    'ret_acceleration_min': 4.0,
    'min_recommend_count': 0,
    'keep_original_pool': True,
    'decision_time': '09:50前',
}


def _experiment_stat(rows: list[dict]) -> dict:
    def stat_for(field: str) -> dict:
        values = [float(item[field]) for item in rows if item.get(field) is not None]
        if not values:
            return {'priced_count': 0, 'win_rate': None, 'avg': None}
        return {
            'priced_count': len(values),
            'win_rate': round(sum(1 for value in values if value > 0) * 100 / len(values), 1),
            'avg': round(sum(values) / len(values), 2),
        }

    return {
        'candidate_count': len(rows),
        'same_day': stat_for('same_day_return_pct'),
        'next_0940': stat_for('next_0940_return_pct'),
        'next_0940_max': stat_for('next_0940_max_return_pct'),
        'next_close': stat_for('next_close_return_pct'),
    }


def _strategy_validation_experiment(buy_delay: int = 2,
                                    next_cutoff: str = '09:40') -> dict:
    """爆发捕捉V1只打标签，原始候选池始终保留。"""
    _ensure_strategy_validation_tables()
    mdb.executeSql(
        '''
        INSERT INTO cn_strategy_validation_experiment_config
            (strategy_id, strategy_name, version, rule_json, description)
        VALUES (%s, %s, %s, %s::jsonb, %s)
        ON CONFLICT (strategy_id) DO NOTHING
        ''',
        (
            _V1_EXPERIMENT_ID,
            '爆发捕捉 V1',
            'v1',
            json.dumps(_V1_EXPERIMENT_RULE, ensure_ascii=False),
            '保留原始候选池；仅标记首次入池至09:50涨幅加速不少于4%的爆发候选。',
        )
    )
    rows = mdb.executeSqlFetch(
        '''
        WITH snapshot_metric AS (
            SELECT s.trade_date, s.code,
                   (array_agg(NULLIF(s.payload_json->>'ret_vs_prevclose', '')::numeric
                    ORDER BY s.snapshot_time))[1] AS first_ret,
                   (array_agg(NULLIF(s.payload_json->>'ret_vs_prevclose', '')::numeric
                    ORDER BY s.snapshot_time DESC))[1] AS last_ret
            FROM cn_mainline_recommend_snapshot s
            JOIN cn_mainline_recommend_snapshot_meta m
              ON m.trade_date=s.trade_date AND m.snapshot_time=s.snapshot_time
             AND m.param_key=s.param_key AND m.batch_id=s.batch_id
            WHERE m.param_text LIKE %s
            GROUP BY s.trade_date, s.code
        )
        SELECT r.trade_date, r.code, r.recommend_count, r.same_day_return_pct,
               r.next_0940_return_pct, r.next_0940_max_return_pct,
               r.next_close_return_pct,
               COALESCE(metric.last_ret - metric.first_ret, 0) AS ret_acceleration
        FROM cn_strategy_validation_result r
        JOIN snapshot_metric metric ON metric.trade_date=r.trade_date AND metric.code=r.code
        WHERE r.strategy_name=%s AND r.buy_delay=%s AND r.next_cutoff=%s
          AND r.next_date IS NOT NULL AND r.next_0940_return_pct IS NOT NULL
        ORDER BY r.trade_date DESC, r.code
        ''',
        ('%limit=300%', 'mainline_relay', buy_delay, next_cutoff)
    ) or []
    data = [
        {
            'trade_date': str(row[0]),
            'code': str(row[1]).zfill(6),
            'recommend_count': int(row[2] or 0),
            'same_day_return_pct': float(row[3]) if row[3] is not None else None,
            'next_0940_return_pct': float(row[4]) if row[4] is not None else None,
            'next_0940_max_return_pct': float(row[5]) if row[5] is not None else None,
            'next_close_return_pct': float(row[6]) if row[6] is not None else None,
            'ret_acceleration': float(row[7]) if row[7] is not None else None,
        }
        for row in rows
    ]
    experiment = [row for row in data if (row.get('ret_acceleration') or 0) >= 4]
    grouped: dict[str, dict[str, list[dict]]] = {}
    for row in data:
        grouped.setdefault(row['trade_date'], {'baseline': [], 'experiment': []})['baseline'].append(row)
        if (row.get('ret_acceleration') or 0) >= 4:
            grouped[row['trade_date']]['experiment'].append(row)
    daily = [
        {
            'trade_date': trade_date,
            'baseline': _experiment_stat(groups['baseline']),
            'experiment': _experiment_stat(groups['experiment']),
            'opportunity_total': sum(
                1 for row in groups['baseline']
                if (row.get('same_day_return_pct') or 0) >= 10
                or (row.get('next_0940_max_return_pct') or 0) >= 10
            ),
            'opportunity_captured': sum(
                1 for row in groups['experiment']
                if (row.get('same_day_return_pct') or 0) >= 10
                or (row.get('next_0940_max_return_pct') or 0) >= 10
            ),
        }
        for trade_date, groups in sorted(grouped.items(), reverse=True)
    ]
    return {
        'strategy_id': _V1_EXPERIMENT_ID,
        'strategy_name': '爆发捕捉 V1',
        'version': 'v1',
        'rule': _V1_EXPERIMENT_RULE,
        'baseline': _experiment_stat(data),
        'experiment': _experiment_stat(experiment),
        'opportunity': {
            'definition': '当天收益≥10% 或次日09:40前最高收益≥10%',
            'total': sum(1 for row in data if (row.get('same_day_return_pct') or 0) >= 10 or (row.get('next_0940_max_return_pct') or 0) >= 10),
            'captured': sum(1 for row in experiment if (row.get('same_day_return_pct') or 0) >= 10 or (row.get('next_0940_max_return_pct') or 0) >= 10),
        },
        'daily': daily,
    }


def _load_strategy_validation_result(date: str, buy_delay: int,
                                     next_cutoff: str) -> list[dict] | None:
    _ensure_strategy_validation_tables()
    rows = mdb.executeSqlFetch(
        '''
        SELECT code, name, first_snapshot, buy_time, buy_actual_time, buy_price,
               recommend_count, recommend_snapshots, ever_buyable, max_core_score,
               modes, buy_statuses, mainlines, risk_tags, same_day_close,
               same_day_return_pct, next_date, next_0940_price,
               next_0940_return_pct, next_0940_max_return_pct, next_close,
               next_close_return_pct, judged_mainline_hit, actual_mainline_hit
        FROM cn_strategy_validation_result
        WHERE strategy_name=%s AND trade_date=%s AND buy_delay=%s AND next_cutoff=%s
        ORDER BY recommend_count DESC, max_core_score DESC NULLS LAST, code
        ''',
        ('mainline_relay', date, buy_delay, next_cutoff)
    ) or []
    if not rows:
        return None
    data = []
    for r in rows:
        data.append({
            'date': date,
            'code': str(r[0]).zfill(6),
            'name': r[1] or str(r[0]).zfill(6),
            'first_snapshot': r[2] or '',
            'buy_time': r[3] or '',
            'buy_actual_time': r[4] or '',
            'buy_price': float(r[5]) if r[5] is not None else None,
            'recommend_count': int(r[6] or 0),
            'recommend_snapshots': r[7] or '',
            'ever_buyable': bool(r[8]),
            'max_core_score': float(r[9]) if r[9] is not None else None,
            'modes': r[10] or '',
            'buy_statuses': r[11] or '',
            'mainlines': r[12] or '',
            'risk_tags': r[13] or '',
            'same_day_close': float(r[14]) if r[14] is not None else None,
            'same_day_return_pct': float(r[15]) if r[15] is not None else None,
            'next_date': str(r[16]) if r[16] else '',
            'next_0940_price': float(r[17]) if r[17] is not None else None,
            'next_0940_return_pct': float(r[18]) if r[18] is not None else None,
            'next_0940_max_return_pct': float(r[19]) if r[19] is not None else None,
            'next_close': float(r[20]) if r[20] is not None else None,
            'next_close_return_pct': float(r[21]) if r[21] is not None else None,
            'judged_mainline_hit': bool(r[22]),
            'actual_mainline_hit': bool(r[23]),
        })
    return data


def _save_strategy_validation_result(date: str, buy_delay: int, next_cutoff: str,
                                     data: list[dict]) -> None:
    _ensure_strategy_validation_tables()
    values = []
    for row in data:
        values.append((
            'mainline_relay',
            date,
            buy_delay,
            next_cutoff,
            row.get('code'),
            row.get('name'),
            row.get('first_snapshot'),
            row.get('buy_time'),
            row.get('buy_actual_time'),
            row.get('buy_price'),
            row.get('recommend_count') or 0,
            row.get('recommend_snapshots') or '',
            bool(row.get('ever_buyable')),
            row.get('max_core_score'),
            row.get('modes') or '',
            row.get('buy_statuses') or '',
            row.get('mainlines') or '',
            row.get('risk_tags') or '',
            row.get('same_day_close'),
            row.get('same_day_return_pct'),
            row.get('next_date') or None,
            row.get('next_0940_price'),
            row.get('next_0940_return_pct'),
            row.get('next_0940_max_return_pct'),
            row.get('next_close'),
            row.get('next_close_return_pct'),
            bool(row.get('judged_mainline_hit')),
            bool(row.get('actual_mainline_hit')),
        ))
    if not values:
        return
    with mdb.get_connection() as conn:
        with conn.cursor() as cur:
            psycopg2.extras.execute_values(
                cur,
                '''
                INSERT INTO cn_strategy_validation_result
                    (strategy_name, trade_date, buy_delay, next_cutoff, code,
                     name, first_snapshot, buy_time, buy_actual_time, buy_price,
                     recommend_count, recommend_snapshots, ever_buyable,
                     max_core_score, modes, buy_statuses, mainlines, risk_tags,
                     same_day_close, same_day_return_pct, next_date,
                     next_0940_price, next_0940_return_pct,
                     next_0940_max_return_pct, next_close, next_close_return_pct,
                     judged_mainline_hit, actual_mainline_hit,
                     updated_at)
                VALUES %s
                ON CONFLICT (strategy_name, trade_date, buy_delay, next_cutoff, code)
                DO UPDATE SET name=EXCLUDED.name,
                              first_snapshot=EXCLUDED.first_snapshot,
                              buy_time=EXCLUDED.buy_time,
                              buy_actual_time=EXCLUDED.buy_actual_time,
                              buy_price=EXCLUDED.buy_price,
                              recommend_count=EXCLUDED.recommend_count,
                              recommend_snapshots=EXCLUDED.recommend_snapshots,
                              ever_buyable=EXCLUDED.ever_buyable,
                              max_core_score=EXCLUDED.max_core_score,
                              modes=EXCLUDED.modes,
                              buy_statuses=EXCLUDED.buy_statuses,
                              mainlines=EXCLUDED.mainlines,
                              risk_tags=EXCLUDED.risk_tags,
                              same_day_close=EXCLUDED.same_day_close,
                              same_day_return_pct=EXCLUDED.same_day_return_pct,
                              next_date=EXCLUDED.next_date,
                              next_0940_price=EXCLUDED.next_0940_price,
                              next_0940_return_pct=EXCLUDED.next_0940_return_pct,
                              next_0940_max_return_pct=EXCLUDED.next_0940_max_return_pct,
                              next_close=EXCLUDED.next_close,
                              next_close_return_pct=EXCLUDED.next_close_return_pct,
                              judged_mainline_hit=EXCLUDED.judged_mainline_hit,
                              actual_mainline_hit=EXCLUDED.actual_mainline_hit,
                              updated_at=CURRENT_TIMESTAMP
                ''',
                values,
                template='(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,CURRENT_TIMESTAMP)',
                page_size=500,
            )


def _strategy_validation_rows(date: str, buy_delay: int = 2,
                              next_cutoff: str = '09:40',
                              refresh: bool = False) -> dict:
    query_date = date or _latest_snapshot_trade_date()
    next_date = _next_trade_date(query_date)
    mainline_info = _strategy_validation_mainlines(query_date, refresh=refresh)
    if not refresh:
        cached_data = _load_strategy_validation_result(query_date, buy_delay, next_cutoff)
        if cached_data is not None:
            cached_next = cached_data[0].get('next_date') if cached_data else next_date
            has_next_result = any(item.get('next_0940_return_pct') is not None for item in cached_data)
            if next_date and (not cached_next or not has_next_result):
                cached_data = None
            else:
                changed = _annotate_validation_mainline_hits(cached_data, mainline_info)
                if changed:
                    _save_strategy_validation_result(query_date, buy_delay, next_cutoff, cached_data)
                limitup_info = _strategy_validation_limitups(
                    query_date, buy_delay, next_cutoff, cached_data, mainline_info, refresh=refresh
                )
                return {
                    'date': query_date,
                    'next_date': cached_next or next_date,
                    'buy_delay': buy_delay,
                    'next_cutoff': next_cutoff,
                    'mainline_info': mainline_info,
                    'limitup_info': limitup_info,
                    'summary': _strategy_validation_summary(cached_data),
                    'data': cached_data,
                    'cached': True,
                }

    rows = mdb.executeSqlFetch(
        '''
        SELECT
            s.code,
            COALESCE(MAX(NULLIF(s.payload_json->>'name', '')), s.code) AS name,
            MIN(s.snapshot_time) AS first_snapshot,
            COUNT(DISTINCT s.snapshot_time) AS recommend_count,
            string_agg(DISTINCT s.snapshot_time, ',' ORDER BY s.snapshot_time) AS recommend_snapshots,
            ROUND(MAX(s.core_score)::numeric, 1) AS max_core_score,
            string_agg(DISTINCT NULLIF(s.trade_mode, ''), ' / ') AS modes,
            string_agg(DISTINCT NULLIF(s.mainline_theme, ''), ' / ') AS mainlines,
            string_agg(DISTINCT NULLIF(s.risk_tags, ''), ' / ') AS risk_tags
        FROM cn_mainline_recommend_snapshot s
        JOIN cn_mainline_recommend_snapshot_meta m
          ON m.trade_date=s.trade_date
         AND m.snapshot_time=s.snapshot_time
         AND m.param_key=s.param_key
         AND m.batch_id=s.batch_id
        WHERE s.trade_date=%s
          AND m.param_text LIKE %s
        GROUP BY s.code
        ORDER BY COUNT(DISTINCT s.snapshot_time) DESC, MAX(s.core_score) DESC NULLS LAST, s.code
        ''',
        (query_date, '%limit=300%')
    ) or []

    codes = [str(r[0]).zfill(6) for r in rows]
    today_bars = _minute_rows_map(query_date, codes, '09:30', '15:00')
    next_bars = _minute_rows_map(next_date, codes, '09:30', next_cutoff) if next_date else {}
    today_close = _daily_close_map(query_date, codes)
    next_close = _daily_close_map(next_date, codes) if next_date else {}

    data = []
    for r in rows:
        code = str(r[0]).zfill(6)
        first_snapshot = str(r[2])[:5]
        buy_time = _minute_add(first_snapshot, buy_delay)
        buy_bar = _first_bar_at_or_after(today_bars.get(code, []), buy_time)
        buy_price = buy_bar.get('close') if buy_bar else None
        buy_actual_time = buy_bar.get('time') if buy_bar else ''
        same_close = today_close.get(code)
        if same_close is None:
            last_today = _last_bar_at_or_before(today_bars.get(code, []), '15:00')
            same_close = last_today.get('close') if last_today else None

        next_0940_bar = _last_bar_at_or_before(next_bars.get(code, []), next_cutoff)
        next_0940_price = next_0940_bar.get('close') if next_0940_bar else None
        next_0940_high = _max_high_at_or_before(next_bars.get(code, []), next_cutoff)

        same_day_ret = _pct_return(same_close, buy_price)
        next_0940_ret = _pct_return(next_0940_price, buy_price)
        next_0940_max_ret = _pct_return(next_0940_high, buy_price)
        next_close_ret = _pct_return(next_close.get(code), buy_price)

        data.append({
            'date': query_date,
            'code': code,
            'name': r[1] or code,
            'first_snapshot': first_snapshot,
            'buy_time': buy_time,
            'buy_actual_time': buy_actual_time,
            'buy_price': buy_price,
            'recommend_count': int(r[3] or 0),
            'recommend_snapshots': r[4] or '',
            'max_core_score': float(r[5]) if r[5] is not None else None,
            'modes': r[6] or '',
            'mainlines': r[7] or '',
            'risk_tags': r[8] or '',
            'same_day_close': same_close,
            'same_day_return_pct': same_day_ret,
            'next_date': next_date,
            'next_0940_price': next_0940_price,
            'next_0940_return_pct': next_0940_ret,
            'next_0940_max_return_pct': next_0940_max_ret,
            'next_close': next_close.get(code),
            'next_close_return_pct': next_close_ret,
        })

    _annotate_validation_mainline_hits(data, mainline_info)
    _save_strategy_validation_result(query_date, buy_delay, next_cutoff, data)
    limitup_info = _strategy_validation_limitups(
        query_date, buy_delay, next_cutoff, data, mainline_info, refresh=refresh
    )
    return {
        'date': query_date,
        'next_date': next_date,
        'buy_delay': buy_delay,
        'next_cutoff': next_cutoff,
        'mainline_info': mainline_info,
        'limitup_info': limitup_info,
        'summary': _strategy_validation_summary(data),
        'data': data,
        'cached': False,
    }


def _mainline_recommend_frequency(date: str, snapshot: str,
                                  max_sector_rank: int, min_sector_strong: int,
                                  min_ret: float, max_ret: float,
                                  min_amt_ratio: float, theme: str,
                                  min_amount: float, markets: list[str],
                                  limit: int) -> dict[str, dict]:
    """统计 09:35 到当前快照（最多09:50）每只股票进入页面候选池的次数。"""
    snapshots = _mainline_recommend_snapshots(snapshot)
    if not snapshots:
        return {}

    param_key, param_text = _mainline_recommend_param_key(
        max_sector_rank, min_sector_strong, min_ret, max_ret,
        min_amt_ratio, theme, min_amount, markets, limit
    )
    # Today's pre-market query may cache an empty result before the relevant
    # minute bars exist. Version the cache by the available bar progress (up to
    # this calculation window) so that result is never reused after data lands.
    latest_time = _latest_minute_time(date) if date == _today() else ''
    cache_progress = min(latest_time, snapshots[-1]) if latest_time else 'none'
    cache_key = (
        f'mainline_recommend_freq:v2:{date}:{snapshot}:{max_sector_rank}:'
        f'{min_sector_strong}:{min_ret}:{max_ret}:{min_amt_ratio}:'
        f'{min_amount}:{theme}:{param_key}:{limit}:bars={cache_progress}'
    )
    redis_client = None
    try:
        redis_client = get_redis()
        raw = redis_client.get(cache_key)
        if raw:
            return json.loads(raw)
    except Exception:
        redis_client = None

    market_fn = _market_code_fn(markets)
    stats: dict[str, dict] = {}
    # 当天开盘前可能有人选择了未来快照。那一刻没有分钟线，不能把空候选
    # 当作真实结果长期留存；等数据到位后必须允许按该分钟重新计算。
    for snap in snapshots:
        try:
            snapshot_rows = _load_mainline_recommend_snapshot(date, snap, param_key)
            should_refresh_empty_today = (
                date == _today()
                and not snapshot_rows
                and bool(latest_time)
                and snap <= latest_time
                and not _mainline_recommend_snapshot_confirmed(date, snap, param_key)
            )
            if snapshot_rows is None or should_refresh_empty_today:
                raw_rows = _calc_mainline_core_rows_from_redis(
                    date, snap, max_sector_rank, min_sector_strong,
                    min_ret, max_ret, min_amt_ratio, theme, min_amount,
                    include_all=True
                )
                if date == _today() and (not latest_time or snap > latest_time) and not raw_rows:
                    # 未来分钟尚未产生，既不统计也不写入空快照。
                    continue
                market_env = _mainline_market_environment(raw_rows, markets, snap)
                rows = raw_rows
                if market_fn:
                    rows = [row for row in rows if market_fn(str(row.get('code') or ''))]
                passed = [
                    row for row in rows
                    if _passes_mainline_candidate(
                        row, max_sector_rank, min_sector_strong,
                        min_ret, max_ret, min_amt_ratio, min_amount
                    )
                ]
                passed.sort(key=lambda r: (
                    -float(r.get('core_score') or 0),
                    int(r.get('sector_rank') or 999),
                    -float(r.get('ret_vs_prevclose') or 0),
                ))
                if _mainline_list_mode(snap) == 'review':
                    page_rows = [row for row in passed if _is_mainline_review_core(row)]
                else:
                    page_rows = passed
                if market_env.get('trade_allowed') is False:
                    page_rows = []
                _save_mainline_market_env_snapshot(
                    date, snap, markets, market_env,
                    raw_count=len(raw_rows), candidate_count=len(page_rows), source='replay'
                )

                snapshot_rows = []
                for index, row in enumerate(page_rows[:limit], start=1):
                    item = row.copy()
                    item['page_rank'] = index
                    snapshot_rows.append(item)
                _save_mainline_recommend_snapshot(
                    date, snap, param_key, param_text, snapshot_rows,
                    snapshot_confirmed=(date != _today() or bool(latest_time and snap <= latest_time)),
                )

            for row in snapshot_rows:
                code = str(row.get('code') or '').zfill(6)
                if not code:
                    continue
                item = stats.setdefault(code, {
                    'recommend_count': 0,
                    'recommend_snapshots': [],
                    'recommend_window': f'{snapshots[0]}-{snapshots[-1]}',
                    'first_snapshot': snap,
                    'last_snapshot': snap,
                    'first_ret_vs_prevclose': None,
                    'last_ret_vs_prevclose': None,
                    'first_amt_vs_prev': None,
                    'last_amt_vs_prev': None,
                    'ret_values': [],
                })
                item['recommend_count'] += 1
                item['recommend_snapshots'].append(snap)
                payload = row.get('payload_json') if isinstance(row.get('payload_json'), dict) else row
                ret = _float_or_default(payload.get('ret_vs_prevclose'), None)
                amount_ratio = _float_or_default(payload.get('amt_vs_prev'), None)
                if item['first_ret_vs_prevclose'] is None:
                    item['first_ret_vs_prevclose'] = ret
                    item['first_amt_vs_prev'] = amount_ratio
                item['last_snapshot'] = snap
                item['last_ret_vs_prevclose'] = ret
                item['last_amt_vs_prev'] = amount_ratio
                if ret is not None:
                    item['ret_values'].append(ret)
        except Exception as e:
            log.warning("主线推荐次数统计失败 %s %s: %s", date, snap, e)

    for item in stats.values():
        item['max_consecutive_count'] = _mainline_max_streak(item.get('recommend_snapshots') or [])
        item['recommend_snapshots_text'] = ','.join(item.get('recommend_snapshots') or [])
        first_ret = item.get('first_ret_vs_prevclose')
        last_ret = item.get('last_ret_vs_prevclose')
        first_amt = item.get('first_amt_vs_prev')
        last_amt = item.get('last_amt_vs_prev')
        item['ret_acceleration'] = (
            round(last_ret - first_ret, 2)
            if first_ret is not None and last_ret is not None else None
        )
        item['amount_acceleration'] = (
            round(last_amt - first_amt, 2)
            if first_amt is not None and last_amt is not None else None
        )
        if (item.get('ret_acceleration') or 0) >= 4:
            item['burst_label'] = '加速爆发'
        elif (item.get('ret_acceleration') or 0) >= 2 and (item.get('amount_acceleration') or 0) >= 0.3:
            item['burst_label'] = '量价加速'
        else:
            item['burst_label'] = ''
        ret_values = item.pop('ret_values', [])
        changes = [
            later - earlier
            for earlier, later in zip(ret_values, ret_values[1:])
        ]
        positive_count = sum(1 for value in changes if value >= 0.05)
        last_two = changes[-2:]
        if (item.get('ret_acceleration') or 0) >= 2 and len(changes) >= 2 and \
                positive_count / len(changes) >= 0.7 and sum(last_two) >= 0.2:
            item['acceleration_persistence_label'] = '持续加速'
        elif (item.get('ret_acceleration') or 0) >= 2 and sum(last_two) >= 0.5:
            item['acceleration_persistence_label'] = '后段加速'
        elif (item.get('ret_acceleration') or 0) >= 2:
            item['acceleration_persistence_label'] = '断续加速'
        else:
            item['acceleration_persistence_label'] = '未加速'
        item['acceleration_persistence_rate'] = round(
            positive_count / len(changes) * 100, 0
        ) if changes else None

    try:
        if redis_client:
            redis_client.set(cache_key, json.dumps(stats, ensure_ascii=False), ex=60)
    except Exception:
        pass
    return stats


def _mainline_observation_profile(row: dict, frequency: dict) -> dict:
    """把盘中过程拆成可观察标签，不参与候选池筛选。"""
    ret = _float_or_default(row.get('ret_vs_prevclose'), 0)
    theme_avg = _float_or_default(row.get('sector_top3_avg_ret'), 0)
    relative_strength = round(ret - theme_avg, 2)
    rank = int(row.get('sector_rank') or 999)
    pullback = _float_or_default(row.get('pullback'), 0)
    amount_ratio = _float_or_default(row.get('amt_vs_prev'), 0)
    amount_acceleration = _float_or_default(frequency.get('amount_acceleration'), 0)
    ret_acceleration = _float_or_default(frequency.get('ret_acceleration'), 0)

    if pullback <= 0.8:
        pullback_label = '高位横住'
    elif pullback <= 2:
        pullback_label = '回撤受控'
    elif pullback <= 4:
        pullback_label = '冲高回撤'
    else:
        pullback_label = '回撤偏大'

    if amount_ratio >= 1.2 and amount_acceleration >= 0.3 and pullback <= 1.5:
        volume_label = '放量承接'
    elif amount_ratio >= 0.8 and pullback <= 2:
        volume_label = '量能跟随'
    elif amount_ratio < 0.8:
        volume_label = '量能不足'
    else:
        volume_label = '量价待确认'

    if rank == 1 and ret_acceleration >= 4:
        execution_label = '爆发核心'
    elif rank == 1 and ret_acceleration >= 2:
        execution_label = '加速主线'
    elif relative_strength >= 2:
        execution_label = '强于主线'
    else:
        execution_label = '常规观察'

    return {
        'relative_theme_strength': relative_strength,
        'relative_theme_label': '强于主线' if relative_strength >= 2 else (
            '同步主线' if relative_strength >= -1 else '弱于主线'
        ),
        'acceleration_persistence_label': frequency.get('acceleration_persistence_label') or '未加速',
        'acceleration_persistence_rate': frequency.get('acceleration_persistence_rate'),
        'volume_follow_through_label': volume_label,
        'pullback_label': pullback_label,
        'execution_label': execution_label,
    }


def _intraday_burst_profile(row: dict, snapshot: str) -> dict:
    """日内爆发捕捉V2：只使用所选快照及其之前已出现的候选过程。"""
    ret = _float_or_default(row.get('ret_vs_prevclose'), 0)
    rank = int(row.get('sector_rank') or 999)
    repeated = int(row.get('recommend_count') or 0)
    mode = str(row.get('trade_mode') or '')
    core_score = _float_or_default(row.get('core_score'), 0)
    amount_ratio = _float_or_default(row.get('amt_vs_prev'), 0)
    sector_strong = int(row.get('sector_strong_count') or 0)
    risks = row.get('risk_tags') or []
    if not isinstance(risks, list):
        risks = [part.strip() for part in str(risks).split('/') if part.strip()]

    score = 0.0
    factors: list[str] = []
    gene_score = 0
    if mode == '主线核心追强':
        score += 4
        factors.append('核心追强')
        gene_score += 1
    elif mode == '核心中位承接':
        score += 2
        factors.append('核心承接')
    if rank <= 3:
        score += 3
        factors.append('主线前排')
        gene_score += 1
    elif rank <= 8:
        score += 1
    if repeated >= 5:
        score += 3
        factors.append(f'持续出现{repeated}次')
        gene_score += 1
    elif repeated >= 3:
        score += 1
        factors.append(f'出现{repeated}次')
    if 5 <= ret < 12:
        score += 3
        factors.append('强势区间')
        gene_score += 1
    elif ret >= 12:
        # 涨停样本中高涨幅票占比不低，保留爆发机会，但不因涨幅过高加满分。
        score += 2
        factors.append('高动量')
        gene_score += 1
    elif ret >= 2:
        score += 1
    if sector_strong >= 3:
        score += 1
        factors.append('板块共振')
    if 0.7 <= amount_ratio <= 15:
        score += 1
        factors.append('量能可承接')
    if core_score >= 180:
        score += 1
    penalties = []
    if any(tag in risks for tag in ('回撤偏大', '冲高回落')):
        score -= 2
        penalties.append('回撤风险')
    if '巨量分歧' in risks:
        score -= 1
        penalties.append('巨量分歧')

    if gene_score >= 3:
        gene_label = '涨停基因强'
    elif gene_score >= 2:
        gene_label = '涨停基因'
    else:
        gene_label = ''
    if score >= 10:
        signal_label = '爆发优先'
    elif score >= 7:
        signal_label = '重点爆发观察'
    else:
        signal_label = '常规候选'
    return {
        'intraday_burst_score': round(score, 1),
        'limitup_gene_score': gene_score,
        'limitup_gene_label': gene_label,
        'intraday_burst_label': signal_label,
        'intraday_burst_factors': factors,
        'intraday_burst_penalties': penalties,
        'intraday_buy_reference_time': _minute_add(snapshot, 2),
    }


def _save_intraday_burst_signals(date: str, snapshot: str, rows: list[dict],
                                  market_status: str,
                                  strategy_id: str = _INTRADAY_BURST_STRATEGY_ID,
                                  version: str = _INTRADAY_BURST_VERSION) -> None:
    """保存每日前5爆发候选，候选原始JSON仍由分钟快照表完整留存。"""
    if not rows:
        return
    try:
        _ensure_mainline_recommend_tables()
        values = [
            (
                strategy_id,
                version,
                date,
                snapshot,
                str(row.get('code') or '').zfill(6),
                index,
                row.get('intraday_burst_score'),
                int(row.get('limitup_gene_score') or 0),
                market_status or '',
                psycopg2.extras.Json(_json_safe({
                    'name': row.get('name') or '',
                    'trade_mode': row.get('trade_mode') or '',
                    'mainline_theme': row.get('mainline_theme') or row.get('trade_theme') or '',
                    'factors': row.get('intraday_burst_factors') or [],
                    'penalties': row.get('intraday_burst_penalties') or [],
                    'recommend_count': row.get('recommend_count') or 0,
                    'ret_vs_prevclose': row.get('ret_vs_prevclose'),
                })),
            )
            for index, row in enumerate(rows, start=1)
            if row.get('code')
        ]
        if not values:
            return
        with mdb.get_connection() as conn:
            with conn.cursor() as cur:
                psycopg2.extras.execute_values(
                    cur,
                    '''
                    INSERT INTO cn_intraday_burst_signal
                        (strategy_id, version, trade_date, snapshot_time, code,
                         page_rank, burst_score, limitup_gene_score, market_status,
                         factor_json, updated_at)
                    VALUES %s
                    ON CONFLICT (strategy_id, version, trade_date, snapshot_time, code)
                    DO UPDATE SET page_rank=EXCLUDED.page_rank,
                                  burst_score=EXCLUDED.burst_score,
                                  limitup_gene_score=EXCLUDED.limitup_gene_score,
                                  market_status=EXCLUDED.market_status,
                                  factor_json=EXCLUDED.factor_json,
                                  updated_at=CURRENT_TIMESTAMP
                    ''',
                    values,
                    template='(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,CURRENT_TIMESTAMP)',
                )
    except Exception as e:
        log.warning('保存日内爆发候选失败 %s %s: %s', date, snapshot, e)


def _is_mainline_review_core(row: dict) -> bool:
    mode = row.get('trade_mode') or _mainline_trade_mode(row)
    return mode in MAINLINE_REVIEW_CORE_MODES


def _is_mainline_review_watch(row: dict) -> bool:
    mode = row.get('trade_mode') or _mainline_trade_mode(row)
    return mode in MAINLINE_REVIEW_WATCH_MODES


def _is_near_30d_high_rush(row: dict, snapshot: str) -> bool:
    """09:45 后首次贴近 30 日高点急拉，不直接给追强。"""
    if _mainline_list_mode(snapshot) != 'review':
        return False
    gap = _minute_gap(snapshot, str(row.get('high_time') or ''))
    if gap is None or gap < 0 or gap > 2:
        return False
    ret = float(row.get('ret_vs_prevclose') or 0)
    pos = float(row.get('pos_in_range') or 0)
    pullback = _float_or_default(row.get('pullback'), 99)
    dist_high = float(row.get('distance_to_30d_high') or 999)
    return ret >= 4.5 and pos >= 90 and pullback <= 1.0 and dist_high <= 1.5


def _is_weak_theme_branch(row: dict) -> bool:
    rank = int(row.get('trade_theme_rank') or 999)
    score = float(row.get('trade_theme_score') or 0)
    return rank > 8 or score < 90


def _leader_early_candidate_codes(date: str, th: MorningSignalThresholds) -> set[str]:
    codes: set[str] = set()
    for snap in (MAINLINE_GUARD_CUTOFF,):
        try:
            for row in _calc_leader_rows_from_redis(date, snap, th):
                code = row.get('code')
                if code:
                    codes.add(code)
        except Exception as e:
            log.warning("加载早盘龙头池失败 %s %s: %s", date, snap, e)
    return codes


def _mainline_early_candidate_codes(date: str, max_sector_rank: int,
                                    min_sector_strong: int, min_ret: float,
                                    max_ret: float, min_amt_ratio: float,
                                    theme: str, min_amount: float) -> set[str]:
    cache_key = (
        f'mainline_early_candidates:v3:{date}:{max_sector_rank}:'
        f'{min_sector_strong}:{min_ret}:{max_ret}:{min_amt_ratio}:'
        f'{min_amount}:{theme}'
    )
    try:
        redis_client = get_redis()
        raw = redis_client.get(cache_key)
        if raw:
            return set(json.loads(raw))
    except Exception:
        redis_client = None

    codes: set[str] = set()
    for snap in (MAINLINE_GUARD_CUTOFF,):
        try:
            rows = _calc_mainline_core_rows_from_redis(
                date, snap, max_sector_rank, min_sector_strong,
                min_ret, max_ret, min_amt_ratio, theme, min_amount,
                include_all=True, early_candidate_codes=set()
            )
            for row in rows:
                if _passes_mainline_candidate(
                    row, max_sector_rank, min_sector_strong,
                    min_ret, max_ret, min_amt_ratio, min_amount
                ):
                    code = row.get('code')
                    if code:
                        codes.add(code)
        except Exception as e:
            log.warning("加载早盘主线池失败 %s %s: %s", date, snap, e)

    try:
        if redis_client:
            redis_client.set(cache_key, json.dumps(sorted(codes)), ex=10 * 60)
    except Exception:
        pass
    return codes


def _annotate_mainline_items(items: list[dict], date: str, snapshot: str,
                             max_sector_rank: int, min_sector_strong: int,
                             min_ret: float, max_ret: float,
                             min_amt_ratio: float, theme: str,
                             min_amount: float,
                             early_candidate_codes: set[str] | None = None) -> list[dict]:
    if not items:
        return items

    groups = _build_mainline_theme_groups(items, items, include_strong_lists=False)
    theme_meta = {g.get('theme'): g for g in groups}
    after_cutoff = _mainline_list_mode(snapshot) == 'review'
    if after_cutoff and early_candidate_codes is None:
        early_candidate_codes = _mainline_early_candidate_codes(
            date, max_sector_rank, min_sector_strong, min_ret,
            max_ret, min_amt_ratio, theme, min_amount
        )

    for item in items:
        sector = item.get('mainline_theme') or item.get('trade_theme') or item.get('best_sector')
        meta = theme_meta.get(sector) or {}
        item['trade_theme_rank'] = meta.get('rank')
        item['trade_theme_score'] = meta.get('score')
        item['trade_theme_status'] = meta.get('status')
        item['late_new_signal'] = (
            after_cutoff
            and early_candidate_codes is not None
            and len(early_candidate_codes) > 0
            and item.get('code') not in early_candidate_codes
        )
        item['weak_theme_branch'] = _is_weak_theme_branch(item)
        item['near_30d_high_rush'] = _is_near_30d_high_rush(item, snapshot)

        guarded = after_cutoff and (
            item['late_new_signal']
            or (item['weak_theme_branch'] and item['near_30d_high_rush'])
            or (item['weak_theme_branch'] and float(item.get('ret_vs_prevclose') or 0) >= 5)
        )
        if guarded and item.get('trade_mode') in MAINLINE_REVIEW_CORE_MODES:
            item['trade_mode'] = MAINLINE_LATE_RUSH_MODE
            item['signal_type'] = MAINLINE_LATE_RUSH_MODE
        item['mode_group'] = _mainline_mode_group(item['trade_mode'])
        item['core_score'] = _mainline_core_score(item)
        item['risk_tags'] = _mainline_risk_tags(item)
    return items


def _mainline_risk_tags(row: dict) -> list[str]:
    ret = float(row.get('ret_vs_prevclose') or 0)
    pos = float(row.get('pos_in_range') or 0)
    pullback = float(row.get('pullback') or 0)
    amt_ratio = float(row.get('amt_vs_prev') or 0)
    amount = float(row.get('amount_so_far') or 0)
    rank = int(row.get('sector_rank') or 999)
    strong = int(row.get('sector_strong_count') or 0)
    risks: list[str] = []
    if row.get('late_new_signal'):
        risks.append('09:45后新出现')
    if row.get('weak_theme_branch'):
        risks.append('分支非核心')
    if row.get('near_30d_high_rush'):
        risks.append('临近30日高急拉')
    if ret > 12:
        risks.append('涨幅偏高')
    if pullback > 4:
        risks.append('冲高回落')
    elif pullback > 2.5:
        risks.append('回撤偏大')
    if pos < 35:
        risks.append('位置偏低需确认')
    if amt_ratio < 0.7:
        risks.append('量能不足')
    elif amt_ratio > 4:
        risks.append('巨量分歧')
    if amount < 5_000_000:
        risks.append('成交额偏小')
    if rank > 8:
        risks.append('主线后排')
    if strong < 2:
        risks.append('主线共振弱')
    return risks[:5]


def _mainline_strict_overnight_profile(row: dict, snapshot: str) -> dict:
    """6/7月回测校准出的隔夜候选收窄规则，用作额外标记/筛选。"""
    ret = float(row.get('ret_vs_prevclose') or 0)
    pos = float(row.get('pos_in_range') or 0)
    pullback = float(row.get('pullback') or 0)
    amt_ratio = float(row.get('amt_vs_prev') or 0)
    tags = row.get('risk_tags') or []
    if not isinstance(tags, list):
        tags = [str(tags)] if tags else []

    hard_tags = {
        '巨量分歧',
        '分支非核心',
        '涨幅偏高',
        '09:45后新出现',
        '临近30日高急拉',
        '冲高回落',
        '回撤偏大',
    }
    hard_hits = [tag for tag in tags if tag in hard_tags]
    blocks: list[str] = []
    if snapshot != '09:35':
        blocks.append('仅09:35早识别')
    if hard_hits:
        blocks.append('硬风险:' + '/'.join(hard_hits[:3]))
    if pullback > 2.5:
        blocks.append('回落超过2.5%')

    no_risk = len(tags) == 0
    patterns: list[str] = []
    if 5 <= ret <= 8 and pos >= 75 and no_risk:
        patterns.append('5-8%强势无风险')
    if 0.7 <= amt_ratio <= 2.5 and 2 <= ret <= 8 and pos >= 90:
        patterns.append('适量高位承接')
    if amt_ratio <= 2.5 and pos >= 90 and no_risk:
        patterns.append('高位无风险')
    if amt_ratio <= 1.5 and 2 <= ret <= 8 and no_risk:
        patterns.append('温和量能无风险')

    ok = not blocks and bool(patterns)
    return {
        'ok': ok,
        'pattern': ' / '.join(patterns),
        'reason': '；'.join(patterns if ok else blocks),
    }


def _mainline_core_score(row: dict) -> float:
    rank = int(row.get('sector_rank') or 999)
    strong = int(row.get('sector_strong_count') or 0)
    top3 = float(row.get('sector_top3_avg_ret') or 0)
    ret = float(row.get('ret_vs_prevclose') or 0)
    pos = float(row.get('pos_in_range') or 0)
    amt_ratio = float(row.get('amt_vs_prev') or 0)
    pullback = float(row.get('pullback') or 0)
    eff = float(row.get('push_efficiency') or 0)
    dist_high = float(row.get('distance_to_30d_high') or 0)
    mode = row.get('trade_mode') or _mainline_trade_mode(row)
    score = 0.0
    score += max(0, 16 - rank) * 5
    score += strong * 7
    score += max(0, top3) * 2.5
    score += max(0, ret) * 1.5
    score += max(0, min(100, pos)) * 0.16
    score += min(28, max(0, amt_ratio - 0.7) * 18)
    score += min(18, max(0, eff))
    score += min(12, max(0, dist_high) * 0.3)
    score -= max(0, pullback - 2) * 4
    if mode == '主线核心追强':
        score += 18
    elif mode == '核心中位承接':
        score += 22
    elif mode == '主线低位突破':
        score += 12
    elif mode == '修复反包观察':
        score += 6
    elif mode == MAINLINE_LATE_RUSH_MODE:
        score -= 12
    if row.get('late_new_signal'):
        score -= 20
    if row.get('weak_theme_branch'):
        score -= 12
    if row.get('near_30d_high_rush'):
        score -= 18
    return round(score, 1)


def _passes_mainline_candidate(row: dict, max_sector_rank: int,
                               min_sector_strong: int, min_ret: float,
                               max_ret: float, min_amt_ratio: float,
                               min_amount: float) -> bool:
    rank = int(row.get('sector_rank') or 999)
    strong = int(row.get('sector_strong_count') or 0)
    ret = float(row.get('ret_vs_prevclose') or 0)
    pullback = _float_or_default(row.get('pullback'), 99)
    amt_ratio = float(row.get('amt_vs_prev') or 0)
    amount = float(row.get('amount_so_far') or 0)
    mode = row.get('trade_mode') or _mainline_trade_mode(row)

    if rank > max_sector_rank or strong < min_sector_strong:
        return False
    if ret < min_ret or ret > max_ret:
        return False
    if amt_ratio < min_amt_ratio:
        return False
    if amount < min_amount and mode not in ('核心中位承接',):
        return False

    if mode == '主线核心追强':
        return pullback <= 4.5
    if mode == '核心中位承接':
        return amount >= 1_000_000 and pullback <= 5
    if mode == '主线低位突破':
        return amount >= 3_000_000 and pullback <= 4
    if mode == '修复反包观察':
        return amount >= 3_000_000 and pullback <= 5
    if mode == MAINLINE_LATE_RUSH_MODE:
        return amount >= 3_000_000 and pullback <= 5
    return amount >= min_amount and pullback <= 4


MAINLINE_THEMES = {
    '机器人': [
        '机器人', '自动化', '减速器', '伺服', '机器视觉',
        '仪器仪表', '激光设备', '智能装备', '工业母机',
    ],
    '玻璃基板': ['玻璃基板', '玻璃玻纤'],
    '科技半导体': [
        '半导体', '芯', '中芯', '高带宽', 'HBM', '分立器件',
        '工业气体', '硅料硅片', '电子化学品', '光刻',
    ],
    '医药生物': [
        '医药', '医疗', '生物', '制药', '药', 'CRO', 'CAR-T',
        '体外诊断', '减肥药', '肝炎',
    ],
    '军工高端制造': ['军工', '航天', '航空', '卫星'],
    '新能源电力': ['电力', '电网', '电源', '储能', '光伏', '锂电', '电池', '新能源'],
}


def _mainline_theme_of_sector(sector: str) -> str:
    s = sector or ''
    for theme, keywords in MAINLINE_THEMES.items():
        if any(k in s for k in keywords):
            return theme
    return '其他主线'


SUMMARY_THEME_RULES = {
    '医药类': [
        '医药', '医疗', '生物', '制药', '药', 'CRO', '创新药', '化学制药',
        '中药', '医疗器械', '体外诊断', 'CAR-T', '肝炎', '原料药',
        '化学制剂', '减肥药', '医疗研发外包',
    ],
    '军工高端制造': ['军工', '航天', '航空', '航海装备', '卫星'],
    'AI手机/消费电子': [
        'AI手机', '消费电子', '其他电子', '光学', 'PCB', '铜缆',
        '连接器', 'F5G', '智能穿戴',
    ],
    '汽车链': ['汽车电子电气系统', '汽车一体化压铸', '汽车零部件', '汽车'],
    '半导体硬科技': [
        '半导体', '芯', '光刻', '分立器件', '电子化学品', '硅片',
        'EDA', '模拟芯片', '数字芯片',
    ],
    '机器人': [
        '机器人', '自动化', '减速器', '伺服', '机器视觉',
        '工业母机', '智能装备',
    ],
    '设备制造': [
        '工程机械', '专用设备', '通用设备', '电机', '机械',
        '输变电设备', '航海装备',
    ],
    '有色资源': ['贵金属', '能源金属', '铅锌', '黄金', '小金属'],
}


def _summary_broad_theme(theme: str) -> str:
    s = theme or ''
    for label, keywords in SUMMARY_THEME_RULES.items():
        if any(k in s for k in keywords):
            return label
    return '其他'


def _theme_summary_stats(items: list[dict]) -> dict:
    ranked = sorted(items, key=lambda r: float(r.get('ret_vs_prevclose') or 0), reverse=True)
    top3 = ranked[:3]
    top5 = ranked[:5]

    def avg(rows, key):
        vals = [float(r.get(key) or 0) for r in rows]
        return sum(vals) / len(vals) if vals else 0.0

    ret2 = sum(1 for r in ranked if float(r.get('ret_vs_prevclose') or 0) >= 2)
    ret5 = sum(1 for r in ranked if float(r.get('ret_vs_prevclose') or 0) >= 5)
    ret8 = sum(1 for r in ranked if float(r.get('ret_vs_prevclose') or 0) >= 8)
    top3_avg = avg(top3, 'ret_vs_prevclose')
    top5_avg = avg(top5, 'ret_vs_prevclose')
    score = (
        max(0, top3_avg) * 5
        + ret5 * 8
        + ret8 * 10
        + min(len(ranked), 80) * 0.1
    )
    return {
        'score': round(score, 1),
        'count': len(ranked),
        'avg_ret': round(avg(ranked, 'ret_vs_prevclose'), 2),
        'top3_avg_ret': round(top3_avg, 2),
        'top5_avg_ret': round(top5_avg, 2),
        'ret2_count': ret2,
        'ret5_count': ret5,
        'ret8_count': ret8,
        'top_stocks': [
            {
                'code': r.get('code'),
                'ret': round(float(r.get('ret_vs_prevclose') or 0), 2),
                'mode': r.get('trade_mode') or _mainline_trade_mode(r),
            }
            for r in top5
        ],
    }


def _build_mainline_summary(date: str, snapshot: str,
                            all_rows: list[dict],
                            theme_groups: list[dict]) -> dict:
    broad_rows: dict[str, list[dict]] = {}
    detail_rows: dict[str, list[dict]] = {}
    for row in all_rows:
        theme = row.get('trade_theme') or row.get('best_sector') or ''
        if not theme:
            continue
        detail_rows.setdefault(theme, []).append(row)
        broad = _summary_broad_theme(theme)
        if broad != '其他':
            broad_rows.setdefault(broad, []).append(row)

    broad_stats = []
    for label, items in broad_rows.items():
        stat = _theme_summary_stats(items)
        if stat['count'] >= 8:
            broad_stats.append({'name': label, **stat})
    broad_stats.sort(key=lambda x: (
        -float(x.get('score') or 0),
        -int(x.get('ret5_count') or 0),
        -float(x.get('top3_avg_ret') or 0),
    ))

    detail_stats = []
    for label, items in detail_rows.items():
        stat = _theme_summary_stats(items)
        if stat['count'] >= 3:
            detail_stats.append({'name': label, 'broad': _summary_broad_theme(label), **stat})
    detail_stats.sort(key=lambda x: (
        -float(x.get('score') or 0),
        -int(x.get('ret5_count') or 0),
        -float(x.get('top3_avg_ret') or 0),
    ))

    if not broad_stats:
        return {
            'status': '无明确主线',
            'sentence': f'{snapshot}：主线暂不清晰，强势点较分散，优先观察前排个股持续性。',
            'primary': '',
            'secondary': '',
            'top_broad': [],
            'top_themes': detail_stats[:6],
        }

    primary = broad_stats[0]
    secondary = broad_stats[1] if len(broad_stats) > 1 else None
    primary_branches = [
        s for s in detail_stats
        if s.get('broad') == primary['name']
    ][:3]
    branch_names = [s['name'] for s in primary_branches]
    branch_text = '、'.join(branch_names) if branch_names else primary['name']

    score_gap = (
        (primary['score'] - secondary['score']) / max(1, secondary['score'])
        if secondary else 1.0
    )
    if primary['ret5_count'] >= 18 and primary['ret8_count'] >= 5 and score_gap >= 0.08:
        status = '主线明确'
        sentence = (
            f"{snapshot}：{primary['name']}多分支共振较明确，"
            f"{branch_text}领涨，5%以上{primary['ret5_count']}只、"
            f"8%以上{primary['ret8_count']}只。"
        )
    elif primary['ret5_count'] >= 10 and primary['ret8_count'] >= 3:
        status = '主线发酵'
        if secondary and score_gap < 0.18:
            sentence = (
                f"{snapshot}：{primary['name']}已在{branch_text}发酵，"
                f"但{secondary['name']}仍强，主线还在确认。"
            )
        else:
            sentence = (
                f"{snapshot}：{primary['name']}开始形成主线，"
                f"{branch_text}强度靠前，等待更多分支扩散确认。"
            )
    else:
        status = '主线分散'
        sec_text = f"，{secondary['name']}同步活跃" if secondary else ''
        sentence = (
            f"{snapshot}：盘面强点仍偏分散，{primary['name']}暂时领先{sec_text}，"
            f"更适合按核心票观察。"
        )

    return {
        'status': status,
        'sentence': sentence,
        'primary': primary['name'],
        'secondary': secondary['name'] if secondary else '',
        'top_broad': broad_stats[:5],
        'top_themes': detail_stats[:8],
    }


def _pick_mainline_theme(rows: list[dict]) -> str:
    theme_scores: dict[str, float] = {}
    for row in rows:
        sector = row.get('trade_theme') or row.get('best_sector') or ''
        theme = _mainline_theme_of_sector(sector)
        rank = int(row.get('sector_rank') or 999)
        if rank > 8:
            continue
        strong = int(row.get('sector_strong_count') or 0)
        ret = max(0, float(row.get('ret_vs_prevclose') or 0))
        top3 = max(0, float(row.get('sector_top3_avg_ret') or 0))
        amt_ratio = max(0, float(row.get('amt_vs_prev') or 0))
        theme_scores[theme] = theme_scores.get(theme, 0) + strong * 2 + ret + top3 + max(0, amt_ratio - 1) * 4
    if not theme_scores:
        return ''
    named_scores = {k: v for k, v in theme_scores.items() if k != '其他主线'}
    if named_scores:
        return max(named_scores.items(), key=lambda x: x[1])[0]
    return '其他主线'


def _calc_mainline_core_rows_from_redis(date: str, snapshot: str,
                                        max_sector_rank: int,
                                        min_sector_strong: int,
                                        min_ret: float,
                                        max_ret: float,
                                        min_amt_ratio: float,
                                        theme: str = 'auto',
                                        min_amount: float = 5_000_000,
                                        include_all: bool = False,
                                        early_candidate_codes: set[str] | None = None) -> list[dict]:
    th = MorningSignalThresholds(
        min_score=0,
        max_sector_rank=0,
        min_sector_strong=0,
        max_ret=max_ret,
        max_price=9999,
        min_efficiency=-999,
    )
    rows = _calc_leader_rows_from_redis(date, snapshot, th, include_all=True)
    items = []
    for row in rows:
        sector = row.get('trade_theme') or row.get('best_sector')
        if not sector:
            continue
        row_theme = _mainline_theme_of_sector(sector)
        if theme not in ('auto', '全部') and row_theme != theme and sector != theme:
            continue
        item = row.copy()
        item['mainline_theme'] = sector
        item['broad_theme'] = row_theme
        item['trade_mode'] = _mainline_trade_mode(item)
        item['mode_group'] = _mainline_mode_group(item['trade_mode'])
        item['core_score'] = _mainline_core_score(item)
        item['risk_tags'] = _mainline_risk_tags(item)
        item['signal_type'] = item['trade_mode']
        items.append(item)

    _annotate_mainline_items(
        items, date, snapshot, max_sector_rank, min_sector_strong,
        min_ret, max_ret, min_amt_ratio, theme, min_amount,
        early_candidate_codes
    )

    out = []
    for item in items:
        if include_all or _passes_mainline_candidate(
            item, max_sector_rank, min_sector_strong,
            min_ret, max_ret, min_amt_ratio, min_amount
        ):
            out.append(item)

    out.sort(key=lambda r: (
        -float(r.get('core_score') or 0),
        int(r.get('sector_rank') or 999),
        -int(r.get('sector_strong_count') or 0),
        -float(r.get('ret_vs_prevclose') or 0),
    ))
    return out


def _mainline_strong_stock_list(rows: list[dict], names: dict[str, str],
                                threshold: float, limit: int = 80) -> list[dict]:
    strong = []
    for row in rows:
        if float(row.get('ret_vs_prevclose') or 0) < threshold:
            continue
        code = row.get('code')
        name = names.get(code, code)
        if _is_st_stock(name):
            continue
        strong.append(row)
        if len(strong) >= limit:
            break
    return [
        {
            'code': r.get('code'),
            'name': names.get(r.get('code'), r.get('code')),
            'ret': round(float(r.get('ret_vs_prevclose') or 0), 2),
            'mode': r.get('trade_mode') or _mainline_trade_mode(r),
            'rank': r.get('sector_rank'),
            'score': round(float(r.get('core_score') or r.get('score') or 0), 1),
            'risk_tags': r.get('risk_tags') or [],
        }
        for r in strong
    ]


def _mainline_theme_strength(theme: str, all_rows: list[dict], stocks: list[dict],
                             strong_names: dict[str, str] | None = None) -> dict:
    strong_names = strong_names or {}
    ranked_rows = sorted(
        [r for r in all_rows if (r.get('trade_theme') or r.get('best_sector')) == theme],
        key=lambda r: float(r.get('ret_vs_prevclose') or 0),
        reverse=True,
    )
    if not ranked_rows:
        ranked_rows = stocks
    display_rows = [
        r for r in ranked_rows
        if not _is_st_stock(strong_names.get(r.get('code'), r.get('code')))
    ]
    if not display_rows:
        display_rows = ranked_rows
    top3 = display_rows[:3]
    top5 = display_rows[:5]

    def avg(items, key):
        vals = [float(r.get(key) or 0) for r in items]
        return sum(vals) / len(vals) if vals else 0.0

    ret2 = sum(1 for r in display_rows if float(r.get('ret_vs_prevclose') or 0) >= 2)
    ret5 = sum(1 for r in display_rows if float(r.get('ret_vs_prevclose') or 0) >= 5)
    ret8 = sum(1 for r in display_rows if float(r.get('ret_vs_prevclose') or 0) >= 8)
    top3_avg = avg(top3, 'ret_vs_prevclose')
    top5_avg = avg(top5, 'ret_vs_prevclose')
    top_pullback = avg(top3, 'pullback')
    top_amt_ratio = avg(top5, 'amt_vs_prev')
    score = (
        ret2 * 2.5
        + ret5 * 8
        + ret8 * 10
        + max(0, top3_avg) * 8
        + max(0, top5_avg) * 4
        + min(24, max(0, top_amt_ratio - 0.6) * 12)
        - max(0, top_pullback - 2) * 4
    )
    if ret5 >= 5 and top3_avg >= 5:
        status = '主线确认'
    elif ret2 >= 4 and top3_avg >= 3:
        status = '发酵中'
    elif top_pullback > 4:
        status = '分歧中'
    else:
        status = '观察'
    return {
        'theme': theme,
        'score': round(score, 1),
        'status': status,
        'member_count': len(ranked_rows),
        'candidate_count': len(stocks),
        'ret2_count': ret2,
        'ret5_count': ret5,
        'ret8_count': ret8,
        'top3_avg_ret': round(top3_avg, 2),
        'top5_avg_ret': round(top5_avg, 2),
        'top3_avg_pullback': round(top_pullback, 2),
        'top5_avg_amount_ratio': round(top_amt_ratio, 2),
        'ret5_stocks': _mainline_strong_stock_list(display_rows, strong_names, 5),
        'ret8_stocks': _mainline_strong_stock_list(display_rows, strong_names, 8),
    }


def _build_mainline_theme_groups(all_rows: list[dict], stocks: list[dict],
                                 include_strong_lists: bool = True) -> list[dict]:
    all_by_theme: dict[str, list[dict]] = {}
    for row in all_rows:
        theme = row.get('trade_theme') or row.get('best_sector')
        if theme:
            all_by_theme.setdefault(theme, []).append(row)

    strong_names: dict[str, str] = {}
    if include_strong_lists:
        strong_codes = []
        for items in all_by_theme.values():
            for row in items:
                if float(row.get('ret_vs_prevclose') or 0) >= 2 and row.get('code'):
                    strong_codes.append(row['code'])
        strong_names = _stock_names(sorted(set(strong_codes)))

    stocks_by_theme: dict[str, list[dict]] = {}
    for row in stocks:
        theme = row.get('mainline_theme') or row.get('trade_theme') or row.get('best_sector')
        if theme:
            stocks_by_theme.setdefault(theme, []).append(row)

    groups = []
    for theme, items in stocks_by_theme.items():
        items.sort(key=lambda r: (
            -float(r.get('core_score') or 0),
            int(r.get('sector_rank') or 999),
            -float(r.get('ret_vs_prevclose') or 0),
        ))
        meta = _mainline_theme_strength(theme, all_by_theme.get(theme, []), items, strong_names)
        mode_counts: dict[str, int] = {}
        for item in items:
            mode = item.get('trade_mode') or '主线观察'
            mode_counts[mode] = mode_counts.get(mode, 0) + 1
        meta['mode_counts'] = mode_counts
        meta['stocks'] = items
        groups.append(meta)

    groups.sort(key=lambda g: (
        -float(g.get('score') or 0),
        -int(g.get('ret5_count') or 0),
        -float(g.get('top3_avg_ret') or 0),
        -int(g.get('candidate_count') or 0),
    ))
    for idx, group in enumerate(groups, start=1):
        group['rank'] = idx
    return groups


def _stock_signal_detail_for_code(code: str, date: str, snapshot: str) -> dict:
    """复用龙头强势计算，返回单只股票详情卡所需的数据。"""
    mainline_rows = _calc_mainline_core_rows_from_redis(
        date, snapshot, 8, 0, -3, 35, 0.3, '全部', 5_000_000,
        include_all=True
    )
    row = next((r for r in mainline_rows if str(r.get('code')).zfill(6) == code), None)
    if row is None:
        th = MorningSignalThresholds(
            min_score=0,
            min_ret=-999,
            max_ret=999,
            min_pos=0,
            max_pullback=999,
            min_efficiency=-999,
            min_amount=0,
            max_amount_ratio=999,
            max_price=99999,
            max_sector_rank=0,
            min_sector_strong=0,
        )
        rows = _calc_leader_rows_from_redis(date, snapshot, th, include_all=True)
        row = next((r for r in rows if str(r.get('code')).zfill(6) == code), None)
    prev_d = str(row.get('prev_d') or _prev_trade_date(date)) if row else _prev_trade_date(date)
    names = _stock_names([code])
    if row:
        item = {k: _json_number(v) for k, v in row.items()}
        sector = item.get('trade_theme') or item.get('best_sector')
        if sector and 'core_score' not in item:
            item['mainline_theme'] = sector
            item['broad_theme'] = _mainline_theme_of_sector(sector)
            item['trade_mode'] = _mainline_trade_mode(item)
            item['mode_group'] = _mainline_mode_group(item['trade_mode'])
            item['core_score'] = _mainline_core_score(item)
            item['risk_tags'] = _mainline_risk_tags(item)
    else:
        item = {
            'date': date,
            'code': code,
            'prev_d': prev_d,
            'score': None,
            'signal_type': '数据不足',
            'tags': '',
        }
    item['code'] = code
    item['name'] = names.get(code, code)
    item['prev_date'] = prev_d
    overnight = _mainline_strict_overnight_profile(item, snapshot)
    item['overnight_strict'] = overnight['ok']
    item['overnight_pattern'] = overnight['pattern']
    item['overnight_reason'] = overnight['reason']
    item['today_bars'] = _minute_bars(date, code)
    item['prev_bars'] = _minute_bars(prev_d, code) if prev_d else []
    return item


class ApiStockSignalDetailHandler(webBase.BaseHandler, ABC):
    """
    GET /api/stock_signal_detail?code=300503&date=2026-06-30&snapshot=10:00
    返回单票详情卡数据，供主线分组表格点击后懒加载分时图。
    """
    def get(self):
        self.set_header('Content-Type', 'application/json; charset=utf-8')
        try:
            code = str(self.get_argument('code', '')).strip().zfill(6)
            date = self.get_argument('date', '') or _latest_data_date()
            snapshot = self.get_argument('snapshot', '') or _default_mainline_snapshot(date)
            if not code or code == '000000':
                self.set_status(400)
                self.write(json.dumps({'ok': False, 'error': 'code required'}, ensure_ascii=False))
                return
            detail = _stock_signal_detail_for_code(code, date, snapshot)
            self.write(json.dumps({
                'ok': True,
                'date': date,
                'snapshot': snapshot,
                'data': detail,
            }, ensure_ascii=False, default=str))
        except Exception as e:
            log.error(f"stock_signal_detail error: {e}", exc_info=True)
            self.set_status(500)
            self.write(json.dumps({'ok': False, 'error': str(e)}, ensure_ascii=False))


# ── 市场过滤规则 ──────────────────────────────────────────────────────────
# 实际数据中的代码分布：
#   沪市A股:  6xxxxx（含科创板688/689）
#   深市A股:  00xxxx
#   创业板:   300xxx, 301xxx
#   科创板:   688xxx, 689xxx
#   京市A股:  9xxxxx（北交所，如920xxx）
MARKET_RULES = {
    'sh':   lambda c: c.startswith('6') and not (c.startswith('688') or c.startswith('689')),
    'sz':   lambda c: c.startswith('00'),
    'cyb':  lambda c: c.startswith('300') or c.startswith('301'),
    'kcb':  lambda c: c.startswith('688') or c.startswith('689'),
    'bj':   lambda c: c.startswith('9'),
}

def _market_code_fn(markets: list):
    """返回市场过滤函数，无过滤时返回 None"""
    if not markets or 'all' in markets:
        return None
    rules = [MARKET_RULES[m] for m in markets if m in MARKET_RULES]
    if not rules:
        return None
    return lambda code: any(r(code) for r in rules)


def _breadth_stats(rows: list[dict], code_filter_fn=None) -> dict:
    values = []
    for row in rows or []:
        code = str(row.get('code') or '')
        if code_filter_fn and not code_filter_fn(code):
            continue
        try:
            pct = float(row.get('ret_vs_prevclose'))
        except (TypeError, ValueError):
            continue
        values.append(pct)

    total = len(values)
    if total == 0:
        return {
            'total': 0, 'up_count': 0, 'down_count': 0, 'up_rate': 0,
            'avg_pct': None, 'median_pct': None,
            'up2_count': 0, 'up5_count': 0, 'down2_count': 0, 'down5_count': 0,
            'down2_rate': 0, 'down5_rate': 0,
        }

    ordered = sorted(values)
    mid = total // 2
    median = ordered[mid] if total % 2 else (ordered[mid - 1] + ordered[mid]) / 2
    up_count = sum(1 for v in values if v > 0)
    down_count = sum(1 for v in values if v < 0)
    down2_count = sum(1 for v in values if v <= -2)
    down5_count = sum(1 for v in values if v <= -5)

    return {
        'total': total,
        'up_count': up_count,
        'down_count': down_count,
        'up_rate': round(up_count / total * 100, 1),
        'avg_pct': round(sum(values) / total, 2),
        'median_pct': round(median, 2),
        'up2_count': sum(1 for v in values if v >= 2),
        'up5_count': sum(1 for v in values if v >= 5),
        'down2_count': down2_count,
        'down5_count': down5_count,
        'down2_rate': round(down2_count / total * 100, 1),
        'down5_rate': round(down5_count / total * 100, 1),
    }


def _pullback_stats(rows: list[dict], code_filter_fn=None) -> dict:
    values = []
    for row in rows or []:
        code = str(row.get('code') or '')
        if code_filter_fn and not code_filter_fn(code):
            continue
        try:
            pullback = float(row.get('pullback'))
        except (TypeError, ValueError):
            continue
        values.append(max(0, pullback))

    total = len(values)
    if total == 0:
        return {
            'total': 0, 'avg_pullback': None, 'median_pullback': None,
            'pullback2_count': 0, 'pullback5_count': 0,
            'pullback2_rate': 0, 'pullback5_rate': 0,
        }

    ordered = sorted(values)
    mid = total // 2
    median = ordered[mid] if total % 2 else (ordered[mid - 1] + ordered[mid]) / 2
    pullback2_count = sum(1 for v in values if v >= 2)
    pullback5_count = sum(1 for v in values if v >= 5)
    return {
        'total': total,
        'avg_pullback': round(sum(values) / total, 2),
        'median_pullback': round(median, 2),
        'pullback2_count': pullback2_count,
        'pullback5_count': pullback5_count,
        'pullback2_rate': round(pullback2_count / total * 100, 1),
        'pullback5_rate': round(pullback5_count / total * 100, 1),
    }


def _mainline_market_environment(rows: list[dict], markets: list[str], snapshot: str) -> dict:
    market_fn = _market_code_fn(markets)
    all_stats = _breadth_stats(rows)
    pool_stats = _breadth_stats(rows, market_fn)
    all_pullback = _pullback_stats(rows)
    pool_pullback = _pullback_stats(rows, market_fn)

    up_rate = float(pool_stats.get('up_rate') or 0)
    median = _float_or_default(pool_stats.get('median_pct'), -99)
    all_up_rate = float(all_stats.get('up_rate') or 0)
    down2_rate = float(pool_stats.get('down2_rate') or 0)
    pullback_median = _float_or_default(pool_pullback.get('median_pullback'), 0)
    pullback2_rate = float(pool_pullback.get('pullback2_rate') or 0)
    pullback5_rate = float(pool_pullback.get('pullback5_rate') or 0)

    if (
        up_rate < 25
        or median <= -2
        or all_up_rate < 20
        or pullback_median >= 2.5
        or pullback2_rate >= 60
        or pullback5_rate >= 25
    ):
        status = '弱市退潮'
        action = '空仓/轻仓观察'
        severity = 'danger'
        trade_allowed = False
        reason = '上涨家数不足、交易池中位数明显下跌，或盘中高点回撤扩散，默认关闭可买池。'
    elif (
        up_rate < 35
        or all_up_rate < 35
        or median < -1
        or down2_rate >= 45
        or pullback_median >= 1.5
        or pullback2_rate >= 40
    ):
        status = '谨慎试错'
        action = '只看无风险前排'
        severity = 'warning'
        trade_allowed = 'guarded'
        reason = '市场宽度偏弱或盘中回撤开始扩散，只允许前排核心小仓试错。'
    else:
        status = '接力可做'
        action = '按主线接力'
        severity = 'success'
        trade_allowed = True
        reason = '市场宽度尚可，可按主线强度和个股买点筛选。'

    return {
        'status': status,
        'action': action,
        'severity': severity,
        'trade_allowed': trade_allowed,
        'reason': reason,
        'snapshot': snapshot,
        'all': all_stats,
        'pool': pool_stats,
        'all_pullback': all_pullback,
        'pool_pullback': pool_pullback,
        'markets': markets or ['all'],
    }


# ── /api/volume_rank ─────────────────────────────────────────────────────
class ApiVolumeRankHandler(webBase.BaseHandler, ABC):
    """
    GET /api/volume_rank
    参数：
      date           YYYY-MM-DD，默认自动取最近有数据的日期
      position       all/low/break，默认all
      vol_threshold  量比阈值，默认1.5
      market         逗号分隔，可选 sh/sz/cyb/kcb/bj/all，默认all
      change         all/up/down，默认all
      refresh        1=强制刷新缓存
      fa_min         因子A最低分（含），不传=不过滤
      fb_min         因子B最低分（含），不传=不过滤
      fc_min         因子C最低分（含），不传=不过滤
      fd_min         因子D最低分（含），不传=不过滤
      factor_mode    and=全部因子满足(默认) / or=任一因子满足
    """
    def get(self):
        date          = self.get_argument('date',          '')
        position      = self.get_argument('position',      'all')
        vol_threshold = float(self.get_argument('vol_threshold', str(VOL_RATIO_TH)))
        force_refresh = self.get_argument('refresh', '0') == '1'
        market_str    = self.get_argument('market', 'all')
        change_filter = self.get_argument('change', 'all')
        markets       = [m.strip() for m in market_str.split(',') if m.strip()]
        market_key    = ','.join(sorted(markets)) if markets else 'all'

        # 因子过滤参数
        def _parse_float(key):
            v = self.get_argument(key, '')
            try: return float(v) if v != '' else None
            except: return None

        fa_min      = _parse_float('fa_min')
        fb_min      = _parse_float('fb_min')
        fc_min      = _parse_float('fc_min')
        fd_min      = _parse_float('fd_min')
        factor_mode = self.get_argument('factor_mode', 'and')  # and / or

        if not date or date == _today():
            latest = _latest_data_date()
            if latest != date:
                date = latest

        self.set_header('Content-Type', 'application/json; charset=utf-8')
        try:
            now_time  = _current_time()
            market_fn = _market_code_fn(markets)

            if force_refresh:
                items = refresh_rank_cache(date, now_time, position, vol_threshold,
                                           market=market_key, market_filter_fn=market_fn,
                                           change_filter=change_filter)
            else:
                items = get_cached_rank(date, position, vol_threshold,
                                        market=market_key, change_filter=change_filter)
                if not items:
                    items = refresh_rank_cache(date, now_time, position, vol_threshold,
                                               market=market_key, market_filter_fn=market_fn,
                                               change_filter=change_filter)

            # 因子过滤（在缓存结果上做，不影响缓存本身）
            factor_filters = [
                ('fa', fa_min), ('fb', fb_min),
                ('fc', fc_min), ('fd', fd_min),
            ]
            active = [(k, v) for k, v in factor_filters if v is not None]
            if active:
                def _pass(item):
                    results = [item.get(k, 0) is not None and item.get(k, 0) >= v
                               for k, v in active]
                    return all(results) if factor_mode == 'and' else any(results)
                items = [i for i in items if _pass(i)]

            self.write(json.dumps({
                'date':      date,
                'time':      now_time,
                'count':     len(items),
                'threshold': vol_threshold,
                'market':    markets,
                'data':      items,
            }, ensure_ascii=False, default=str))
        except Exception as e:
            log.error(f"volume_rank error: {e}", exc_info=True)
            self.set_status(500)
            self.write(json.dumps({'error': str(e)}))


# ── /api/leader_strength ─────────────────────────────────────────────────
class ApiLeaderStrengthHandler(webBase.BaseHandler, ABC):
    """
    GET /api/leader_strength
    早盘“龙头强势”实时榜：复用早盘回放因子，并补充板块排名与今日/昨日分钟线。
    """
    def get(self):
        date = self.get_argument('date', '')
        snapshot = self.get_argument('snapshot', '')
        limit = int(self.get_argument('limit', '80'))
        top_per_day = int(self.get_argument('top_per_day', '0'))
        min_score = float(self.get_argument('min_score', '72'))
        max_sector_rank = int(self.get_argument('max_sector_rank', '3'))
        min_sector_strong = int(self.get_argument('min_sector_strong', '3'))
        force_refresh = self.get_argument('refresh', '0') == '1'

        self.set_header('Content-Type', 'application/json; charset=utf-8')

        try:
            if not date:
                date = _latest_data_date()
            if not snapshot:
                snapshot = _default_leader_snapshot(date)
            limit = max(1, min(limit, 300))

            cache_key = _leader_cache_key(
                date, snapshot, min_score, max_sector_rank, min_sector_strong, limit
            )
            redis_client = get_redis()
            cached = None if force_refresh else redis_client.get(cache_key)
            if cached:
                self.write(cached)
                return

            th = MorningSignalThresholds(
                min_score=min_score,
                max_sector_rank=max_sector_rank,
                min_sector_strong=min_sector_strong,
            )
            candidates = _calc_leader_rows_from_redis(date, snapshot, th)
            if _mainline_list_mode(snapshot) == 'review':
                early_codes = _leader_early_candidate_codes(date, th)
                if early_codes:
                    candidates = [row for row in candidates if row.get('code') in early_codes]
            if top_per_day > 0:
                candidates = candidates[:top_per_day]

            names = _stock_names([r['code'] for r in candidates])
            data = []
            for row in candidates:
                prev_d = str(row.get('prev_d') or '')
                code = row['code']
                name = names.get(code, code)
                if _is_st_stock(name):
                    continue
                item = {k: _json_number(v) for k, v in row.items()}
                item['name'] = name
                item['prev_date'] = prev_d
                item['today_bars'] = _minute_bars(date, code)
                item['prev_bars'] = _minute_bars(prev_d, code) if prev_d else []
                data.append(item)
                if len(data) >= limit:
                    break

            payload = json.dumps({
                'ok': True,
                'date': date,
                'snapshot': snapshot,
                'latest_time': _latest_minute_time(date),
                'count': len(data),
                'params': {
                    'min_score': min_score,
                    'max_sector_rank': max_sector_rank,
                    'min_sector_strong': min_sector_strong,
                    'top_per_day': top_per_day,
                    'late_entry_filter': _mainline_list_mode(snapshot) == 'review',
                },
                'data': data,
            }, ensure_ascii=False, default=str)
            redis_client.set(cache_key, payload, ex=30)
            self.write(payload)
        except Exception as e:
            log.error(f"leader_strength error: {e}", exc_info=True)
            self.set_status(500)
            self.write(json.dumps({'ok': False, 'error': str(e)}, ensure_ascii=False))


# ── /api/mainline_core ───────────────────────────────────────────────────
class ApiMainlineCoreHandler(webBase.BaseHandler, ABC):
    """
    GET /api/mainline_core
    主线核心观察：放宽普通龙头强势的追高/高价限制，展示强主线前排，供人工筛选。
    """
    def get(self):
        date = self.get_argument('date', '')
        snapshot = self.get_argument('snapshot', '')
        limit = int(self.get_argument('limit', '300'))
        max_sector_rank = int(self.get_argument('max_sector_rank', '8'))
        min_sector_strong = int(self.get_argument('min_sector_strong', '0'))
        min_ret = float(self.get_argument('min_ret', '-3'))
        max_ret = float(self.get_argument('max_ret', '35'))
        min_amt_ratio = float(self.get_argument('min_amt_ratio', '0.3'))
        min_amount = float(self.get_argument('min_amount', '5000000'))
        theme = self.get_argument('theme', 'auto').strip() or 'auto'
        market_str = self.get_argument('market', 'all')
        markets = [m.strip() for m in market_str.split(',') if m.strip()]
        market_key = ','.join(sorted(markets)) if markets else 'all'
        include_bars = self.get_argument('include_bars', '0') == '1'
        force_refresh = self.get_argument('refresh', '0') == '1'
        high_prob_only = self.get_argument('high_prob_only', '0') == '1'  # 新增：只看高概率
        strict_overnight = False

        self.set_header('Content-Type', 'application/json; charset=utf-8')
        try:
            if not date:
                date = _latest_data_date()
            if not snapshot:
                snapshot = _default_mainline_snapshot(date)
            limit = max(1, min(limit, 500))
            max_sector_rank = max(1, min(max_sector_rank, 20))
            min_sector_strong = max(0, min(min_sector_strong, 30))
            list_mode = _mainline_list_mode(snapshot)

            cache_key = _mainline_cache_key(
                date, snapshot, max_sector_rank, min_sector_strong,
                min_ret, max_ret, min_amt_ratio, min_amount,
                theme, limit, include_bars, market_key,
                high_prob_only, strict_overnight
            )
            redis_client = get_redis()
            cached = None if force_refresh else redis_client.get(cache_key)
            if cached:
                self.write(cached)
                return

            snapshot_param_key, snapshot_param_text = _mainline_recommend_param_key(
                max_sector_rank, min_sector_strong, min_ret, max_ret,
                min_amt_ratio, theme, min_amount, markets, limit
            )
            latest_time = _latest_minute_time(date)
            stored_snapshot = _load_mainline_recommend_snapshot(
                date, snapshot, snapshot_param_key
            )
            stored_env = _load_mainline_market_env_snapshot(date, snapshot, markets)
            is_live_latest_minute = (
                date == _today()
                and '09:30' <= _current_time() <= '15:01'
                and bool(latest_time and snapshot >= latest_time)
            )
            use_saved_snapshot = (
                not is_live_latest_minute
                and stored_snapshot is not None
                and _mainline_recommend_snapshot_confirmed(date, snapshot, snapshot_param_key)
                and stored_env is not None
            )

            if use_saved_snapshot:
                # 已完成的任意分钟直接恢复当时的候选，不重扫全市场 Redis。
                candidates = _mainline_snapshot_payload_rows(stored_snapshot)
                watch_candidates = []
                all_rows = candidates
                market_env = stored_env
            else:
                raw_all_rows = _calc_mainline_core_rows_from_redis(
                    date, snapshot, max_sector_rank, min_sector_strong,
                    min_ret, max_ret, min_amt_ratio, theme, min_amount,
                    include_all=True
                )
                market_env = _mainline_market_environment(raw_all_rows, markets, snapshot)

                all_rows = raw_all_rows
                market_fn = _market_code_fn(markets)
                if market_fn:
                    all_rows = [row for row in all_rows if market_fn(str(row.get('code') or ''))]
                passed_candidates = [
                    row for row in all_rows
                    if _passes_mainline_candidate(
                        row, max_sector_rank, min_sector_strong,
                        min_ret, max_ret, min_amt_ratio, min_amount
                    )
                ]
                passed_candidates.sort(key=lambda r: (
                    -float(r.get('core_score') or 0),
                    int(r.get('sector_rank') or 999),
                    -float(r.get('ret_vs_prevclose') or 0),
                ))
                if list_mode == 'review':
                    candidates = [row for row in passed_candidates if _is_mainline_review_core(row)]
                    watch_candidates = [row for row in passed_candidates if _is_mainline_review_watch(row)]
                else:
                    candidates = passed_candidates
                    watch_candidates = []

                _save_mainline_market_env_snapshot(
                    date, snapshot, markets, market_env,
                    raw_count=len(raw_all_rows), candidate_count=len(candidates), source='live'
                )
                snapshot_rows_for_store = [] if market_env.get('trade_allowed') is False else candidates[:limit]
                store_rows = []
                for index, row in enumerate(snapshot_rows_for_store, start=1):
                    item = row.copy()
                    item['page_rank'] = index
                    store_rows.append(item)
                _save_mainline_recommend_snapshot(
                    date, snapshot, snapshot_param_key, snapshot_param_text, store_rows
                )

            name_codes = []
            for row in candidates[:limit * 2] + watch_candidates[:limit * 2]:
                code = row.get('code')
                if code:
                    name_codes.append(code)
            names = _stock_names(name_codes)
            latest_prices = _latest_minute_prices(date, sorted(set(name_codes)))
            daily_metrics = _stock_daily_metrics(name_codes, date)
            recommend_frequency = _mainline_recommend_frequency(
                date, snapshot, max_sector_rank, min_sector_strong,
                min_ret, max_ret, min_amt_ratio, theme, min_amount,
                markets, limit
            )

            def compact_item(row: dict) -> dict:
                prev_d = str(row.get('prev_d') or '')
                code = row['code']
                name = names.get(code, code)
                item = {k: _json_number(v) for k, v in row.items()}
                item['score'] = item.get('core_score', item.get('score'))
                item['name'] = name
                item['prev_date'] = prev_d
                current = latest_prices.get(code) or {}
                item['current_price'] = current.get('current_price')
                item['current_time'] = current.get('current_time')
                prev_close = float(item.get('prev_close') or 0)
                current_price = float(item.get('current_price') or 0)
                item['current_change_pct'] = (
                    (current_price / prev_close - 1) * 100
                    if current_price > 0 and prev_close > 0 else None
                )
                freq = recommend_frequency.get(code) or {}
                item['recommend_count'] = int(freq.get('recommend_count') or 0)
                item['max_consecutive_count'] = int(freq.get('max_consecutive_count') or 0)
                item['recommend_window'] = freq.get('recommend_window') or ''
                item['recommend_snapshots'] = freq.get('recommend_snapshots_text') or ''
                item['first_candidate_snapshot'] = freq.get('first_snapshot') or ''
                item['first_candidate_ret_vs_prevclose'] = freq.get('first_ret_vs_prevclose')
                item['ret_acceleration'] = freq.get('ret_acceleration')
                item['amount_acceleration'] = freq.get('amount_acceleration')
                item['burst_label'] = freq.get('burst_label') or ''
                item.update(_mainline_observation_profile(item, freq))
                item.update(_intraday_burst_profile(item, snapshot))

                # 添加全天量比、换手率和概率标签
                metrics = daily_metrics.get(code, {})
                item['daily_volume_ratio'] = metrics.get('daily_volume_ratio', 0.0)
                item['turnoverrate'] = metrics.get('turnoverrate', 0.0)
                snapshot_ratio = float(item.get('amt_vs_prev') or 0)
                daily_ratio = item['daily_volume_ratio']
                turnover = item['turnoverrate']
                prob = _calc_prob_label(snapshot_ratio, daily_ratio, turnover)
                item['prob_label'] = prob['label']
                item['prob_color'] = prob['color']
                item['prob_icon'] = prob['icon']
                item['prob_tip'] = prob['tip']
                if include_bars:
                    item['today_bars'] = _minute_bars(date, code)
                    item['prev_bars'] = _minute_bars(prev_d, code) if prev_d else []
                # 完整候选快照已在入库阶段保存；页面只需要轻量字段，避免把
                # 大量回放原始值重复传给浏览器，拖慢主线分组和个股表渲染。
                table_fields = (
                    'date', 'code', 'name', 'prev_d', 'prev_date',
                    'trade_theme', 'best_sector', 'mainline_theme', 'broad_theme',
                    'sector_rank', 'sector_strong_count', 'sector_top3_avg_ret',
                    'trade_mode', 'signal_type', 'risk_tags', 'tags', 'ret_vs_prevclose',
                    'buy_price', 'pos_in_range', 'pullback', 'distance_to_30d_high',
                    'day_max_up_pct', 'day_close_return_pct', 'next_open_return_pct',
                    'next_1000_return_pct', 'next_1000_max_up_pct',
                    'amt_vs_prev', 'push_efficiency',
                    'current_price', 'current_time', 'current_change_pct',
                    'recommend_count', 'max_consecutive_count', 'recommend_window',
                    'recommend_snapshots', 'first_candidate_snapshot',
                    'first_candidate_ret_vs_prevclose', 'ret_acceleration',
                    'amount_acceleration', 'burst_label',
                    'acceleration_persistence_label', 'acceleration_persistence_rate',
                    'volume_follow_through_label', 'execution_label',
                    'intraday_burst_score', 'intraday_burst_label',
                    'limitup_gene_score', 'limitup_gene_label',
                    'intraday_burst_factors', 'intraday_burst_penalties',
                    'intraday_buy_reference_time',
                    'daily_volume_ratio', 'prob_label', 'prob_color', 'prob_icon',
                    'prob_tip', 'page_rank',
                )
                compact = {field: item.get(field) for field in table_fields if field in item}
                if include_bars:
                    compact['today_bars'] = item.get('today_bars') or []
                    compact['prev_bars'] = item.get('prev_bars') or []
                return compact

            def build_data(source: list[dict], row_limit: int,
                           respect_high_probability: bool = True) -> list[dict]:
                out = []
                for row in source:
                    code = row['code']
                    name = names.get(code, code)
                    if _is_st_stock(name):
                        continue
                    item = compact_item(row)
                    # 如果开启 high_prob_only，只保留高概率的票
                    if respect_high_probability and high_prob_only and item.get('prob_label') != 'high':
                        continue
                    item['page_rank'] = len(out) + 1
                    out.append(item)
                    if len(out) >= row_limit:
                        break
                return out

            data = build_data(candidates, limit)
            watch_data = build_data(watch_candidates, limit)
            burst_source = build_data(candidates, limit, respect_high_probability=False)
            themes = _build_mainline_theme_groups(all_rows, data)
            watch_themes = _build_mainline_theme_groups(all_rows, watch_data)
            summary = _build_mainline_summary(date, snapshot, all_rows, themes)
            if market_env.get('trade_allowed') is False:
                data = []
                watch_data = []
                themes = []
                watch_themes = []
                burst_source = []
                summary = {
                    'status': market_env.get('status') or '弱市退潮',
                    'sentence': f"{snapshot}：{market_env.get('action') or '空仓/轻仓观察'}。{market_env.get('reason') or ''}",
                    'primary': '',
                    'secondary': '',
                    'top_broad': [],
                    'top_themes': [],
                }

            intraday_burst = sorted(
                burst_source,
                key=lambda item: (
                    -float(item.get('intraday_burst_score') or 0),
                    -int(item.get('limitup_gene_score') or 0),
                    -int(item.get('recommend_count') or 0),
                    -float(item.get('ret_vs_prevclose') or 0),
                ),
            )
            if market_env.get('trade_allowed') == 'guarded':
                intraday_burst = intraday_burst[:3]
            else:
                intraday_burst = intraday_burst[:5]
            _save_intraday_burst_signals(
                date, snapshot, intraday_burst, market_env.get('status') or ''
            )

            # V3 继承 V2 的全部评分，只剔除已涨到 9% 及以上、实盘不宜再追的票。
            intraday_burst_v3 = sorted(
                (
                    item for item in burst_source
                    if _float_or_default(item.get('ret_vs_prevclose'), 0) < 9
                ),
                key=lambda item: (
                    -float(item.get('intraday_burst_score') or 0),
                    -int(item.get('limitup_gene_score') or 0),
                    -int(item.get('recommend_count') or 0),
                    -float(item.get('ret_vs_prevclose') or 0),
                ),
            )
            if market_env.get('trade_allowed') == 'guarded':
                intraday_burst_v3 = intraday_burst_v3[:3]
            else:
                intraday_burst_v3 = intraday_burst_v3[:5]
            _save_intraday_burst_signals(
                date, snapshot, intraday_burst_v3, market_env.get('status') or '',
                strategy_id=_INTRADAY_BURST_V3_STRATEGY_ID,
                version=_INTRADAY_BURST_V3_VERSION,
            )

            def compact_theme_groups(groups: list[dict]) -> list[dict]:
                stock_fields = (
                    'code', 'name', 'mainline_theme', 'trade_theme', 'best_sector',
                    'trade_mode', 'risk_tags', 'amt_vs_prev', 'ret_acceleration',
                    'amount_acceleration', 'burst_label',
                    'acceleration_persistence_label', 'volume_follow_through_label',
                    'execution_label',
                )
                result = []
                for group in groups:
                    item = {key: value for key, value in group.items() if key != 'stocks'}
                    item['stocks'] = [
                        {key: stock.get(key) for key in stock_fields if key in stock}
                        for stock in group.get('stocks') or []
                    ]
                    result.append(item)
                return result

            response_themes = compact_theme_groups(themes)
            response_watch_themes = compact_theme_groups(watch_themes)

            payload = json.dumps({
                'ok': True,
                'date': date,
                'snapshot': snapshot,
                'list_mode': list_mode,
                'list_mode_name': '09:45前口径' if list_mode == 'early' else '09:45后风控',
                'review_cutoff': MAINLINE_GUARD_CUTOFF,
                'latest_time': _latest_minute_time(date),
                'count': len(data),
                'theme_count': len(themes),
                'watch_count': len(watch_data),
                'watch_theme_count': len(watch_themes),
                'summary': summary,
                'market_env': market_env,
                'intraday_burst': {
                    'strategy_id': _INTRADAY_BURST_STRATEGY_ID,
                    'version': _INTRADAY_BURST_VERSION,
                    'buy_reference_time': _minute_add(snapshot, 2),
                    'data': intraday_burst,
                },
                'intraday_burst_v3': {
                    'strategy_id': _INTRADAY_BURST_V3_STRATEGY_ID,
                    'version': _INTRADAY_BURST_V3_VERSION,
                    'buy_reference_time': _minute_add(snapshot, 2),
                    'max_ret_vs_prevclose': 9,
                    'data': intraday_burst_v3,
                },
                'params': {
                    'max_sector_rank': max_sector_rank,
                    'min_sector_strong': min_sector_strong,
                    'min_ret': min_ret,
                    'max_ret': max_ret,
                    'min_amt_ratio': min_amt_ratio,
                    'min_amount': min_amount,
                    'theme': theme,
                    'market': markets or ['all'],
                    'include_bars': include_bars,
                    'list_mode': list_mode,
                    'review_cutoff': MAINLINE_GUARD_CUTOFF,
                    'high_prob_only': high_prob_only,
                    'strict_overnight': strict_overnight,
                },
                'data': data,
                'themes': response_themes,
                'watch_data': watch_data,
                'watch_themes': response_watch_themes,
            }, ensure_ascii=False, default=str)
            latest_time = _latest_minute_time(date)
            early_snapshot_settled = (
                date != _today()
                or (snapshot <= MAINLINE_RECOMMEND_END and bool(latest_time and snapshot <= latest_time))
            )
            # 09:50 前的回放快照在分钟线落地后不会再变化，长缓存避免反复重算。
            redis_client.set(cache_key, payload, ex=600 if early_snapshot_settled else 30)
            self.write(payload)
        except Exception as e:
            log.error(f"mainline_core error: {e}", exc_info=True)
            self.set_status(500)
            self.write(json.dumps({'ok': False, 'error': str(e)}, ensure_ascii=False))


# ── /api/strategy_validation ─────────────────────────────────────────────
class ApiStrategyValidationHandler(webBase.BaseHandler, ABC):
    """
    GET /api/strategy_validation
    按天复盘超短主线接力候选池：第一次出现后延迟买入，统计当天/次日收益。
    """
    @gen.coroutine
    def get(self):
        date = self.get_argument('date', '') or _latest_snapshot_trade_date()
        try:
            buy_delay = int(self.get_argument('buy_delay', '2') or 2)
        except Exception:
            buy_delay = 2
        next_cutoff = self.get_argument('next_cutoff', '09:40') or '09:40'
        force_refresh = self.get_argument('refresh', '0') == '1'

        self.set_header('Content-Type', 'application/json; charset=utf-8')
        try:
            result = _strategy_validation_rows(date, buy_delay, next_cutoff, refresh=force_refresh)
            self.write(json.dumps({'ok': True, **result}, ensure_ascii=False, default=str))
        except Exception as e:
            log.error(f"strategy_validation error: {e}", exc_info=True)
            self.set_status(500)
            self.write(json.dumps({'ok': False, 'error': str(e)}, ensure_ascii=False))


# ── /api/strategy_validation_experiment ──────────────────────────────────
class ApiStrategyValidationExperimentHandler(webBase.BaseHandler, ABC):
    """GET /api/strategy_validation_experiment：读取已入库回放，比较V1实验组。"""
    @gen.coroutine
    def get(self):
        try:
            buy_delay = int(self.get_argument('buy_delay', '2') or 2)
        except Exception:
            buy_delay = 2
        next_cutoff = self.get_argument('next_cutoff', '09:40') or '09:40'
        self.set_header('Content-Type', 'application/json; charset=utf-8')
        try:
            result = _strategy_validation_experiment(buy_delay, next_cutoff)
            self.write(json.dumps({'ok': True, **result}, ensure_ascii=False, default=str))
        except Exception as e:
            log.error(f"strategy_validation_experiment error: {e}", exc_info=True)
            self.set_status(500)
            self.write(json.dumps({'ok': False, 'error': str(e)}, ensure_ascii=False))


# ── /api/volume_detail ───────────────────────────────────────────────────
class ApiVolumeDetailHandler(webBase.BaseHandler, ABC):
    """
    GET /api/volume_detail?code=000001&date=2026-06-18
    返回个股深度数据（右栏）
    """
    def get(self):
        code = self.get_argument('code', '')
        date = self.get_argument('date', _today())

        self.set_header('Content-Type', 'application/json; charset=utf-8')
        if not code:
            self.set_status(400)
            self.write(json.dumps({'error': 'code required'}))
            return

        try:
            now_time  = _current_time()
            yest_date = _prev_trade_date(date)
            pre_data  = get_pre_calc(date)
            pre       = pre_data.get(code, {})

            # 今日分时数据
            today_bars = get_day_bars_until(date, code, now_time)
            yest_bars  = get_day_bars(yest_date, code)

            # ── 信号时间轴（记录每次触发量比阈值的分钟）────────────────
            yest_vol_map = {b['time']: b.get('volume', 0) for b in yest_bars}
            signal_timeline = []
            for b in today_bars:
                t     = b['time']
                t_vol = b.get('volume', 0)
                y_vol = yest_vol_map.get(t, 0)
                if y_vol > 0:
                    min_ratio = round(t_vol / y_vol, 2)
                    if min_ratio >= VOL_RATIO_TH:
                        close = b.get('close', 0)
                        pre_c = pre.get('close_y', 0)
                        chg   = round((close - pre_c) / pre_c * 100, 2) if pre_c else 0
                        signal_timeline.append({
                            'time':      t,
                            'min_ratio': min_ratio,
                            'change':    chg,
                        })
            trigger_count = len(signal_timeline)

            # ── 近10日效率走势 ────────────────────────────────────────
            past10 = _last_n_trade_dates(date, 10)
            past10.reverse()  # 从早到晚
            efficiency_trend = []
            for d in past10:
                rows = mdb.executeSqlFetch(
                    'SELECT quote_change, turnover FROM cn_stock_hist_data WHERE date=%s AND code=%s',
                    (d, code)
                )
                if rows and rows[0][1] and float(rows[0][1]) > 0:
                    e = round(float(rows[0][0]) / float(rows[0][1]), 3)
                    efficiency_trend.append({'date': d, 'efficiency': e})
                else:
                    efficiency_trend.append({'date': d, 'efficiency': 0})

            # ── 近10日K线（带位置色带）───────────────────────────────
            kline_rows = mdb.executeSqlFetch(
                '''SELECT date, open, close, high, low, volume, quote_change, turnover
                   FROM cn_stock_hist_data
                   WHERE code=%s AND date <= %s
                   ORDER BY date DESC LIMIT 60''',
                (code, date)
            )
            kline_data = []
            for r in reversed(kline_rows or []):
                kline_data.append({
                    'date':   str(r[0]),
                    'open':   float(r[1] or 0),
                    'close':  float(r[2] or 0),
                    'high':   float(r[3] or 0),
                    'low':    float(r[4] or 0),
                    'volume': float(r[5] or 0),
                    'change': float(r[6] or 0),
                })

            # ── 分时对比（今日+昨日价格线 + 量柱）──────────────────
            today_timeline = [
                {'time': b['time'], 'close': b.get('close', 0), 'volume': b.get('volume', 0)}
                for b in today_bars
            ]
            yest_timeline = [
                {'time': b['time'], 'close': b.get('close', 0), 'volume': b.get('volume', 0)}
                for b in yest_bars
            ]

            # 位置信息
            position_info = {
                'position': pre.get('position', 'other'),
                'ma120':    pre.get('ma120', 0),
                'high120':  pre.get('high120', 0),
                'close_y':  pre.get('close_y', 0),
            }

            self.write(json.dumps({
                'code':             code,
                'date':             date,
                'today_timeline':   today_timeline,
                'yest_timeline':    yest_timeline,
                'signal_timeline':  signal_timeline,
                'trigger_count':    trigger_count,
                'efficiency_trend': efficiency_trend,
                'kline_data':       kline_data,
                'position_info':    position_info,
            }, ensure_ascii=False, default=str))

        except Exception as e:
            log.error(f"volume_detail error: {e}", exc_info=True)
            self.set_status(500)
            self.write(json.dumps({'error': str(e)}))


# ── /api/sector_list ─────────────────────────────────────────────────────
class ApiSectorListHandler(webBase.BaseHandler, ABC):
    """GET /api/sector_list → 返回所有板块名称及股票数"""
    def get(self):
        self.set_header('Content-Type', 'application/json; charset=utf-8')
        try:
            rows = mdb.executeSqlFetch(
                'SELECT sector, COUNT(*) AS cnt FROM cn_stock_sector_map GROUP BY sector ORDER BY cnt DESC'
            )
            data = [{'sector': r[0], 'count': r[1]} for r in (rows or [])]
            self.write(json.dumps({'data': data}, ensure_ascii=False))
        except Exception as e:
            self.set_status(500)
            self.write(json.dumps({'error': str(e)}))


# ── /api/sector_stocks ───────────────────────────────────────────────────
class ApiSectorStocksHandler(webBase.BaseHandler, ABC):
    """GET /api/sector_stocks?sector=机器人 → 返回板块内股票列表"""
    def get(self):
        sector = self.get_argument('sector', '')
        self.set_header('Content-Type', 'application/json; charset=utf-8')
        if not sector:
            self.set_status(400)
            self.write(json.dumps({'error': 'sector required'}))
            return
        try:
            rows = mdb.executeSqlFetch(
                '''SELECT m.code, s.name
                   FROM cn_stock_sector_map m
                   LEFT JOIN (
                       SELECT DISTINCT ON (code) code, name FROM cn_stock_spot ORDER BY code, date DESC
                   ) s ON s.code = m.code
                   WHERE m.sector = %s
                   ORDER BY m.code''',
                (sector,)
            )
            data = [{'code': r[0], 'name': r[1] or r[0]} for r in (rows or [])]
            self.write(json.dumps({'sector': sector, 'data': data}, ensure_ascii=False))
        except Exception as e:
            self.set_status(500)
            self.write(json.dumps({'error': str(e)}))


# ── /api/sector_map（增删）──────────────────────────────────────────────
class ApiSectorMapHandler(webBase.BaseHandler, ABC):
    """
    POST   /api/sector_map        body: {code, sector}  → 新增映射
    DELETE /api/sector_map        body: {code, sector}  → 删除映射
    """
    def post(self):
        self.set_header('Content-Type', 'application/json; charset=utf-8')
        try:
            body   = json.loads(self.request.body)
            code   = str(body.get('code', '')).strip()
            sector = str(body.get('sector', '')).strip()
            if not code or not sector:
                self.set_status(400)
                self.write(json.dumps({'error': 'code and sector required'}))
                return
            mdb.executeSql(
                'INSERT INTO cn_stock_sector_map (code, sector) VALUES (%s, %s) ON CONFLICT DO NOTHING',
                (code, sector)
            )
            _cache_sectors()   # 刷新Redis缓存
            self.write(json.dumps({'ok': True}))
        except Exception as e:
            self.set_status(500)
            self.write(json.dumps({'error': str(e)}))

    def delete(self):
        self.set_header('Content-Type', 'application/json; charset=utf-8')
        try:
            body   = json.loads(self.request.body)
            code   = str(body.get('code', '')).strip()
            sector = str(body.get('sector', '')).strip()
            if not code or not sector:
                self.set_status(400)
                self.write(json.dumps({'error': 'code and sector required'}))
                return
            mdb.executeSql(
                'DELETE FROM cn_stock_sector_map WHERE code=%s AND sector=%s',
                (code, sector)
            )
            _cache_sectors()
            self.write(json.dumps({'ok': True}))
        except Exception as e:
            self.set_status(500)
            self.write(json.dumps({'error': str(e)}))


# ── /api/sector_map/batch ────────────────────────────────────────────────
class ApiSectorMapBatchHandler(webBase.BaseHandler, ABC):
    """
    PUT /api/sector_map/batch
    body: {code: "000001", sectors: ["机器人", "新能源"]}
    覆盖设置该股票的所有板块
    """
    def put(self):
        self.set_header('Content-Type', 'application/json; charset=utf-8')
        try:
            body    = json.loads(self.request.body)
            code    = str(body.get('code', '')).strip()
            sectors = body.get('sectors', [])
            if not code:
                self.set_status(400)
                self.write(json.dumps({'error': 'code required'}))
                return
            # 先删除该股票所有板块，再插入
            mdb.executeSql('DELETE FROM cn_stock_sector_map WHERE code=%s', (code,))
            for s in sectors:
                s = str(s).strip()
                if s:
                    mdb.executeSql(
                        'INSERT INTO cn_stock_sector_map (code, sector) VALUES (%s,%s) ON CONFLICT DO NOTHING',
                        (code, s)
                    )
            _cache_sectors()
            self.write(json.dumps({'ok': True, 'code': code, 'sectors': sectors}))
        except Exception as e:
            self.set_status(500)
            self.write(json.dumps({'error': str(e)}))


# ── /api/sector_map/stock ────────────────────────────────────────────────
class ApiSectorMapStockHandler(webBase.BaseHandler, ABC):
    """GET /api/sector_map/stock?code=000001 → 返回该股票的所有板块"""
    def get(self):
        code = self.get_argument('code', '')
        self.set_header('Content-Type', 'application/json; charset=utf-8')
        if not code:
            self.set_status(400)
            self.write(json.dumps({'error': 'code required'}))
            return
        try:
            rows = mdb.executeSqlFetch(
                'SELECT sector FROM cn_stock_sector_map WHERE code=%s ORDER BY sector',
                (code,)
            )
            sectors = [r[0] for r in (rows or [])]
            self.write(json.dumps({'code': code, 'sectors': sectors}, ensure_ascii=False))
        except Exception as e:
            self.set_status(500)
            self.write(json.dumps({'error': str(e)}))


# ── /api/trade_theme/stock ───────────────────────────────────────────
class ApiTradeThemeStockHandler(webBase.BaseHandler, ABC):
    """
    GET /api/trade_theme/stock?code=300162
    PUT /api/trade_theme/stock  body: {code, sector}
    """
    def get(self):
        code = self.get_argument('code', '').strip().zfill(6)
        self.set_header('Content-Type', 'application/json; charset=utf-8')
        if not code:
            self.set_status(400)
            self.write(json.dumps({'ok': False, 'error': 'code required'}, ensure_ascii=False))
            return
        try:
            dom_rows = mdb.executeSqlFetch(
                '''SELECT trade_theme, confidence, source, reason, updated_at
                   FROM cn_stock_trade_theme WHERE code=%s''',
                (code,)
            )
            raw_rows = mdb.executeSqlFetch(
                'SELECT sector FROM cn_stock_sector_map WHERE code=%s ORDER BY sector',
                (code,)
            )
            name = _stock_name(code)
            theme = None
            if dom_rows:
                r = dom_rows[0]
                theme = {
                    'sector': r[0],
                    'confidence': float(r[1] or 0),
                    'source': r[2],
                    'reason': r[3],
                    'updated_at': str(r[4]) if r[4] else '',
                }
            self.write(json.dumps({
                'ok': True,
                'code': code,
                'name': name,
                'theme': theme,
                'dominant': theme,
                'sectors': [r[0] for r in raw_rows or []],
            }, ensure_ascii=False, default=str))
        except Exception as e:
            log.error("trade_theme get error: %s", e, exc_info=True)
            self.set_status(500)
            self.write(json.dumps({'ok': False, 'error': str(e)}, ensure_ascii=False))

    def put(self):
        self.set_header('Content-Type', 'application/json; charset=utf-8')
        try:
            body = json.loads(self.request.body or '{}')
            code = str(body.get('code', '')).strip().zfill(6)
            sector = str(body.get('sector', '')).strip()
            if not code or not sector:
                self.set_status(400)
                self.write(json.dumps({'ok': False, 'error': 'code and sector required'}, ensure_ascii=False))
                return
            name = _stock_name(code)
            with mdb.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        '''INSERT INTO cn_stock_trade_theme
                           (code, name, trade_theme, confidence, source, reason, candidate_count, updated_at)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,NOW())
                           ON CONFLICT (code) DO UPDATE SET
                               name = EXCLUDED.name,
                               trade_theme = EXCLUDED.trade_theme,
                               confidence = EXCLUDED.confidence,
                               source = EXCLUDED.source,
                               reason = EXCLUDED.reason,
                               updated_at = NOW()''',
                        (code, name, sector, 100, 'manual', '用户手动设置交易主线', 1)
                    )
                    cur.execute(
                        'INSERT INTO cn_stock_sector_map (code, sector) VALUES (%s,%s) ON CONFLICT DO NOTHING',
                        (code, sector)
                    )
            _refresh_sector_runtime_cache()
            self.write(json.dumps({
                'ok': True,
                'code': code,
                'name': name,
                'trade_theme': sector,
                'sector': sector,
            }, ensure_ascii=False))
        except Exception as e:
            log.error("trade_theme put error: %s", e, exc_info=True)
            self.set_status(500)
            self.write(json.dumps({'ok': False, 'error': str(e)}, ensure_ascii=False))


# ── /api/trade_theme_list ────────────────────────────────────────────
class ApiTradeThemeListHandler(webBase.BaseHandler, ABC):
    """GET /api/trade_theme_list → 返回交易主线列表及股票数"""
    def get(self):
        self.set_header('Content-Type', 'application/json; charset=utf-8')
        try:
            rows = mdb.executeSqlFetch(
                '''SELECT trade_theme, COUNT(*) AS cnt
                   FROM cn_stock_trade_theme
                   GROUP BY trade_theme
                   ORDER BY cnt DESC, trade_theme'''
            )
            data = [{'sector': r[0], 'count': r[1]} for r in rows or []]
            self.write(json.dumps({'ok': True, 'data': data}, ensure_ascii=False))
        except Exception as e:
            log.error("trade_theme_list error: %s", e, exc_info=True)
            self.set_status(500)
            self.write(json.dumps({'ok': False, 'error': str(e)}, ensure_ascii=False))


# ── /api/trade_theme_stocks ──────────────────────────────────────────
class ApiTradeThemeStocksHandler(webBase.BaseHandler, ABC):
    """GET /api/trade_theme_stocks?sector=LED&search=300162"""
    def get(self):
        sector = self.get_argument('sector', '').strip()
        search = self.get_argument('search', '').strip()
        page = int(self.get_argument('page', '1'))
        size = min(int(self.get_argument('size', '200')), 1000)
        offset = (page - 1) * size
        self.set_header('Content-Type', 'application/json; charset=utf-8')

        try:
            conditions = []
            params = []
            if sector:
                conditions.append('trade_theme = %s')
                params.append(sector)
            if search:
                kw = f'%{search}%'
                conditions.append('(code LIKE %s OR name LIKE %s OR trade_theme LIKE %s)')
                params.extend([kw, kw, kw])
            where = (' WHERE ' + ' AND '.join(conditions)) if conditions else ''

            total_row = mdb.executeSqlFetch(
                f'SELECT COUNT(*) FROM cn_stock_trade_theme{where}',
                tuple(params)
            )
            total = int(total_row[0][0]) if total_row else 0
            rows = mdb.executeSqlFetch(
                f'''SELECT code, name, trade_theme, confidence, source, reason, updated_at
                    FROM cn_stock_trade_theme
                    {where}
                    ORDER BY code
                    LIMIT %s OFFSET %s''',
                tuple(params + [size, offset])
            )
            data = [
                {
                    'code': r[0],
                    'name': r[1] or r[0],
                    'trade_theme': r[2],
                    'confidence': float(r[3] or 0),
                    'source': r[4],
                    'reason': r[5],
                    'updated_at': str(r[6]) if r[6] else '',
                }
                for r in rows or []
            ]
            self.write(json.dumps({
                'ok': True,
                'sector': sector,
                'total': total,
                'page': page,
                'size': size,
                'data': data,
            }, ensure_ascii=False, default=str))
        except Exception as e:
            log.error("trade_theme_stocks error: %s", e, exc_info=True)
            self.set_status(500)
            self.write(json.dumps({'ok': False, 'error': str(e)}, ensure_ascii=False))


# ── /api/redis_dates ─────────────────────────────────────────────────────
class ApiRedisDatesHandler(webBase.BaseHandler, ABC):
    """GET /api/redis_dates → 返回 Redis 中有分钟数据的日期列表"""
    def get(self):
        self.set_header('Content-Type', 'application/json; charset=utf-8')
        try:
            from instock.core.minute_bar_collector import get_redis
            r = get_redis()
            dates = set()
            cursor = 0
            while True:
                cursor, keys = r.scan(cursor, match='minute_bar:*', count=2000)
                for k in keys:
                    parts = k.split(':')
                    if len(parts) >= 2:
                        dates.add(parts[1])
                if cursor == 0:
                    break
            sorted_dates = sorted(dates, reverse=True)
            self.write(json.dumps({'dates': sorted_dates}, ensure_ascii=False))
        except Exception as e:
            self.set_status(500)
            self.write(json.dumps({'error': str(e)}))


# ── /api/redis_query ─────────────────────────────────────────────────────
class ApiRedisQueryHandler(webBase.BaseHandler, ABC):
    """
    GET /api/redis_query?date=2026-06-23&code=000001
    返回 Redis 中该股该日的分钟K线数据，以及统计信息
    """
    def get(self):
        date = self.get_argument('date', '')
        code = self.get_argument('code', '').strip().zfill(6) if self.get_argument('code', '') else ''
        self.set_header('Content-Type', 'application/json; charset=utf-8')

        if not date:
            self.set_status(400)
            self.write(json.dumps({'error': 'date required'}))
            return

        try:
            from instock.core.minute_bar_collector import get_redis, redis_key, get_day_bars, get_all_codes_for_date
            import json as _json

            r = get_redis()

            if code:
                # 查单股
                bars = get_day_bars(date, code)
                # 从 PG 补充股票名称
                name = ''
                try:
                    rows = mdb.executeSqlFetch(
                        'SELECT name FROM cn_stock_spot WHERE code=%s ORDER BY date DESC LIMIT 1',
                        (code,)
                    )
                    if rows:
                        name = rows[0][0] or ''
                except Exception:
                    pass

                stats = {}
                if bars:
                    vols   = [b.get('volume', 0) for b in bars]
                    closes = [b.get('close', 0) for b in bars]
                    stats = {
                        'bar_count':  len(bars),
                        'time_range': f"{bars[0]['time']}~{bars[-1]['time']}",
                        'total_vol':  round(sum(vols), 0),
                        'avg_vol':    round(sum(vols) / len(vols), 0) if vols else 0,
                        'price_open': bars[0].get('open', 0),
                        'price_last': closes[-1] if closes else 0,
                        'price_high': max(b.get('high', 0) for b in bars),
                        'price_low':  min(b.get('low', 0) for b in bars),
                    }

                self.write(_json.dumps({
                    'date':  date,
                    'code':  code,
                    'name':  name,
                    'stats': stats,
                    'bars':  bars,
                }, ensure_ascii=False, default=str))

            else:
                # 查日期概览：返回当日所有股票的汇总统计
                codes = get_all_codes_for_date(date)
                total_codes = len(codes)

                # 抽样5只展示示例
                sample_codes = codes[:5] if codes else []
                samples = []
                for c in sample_codes:
                    b = get_day_bars(date, c)
                    if b:
                        samples.append({
                            'code':      c,
                            'bar_count': len(b),
                            'time_range': f"{b[0]['time']}~{b[-1]['time']}",
                        })

                self.write(_json.dumps({
                    'date':        date,
                    'total_codes': total_codes,
                    'samples':     samples,
                }, ensure_ascii=False, default=str))

        except Exception as e:
            log.error(f"redis_query error: {e}", exc_info=True)
            self.set_status(500)
            self.write(json.dumps({'error': str(e)}))


# ── 分钟K线查询 API ───────────────────────────────────────────────────────────

class ApiMinuteBarsHandler(webBase.BaseHandler, ABC):
    """GET /api/minute_bars?code=000001&date=2026-06-25 → 完整分钟K线"""
    def get(self):
        code = self.get_argument('code', '').strip().zfill(6)
        date = self.get_argument('date', _today())
        self.set_header('Content-Type', 'application/json; charset=utf-8')
        try:
            # 历史回放时 Redis 通常已过期，需回退到 PG 的分钟线留存。
            bars = _minute_bars(date, code)
            self.write(json.dumps({'ok': True, 'code': code, 'date': date, 'bars': bars},
                                  ensure_ascii=False, default=str))
        except Exception as e:
            self.set_status(500)
            self.write(json.dumps({'ok': False, 'error': str(e)}))


# ── 因子配置 API ──────────────────────────────────────────────────────────────

class ApiFactorConfigHandler(webBase.BaseHandler, ABC):
    """GET → 读取配置；POST → 保存配置；DELETE → 重置默认"""

    def get(self):
        from instock.core.factor_config import load_config
        self.write(json.dumps(load_config(), ensure_ascii=False))

    def post(self):
        from instock.core.factor_config import save_config
        try:
            body = json.loads(self.request.body or '{}')
            ok = save_config(body)
            self.write(json.dumps({'ok': ok}, ensure_ascii=False))
        except Exception as e:
            self.set_status(400)
            self.write(json.dumps({'ok': False, 'error': str(e)}))

    def delete(self):
        from instock.core.factor_config import reset_config
        cfg = reset_config()
        self.write(json.dumps({'ok': True, 'config': cfg}, ensure_ascii=False))


# ── 单股票因子得分计算 API ──────────────────────────────────────────────────────

class ApiScoreSingleHandler(webBase.BaseHandler, ABC):
    """
    POST /api/score_single
    body: { code, date, hhmm, factors: ['A','B','C','D'] }
    """

    def post(self):
        try:
            body    = json.loads(self.request.body or '{}')
            code    = str(body.get('code', '')).strip().zfill(6)
            date    = body.get('date', datetime.date.today().strftime('%Y-%m-%d'))
            hhmm    = body.get('hhmm', '15:00')
            factors = body.get('factors', ['A', 'B', 'C', 'D'])

            if not code:
                self.set_status(400)
                self.write(json.dumps({'ok': False, 'error': '股票代码不能为空'}))
                return

            result = _score_single(code, date, hhmm, factors)
            self.write(json.dumps(result, ensure_ascii=False, default=str))
        except Exception as e:
            log.error(f"score_single error: {e}", exc_info=True)
            self.set_status(500)
            self.write(json.dumps({'ok': False, 'error': str(e)}))


def _score_single(code: str, date: str, hhmm: str, factors: list) -> dict:
    """计算单只股票在指定时刻的因子得分明细"""
    from instock.core.factor_config import (
        load_config, apply_factor_a, apply_factor_b,
        apply_factor_c, apply_factor_d, _get_pull_quality_avgs,
    )
    from instock.core.volume_pre_calc import get_pre_calc, get_sectors
    from instock.core.volume_rank_engine import (
        _prev_trade_date, calc_realtime_vol_ratio, calc_price_slope,
        gen_volume_label,
    )
    from collections import defaultdict

    cfg = load_config()

    # ── 基础数据 ──────────────────────────────────────────────────────────────
    pre_data  = get_pre_calc(date)
    sectors   = get_sectors()
    yest_date = _prev_trade_date(date)

    # 股票名称
    name_rows = mdb.executeSqlFetch(
        'SELECT name FROM cn_stock_spot WHERE code=%s ORDER BY date DESC LIMIT 1',
        (code,)
    )
    stock_name = name_rows[0][0] if name_rows else code

    # pre_calc
    pre = pre_data.get(code)
    pre_missing = pre is None
    if pre_missing:
        # 尝试从 DB 直接补算（pre_calc 未生成时）
        pre = _calc_pre_from_db(code, date)

    # ── 分钟K线 ───────────────────────────────────────────────────────────────
    today_bars  = get_day_bars_until(date,      code, hhmm)
    yest_bars   = get_day_bars_until(yest_date, code, hhmm)   # 昨日同期（用于实时量比）
    yest_remain = get_day_bars_from(yest_date,  code, hhmm)   # 昨日剩余（用于虚拟全日量）
    yest_all    = get_day_bars_until(yest_date, code, '15:00')
    yest_total_vol = sum(b.get('volume', 0) for b in yest_all)

    bars_missing = not today_bars

    # ── 当前价格与涨跌幅 ──────────────────────────────────────────────────────
    if today_bars:
        current_close = today_bars[-1].get('close', 0)
    else:
        current_close = 0.0
    pre_close_y   = pre.get('close_y', 0) if pre else 0
    today_change  = round((current_close - pre_close_y) / pre_close_y * 100, 2) \
        if pre_close_y > 0 and current_close > 0 else 0.0
    today_turnover = pre.get('turnover_y', 0) if pre else 0   # 昨日换手率代替

    # 量比
    rt_vol_ratio = calc_realtime_vol_ratio(today_bars, yest_bars)
    today_vol_accum  = sum(b.get('volume', 0) for b in today_bars)
    yest_remain_vol  = sum(b.get('volume', 0) for b in yest_remain)
    virt_ratio = round((today_vol_accum + yest_remain_vol) / yest_total_vol, 2) \
        if yest_total_vol > 0 else 0.0

    price_slope = calc_price_slope(today_bars)

    # ── 因子A ─────────────────────────────────────────────────────────────────
    fa_detail = _factor_a_detail(cfg, pre, current_close, rt_vol_ratio, today_change, factors)

    # ── 因子B：买力强度（用今日实时分钟数据）────────────────────────────────────
    fb_detail = _factor_b_detail(cfg, today_bars, factors)

    # ── 因子C ─────────────────────────────────────────────────────────────────
    fc_detail = _factor_c_detail(cfg, today_bars, yest_remain, yest_total_vol, hhmm, factors)

    # ── 因子D：用全市场第一轮数据（Redis缓存的排行结果）估算板块信息 ──────────
    fd_detail = _factor_d_detail(cfg, code, sectors, date, hhmm, factors)

    # ── 汇总 ──────────────────────────────────────────────────────────────────
    fa = fa_detail['score']
    fb = fb_detail['score']
    fc = fc_detail['score']
    fd = fd_detail['score']
    is_veto = fa_detail.get('is_veto', False)
    total = round(fa + fb + fc + fd, 2) if not is_veto else fa

    vol_label = gen_volume_label(virt_ratio, hhmm, today_change)

    # 效率方向
    e_today = today_change / today_turnover if today_turnover else 0
    e_y     = pre.get('change_y', 0) / pre.get('turnover_y', 1) if pre and pre.get('turnover_y') else 0
    eff_dir = 'up' if e_today > e_y else ('down' if e_today < e_y else 'flat')
    signal_detail = _stock_signal_detail_for_code(code, date, hhmm)

    return {
        'ok': True,
        'code': code,
        'name': stock_name,
        'date': date,
        'hhmm': hhmm,
        'factors_used': factors,
        # 摘要
        'summary': {
            'score':         total,
            'is_veto':       is_veto,
            'current_price': round(current_close, 2),
            'today_change':  today_change,
            'rt_vol_ratio':  rt_vol_ratio,
            'virt_ratio':    virt_ratio,
            'price_slope':   price_slope,
            'eff_dir':       eff_dir,
            'vol_label':     vol_label,
            'position':      pre.get('position', 'unknown') if pre else 'unknown',
        },
        # 预计算数据
        'pre_calc': {
            'available': not pre_missing,
            'ma120':          round(pre.get('ma120', 0), 3)   if pre else None,
            'high120':        round(pre.get('high120', 0), 3) if pre else None,
            'position':       pre.get('position', '-')        if pre else None,
            'close_y':        pre.get('close_y', 0)           if pre else None,
            'turnover_y':     pre.get('turnover_y', 0)        if pre else None,
            'change_y':       pre.get('change_y', 0)          if pre else None,
            'turnover_prev':  pre.get('turnover_prev', 0)     if pre else None,
            'change_prev':    pre.get('change_prev', 0)       if pre else None,
        },
        # K线数据
        'bars_info': {
            'available':       not bars_missing,
            'today_bar_count': len(today_bars),
            'yest_bar_count':  len(yest_bars),
            'yest_total_vol':  yest_total_vol,
            'yest_date':       yest_date,
        },
        # 完整分钟K线（供图表）
        'minute_bars': {
            'today': today_bars,
            'yest':  get_day_bars(yest_date, code) if yest_date else [],
        },
        # 复用龙头强势详情卡的数据结构，供单股票页和列表页共用展示。
        'signal_detail': signal_detail,
        # 四因子明细
        'factor_a': fa_detail,
        'factor_b': fb_detail,
        'factor_c': fc_detail,
        'factor_d': fd_detail,
    }


def _calc_pre_from_db(code: str, date: str) -> dict:
    """pre_calc 不存在时，从 DB 临时计算基础字段"""
    try:
        rows = mdb.executeSqlFetch(
            '''SELECT date, close, high, turnover, quote_change
               FROM cn_stock_hist_data WHERE code=%s AND date<=%s
               ORDER BY date DESC LIMIT 122''',
            (code, date)
        )
        if not rows:
            return {}
        rows = list(reversed(rows))
        recent = rows[-120:]
        closes  = [float(r[1] or 0) for r in recent if r[1]]
        highs   = [float(r[2] or 0) for r in recent if r[2]]
        ma120   = sum(closes) / len(closes) if closes else 0
        high120 = max(highs) if highs else 0
        last    = rows[-1]
        prev    = rows[-2] if len(rows) >= 2 else last
        close_y = float(last[1] or 0)

        def _pos(c, m, h):
            if not c or not m: return 'other'
            if c <= m * 1.05: return 'low'
            if c >= h * 0.98: return 'break'
            if c > m * 1.2:   return 'high'
            return 'other'

        return {
            'ma120': round(ma120, 4), 'high120': round(high120, 4),
            'position': _pos(close_y, ma120, high120),
            'close_y': close_y,
            'turnover_y':    float(last[3] or 0),
            'change_y':      float(last[4] or 0),
            'turnover_prev': float(prev[3] or 0),
            'change_prev':   float(prev[4] or 0),
        }
    except Exception as e:
        log.warning(f"_calc_pre_from_db failed: {e}")
        return {}


def _factor_a_detail(cfg, pre, current_close, rt_vol_ratio, today_change, factors) -> dict:
    from instock.core.factor_config import apply_factor_a
    if 'A' not in factors or not cfg.get('A', {}).get('enabled', True):
        return {'score': 0, 'enabled': False, 'skipped': True, 'steps': []}
    if not pre:
        return {'score': 0, 'enabled': True, 'skipped': True, 'reason': 'pre_calc缺失', 'steps': []}

    a_cfg  = cfg.get('A', {})
    th     = a_cfg.get('thresholds', {})
    veto_c = a_cfg.get('veto', {})
    scores = a_cfg.get('scores', {})

    high1   = pre.get('high1',   pre.get('high120', 0))
    high2   = pre.get('high2',   0)
    close_y = pre.get('close_y', 0)
    pos     = pre.get('position', 'other')
    ratio   = th.get('ratio', {}).get('value', 1.2)

    h1_above = high1 > close_y * ratio if close_y else False
    h2_above = high2 > close_y * ratio if close_y else False

    steps = [
        {'name': '昨日收盘价 (close_y)',      'value': round(close_y, 3), 'unit': '元'},
        {'name': 'High1（120日最高价）',       'value': round(high1, 3),  'unit': '元'},
        {'name': 'High2（120日最高收盘价）',   'value': round(high2, 3),  'unit': '元'},
        {'name': '低位判断倍数 ratio',         'value': ratio,            'unit': ''},
        {'name': f'High1 > 昨收×{ratio} = {round(close_y*ratio,3)}', 'value': '✓' if h1_above else '✗', 'unit': ''},
        {'name': f'High2 > 昨收×{ratio} = {round(close_y*ratio,3)}', 'value': '✓' if h2_above else '✗', 'unit': ''},
        {'name': '位置分类',                   'value': pos,              'unit': ''},
        {'name': '当前实时价格',               'value': round(current_close, 3), 'unit': '元'},
        {'name': '今日涨跌幅',                 'value': today_change,     'unit': '%'},
    ]

    # 一票否决（默认关闭）
    veto_triggered = False
    veto_detail    = {}
    if veto_c.get('enabled', False):
        h_ratio    = veto_c.get('high120_ratio', {}).get('value', 0.95)
        vol_min    = veto_c.get('vol_ratio_min', {}).get('value', 2.0)
        change_max = veto_c.get('change_max',    {}).get('value', 1.0)
        cond1 = high1 > 0 and current_close > high1 * h_ratio
        cond2 = rt_vol_ratio > vol_min
        cond3 = today_change < change_max
        veto_triggered = cond1 and cond2 and cond3
        veto_detail = {
            'high1_ratio_check': f'收盘{current_close:.2f} > High1×{h_ratio}={high1*h_ratio:.2f} → {"✓" if cond1 else "✗"}',
            'vol_ratio_check':   f'量比{rt_vol_ratio:.2f} > {vol_min} → {"✓" if cond2 else "✗"}',
            'change_check':      f'涨幅{today_change:.2f}% < {change_max}% → {"✓" if cond3 else "✗"}',
            'triggered':         veto_triggered,
        }
        steps.append({'name': '一票否决', 'value': '触发' if veto_triggered else '未触发', 'unit': ''})

    score, _ = apply_factor_a(cfg, pre, current_close, rt_vol_ratio, today_change)
    hit_rule  = 'veto' if veto_triggered else pos

    return {
        'score':    score,
        'enabled':  True,
        'is_veto':  veto_triggered,
        'hit_rule': hit_rule,
        'score_def': scores.get(hit_rule if hit_rule in scores else 'score0', {}),
        'steps':    steps,
        'veto':     veto_detail,
    }


def _factor_b_detail(cfg, today_bars, factors) -> dict:
    from instock.core.factor_config import apply_factor_b
    if 'B' not in factors or not cfg.get('B', {}).get('enabled', True):
        return {'score': 0, 'enabled': False, 'skipped': True, 'steps': []}
    if not today_bars:
        return {'score': 0, 'enabled': True, 'skipped': True, 'reason': '无分钟K线数据', 'steps': []}

    th     = cfg.get('B', {}).get('thresholds', {})
    scores = cfg.get('B', {}).get('scores', {})
    strong_th  = th.get('strong',  {}).get('value', 0.62)
    normal_th  = th.get('normal',  {}).get('value', 0.55)
    neutral_th = th.get('neutral', {}).get('value', 0.48)

    total_vol = sum(b.get('volume', 0) for b in today_bars)
    up_vol = 0.0
    for i, b in enumerate(today_bars):
        prev_close = today_bars[i-1]['close'] if i > 0 else b.get('pre_close', b['close'])
        if b.get('close', 0) > prev_close:
            up_vol += b.get('volume', 0)

    S = round(up_vol / total_vol, 4) if total_vol > 0 else 0.0

    if S >= strong_th:   hit_rule = 'strong'
    elif S >= normal_th: hit_rule = 'normal'
    elif S >= neutral_th:hit_rule = 'neutral'
    else:                hit_rule = 'weak'

    score = apply_factor_b(cfg, today_bars)

    steps = [
        {'name': '今日总成交量',               'value': int(total_vol),              'unit': '手'},
        {'name': '涨分钟累计量',               'value': int(up_vol),                 'unit': '手'},
        {'name': '买力强度 S = 涨量/总量',     'value': f'{S:.4f} ({S*100:.1f}%)',   'unit': ''},
        {'name': f'强势阈值 {strong_th}',      'value': '✓ 命中' if S >= strong_th  else '✗', 'unit': ''},
        {'name': f'温和阈值 {normal_th}',      'value': '✓ 命中' if S >= normal_th  else '✗', 'unit': ''},
        {'name': f'均衡阈值 {neutral_th}',     'value': '✓ 命中' if S >= neutral_th else '✗', 'unit': ''},
        {'name': '命中规则',                   'value': hit_rule,                    'unit': ''},
    ]

    return {
        'score':    score,
        'enabled':  True,
        'hit_rule': hit_rule,
        'score_def': scores.get(hit_rule, {}),
        'steps':    steps,
        'buy_strength': S,
    }


def _factor_c_detail(cfg, today_bars, yest_bars, yest_total_vol, hhmm, factors) -> dict:
    from instock.core.factor_config import apply_factor_c
    if 'C' not in factors or not cfg.get('C', {}).get('enabled', True):
        return {'score': 0, 'enabled': False, 'skipped': True, 'steps': []}
    if not today_bars:
        return {'score': 0, 'enabled': True, 'skipped': True, 'reason': '今日无分钟K线', 'steps': []}

    c_cfg  = cfg.get('C', {})
    th     = c_cfg.get('thresholds', {})
    scores = c_cfg.get('scores', {})

    vol_ratio_th    = th.get('vol_ratio_th',    {}).get('value', 1.5)
    high_ratio      = th.get('high_ratio',      {}).get('value', 2.0)
    buy_strength_th = th.get('buy_strength_th', {}).get('value', 0.55)

    today_vol_accum = sum(b.get('volume', 0) for b in today_bars)
    yest_remain_vol = sum(b.get('volume', 0) for b in yest_bars)
    virtual_total   = today_vol_accum + yest_remain_vol
    vol_ratio       = round(virtual_total / yest_total_vol, 4) if yest_total_vol > 0 else 0.0

    # 全程买力强度
    up_vol = 0.0
    for i, b in enumerate(today_bars):
        prev_close = today_bars[i-1]['close'] if i > 0 else b.get('pre_close', b.get('close', 0))
        if b.get('close', 0) > prev_close:
            up_vol += b.get('volume', 0)
    buy_strength = round(up_vol / today_vol_accum, 4) if today_vol_accum > 0 else 0.0

    # 命中规则
    if vol_ratio < vol_ratio_th:
        hit_rule = 'none'
    elif vol_ratio >= high_ratio:
        if buy_strength >= buy_strength_th: hit_rule = 'high_good'
        elif buy_strength < 0.45:           hit_rule = 'high_bad'
        else:                               hit_rule = 'normal'
    else:
        hit_rule = 'normal'

    score = apply_factor_c(cfg, today_bars, yest_bars, yest_total_vol, hhmm)

    steps = [
        {'name': '今日累计量 [09:31..T]',         'value': int(today_vol_accum),              'unit': '手'},
        {'name': '昨日剩余量 [T+1..15:00]',       'value': int(yest_remain_vol),              'unit': '手'},
        {'name': '昨日全天量',                    'value': int(yest_total_vol),               'unit': '手'},
        {'name': '虚拟全日量 = 今日 + 昨日剩余',  'value': int(virtual_total),                'unit': '手'},
        {'name': 'vol_ratio = 虚拟全日 / 昨全天', 'value': vol_ratio,                         'unit': 'x'},
        {'name': '量比阈值',                      'value': vol_ratio_th,                      'unit': ''},
        {'name': '高量比阈值',                    'value': high_ratio,                        'unit': ''},
        {'name': '涨分钟累计量',                  'value': int(up_vol),                       'unit': '手'},
        {'name': '全程买力强度 S = 涨量/总量',    'value': f'{buy_strength:.4f} ({buy_strength*100:.1f}%)', 'unit': ''},
        {'name': f'买力强度阈值 {buy_strength_th}','value': '✓' if buy_strength >= buy_strength_th else '✗', 'unit': ''},
    ]

    return {
        'score':        score,
        'enabled':      True,
        'hit_rule':     hit_rule,
        'score_def':    scores.get(hit_rule, {}),
        'vol_ratio':    vol_ratio,
        'buy_strength': buy_strength,
        'steps':        steps,
    }


def _factor_d_detail(cfg, code, sectors, date, hhmm, factors) -> dict:
    from instock.core.factor_config import apply_factor_d
    if 'D' not in factors or not cfg.get('D', {}).get('enabled', True):
        return {'score': 0, 'enabled': False, 'skipped': True, 'steps': []}

    d_cfg   = cfg.get('D', {})
    th      = d_cfg.get('thresholds', {})
    d_scores= d_cfg.get('scores', {})

    strong_n    = th.get('signal_count_strong', {}).get('value', 3)
    weak_change = th.get('avg_change_weak',     {}).get('value', 1.0)

    my_sectors = sectors.get(code, [])

    # 从 Redis 缓存的排行榜估算 sector_scores
    sector_scores = _get_sector_scores_from_cache(date)

    sector_rows = []
    max_score = 0.0
    hit_rule  = 'none'

    for s in my_sectors:
        info         = sector_scores.get(s, {})
        signal_count = info.get('signal_count', 0)
        avg_change   = info.get('avg_change', 0.0)
        from instock.core.factor_config import _score_item
        if signal_count >= strong_n:
            sc = _score_item(d_scores.get('strong', {'type':'fixed','value':2}),
                             {'signal_count': signal_count, 'avg_change': avg_change})
            if sc > max_score:
                max_score = sc; hit_rule = 'strong'
        elif avg_change > weak_change:
            sc = _score_item(d_scores.get('weak', {'type':'fixed','value':1}),
                             {'signal_count': signal_count, 'avg_change': avg_change})
            if sc > max_score:
                max_score = sc; hit_rule = 'weak'
        sector_rows.append({
            'sector': s,
            'signal_count': signal_count,
            'avg_change':   round(avg_change, 2),
        })

    steps = [
        {'name': '所属板块数量', 'value': len(my_sectors), 'unit': '个'},
        {'name': '强板块阈值（信号股数≥）', 'value': strong_n,    'unit': '只'},
        {'name': '弱板块阈值（均涨幅>）',   'value': weak_change, 'unit': '%'},
    ]

    return {
        'score':       max_score,
        'enabled':     True,
        'hit_rule':    hit_rule,
        'score_def':   d_scores.get(hit_rule, {}),
        'my_sectors':  sector_rows,
        'steps':       steps,
        'cache_source': len(sector_scores) > 0,
    }


def _get_sector_scores_from_cache(date: str) -> dict:
    """从当日排行榜 Redis 缓存反向推算 sector_scores"""
    try:
        r = get_redis()
        cursor = 0
        items  = []
        while True:
            cursor, keys = r.scan(cursor, match=f'volume_rank:{date}:*', count=100)
            for k in keys:
                raw = r.get(k)
                if raw:
                    try:
                        items.extend(json.loads(raw))
                    except Exception:
                        pass
            if cursor == 0:
                break

        if not items:
            return {}

        # 去重（同一code可能在多个cache key里出现）
        seen  = set()
        uniq  = []
        for it in items:
            if it.get('code') not in seen:
                seen.add(it['code'])
                uniq.append(it)

        from collections import defaultdict
        bucket: dict = defaultdict(list)
        for it in uniq:
            score = (it.get('fa', 0) or 0) + (it.get('fb', 0) or 0) + (it.get('fc', 0) or 0)
            for s in it.get('sectors', []):
                bucket[s].append({'change': it.get('today_change', 0), 'signal': score > 2})

        result = {}
        for s, lst in bucket.items():
            result[s] = {
                'signal_count': sum(1 for x in lst if x['signal']),
                'avg_change':   sum(x['change'] for x in lst) / len(lst),
            }
        return result
    except Exception as e:
        log.warning(f"_get_sector_scores_from_cache: {e}")
        return {}

# ── /api/daily_recommend ─────────────────────────────────────────────────
class ApiDailyRecommendHandler(webBase.BaseHandler, ABC):
    """GET /api/daily_recommend
    返回今日推荐 Top 5，融合板块排名(35%)+量能爆发(30%)+位置安全(20%)+持续性(15%)。
    排除涨幅 >8% 的票；市场环境退潮时返回空列表。
    """
    def get(self):
        date     = self.get_argument('date',     '') or _latest_data_date()
        snapshot = self.get_argument('snapshot', '') or _default_mainline_snapshot(date)
        markets  = [m.strip() for m in self.get_argument('market', 'all').split(',') if m.strip()]

        self.set_header('Content-Type', 'application/json; charset=utf-8')
        try:
            max_sector_rank   = 8
            min_sector_strong = 0
            min_ret = -3.0;   max_ret = 35.0
            min_amt_ratio = 0.3; min_amount = 5_000_000

            raw_rows = _calc_mainline_core_rows_from_redis(
                date, snapshot, max_sector_rank, min_sector_strong,
                min_ret, max_ret, min_amt_ratio, 'auto', min_amount,
                include_all=True
            )
            market_env = _mainline_market_environment(raw_rows, markets, snapshot)

            if market_env.get('trade_allowed') is False:
                self.write(json.dumps({
                    'ok': True, 'date': date, 'snapshot': snapshot,
                    'market_env': market_env, 'recommendations': [],
                    'reason': '市场环境退潮，今日不出推荐',
                }, ensure_ascii=False))
                return

            market_fn = _market_code_fn(markets)
            candidates = [
                r for r in raw_rows
                if _passes_mainline_candidate(r, max_sector_rank, min_sector_strong,
                                              min_ret, max_ret, min_amt_ratio, min_amount)
            ]
            if market_fn:
                candidates = [r for r in candidates if market_fn(str(r.get('code') or ''))]
            candidates.sort(key=lambda r: -float(r.get('core_score') or 0))

            top_n = candidates[:50]
            codes = [r['code'] for r in top_n]
            yest_date = _prev_trade_date(date)
            today_bars_map = _load_minute_bars_for_codes(date, codes)
            yest_bars_map  = _load_minute_bars_for_codes(yest_date, codes)

            names         = _stock_names(codes)
            latest_prices = _latest_minute_prices(date, codes)
            daily_metrics = _stock_daily_metrics(codes, date)

            scored = []
            for row in top_n:
                code = row['code']
                ret  = float(row.get('ret_vs_prevclose') or 0)
                if ret > 8:
                    continue

                sector_rank   = int(row.get('sector_rank') or 999)
                sector_score  = max(0, 35 - sector_rank * 2)

                amt_ratio     = float(row.get('amt_vs_prev') or 0)
                vol_score     = min(30, amt_ratio * 12)

                dist_high     = float(row.get('distance_to_30d_high') or 0)
                pos_score     = min(20, max(0, dist_high * 0.8))

                today_bars    = _bars_until(today_bars_map.get(code, []), snapshot)
                yest_bars     = yest_bars_map.get(code, [])
                cont_score    = _recommend_continuity_score(today_bars, yest_bars, snapshot, row)

                total = (sector_score * 0.35 +
                         vol_score   * 0.30 +
                         pos_score   * 0.20 +
                         cont_score  * 0.15)

                name   = names.get(code, code)
                curr   = latest_prices.get(code) or {}
                prev_c = float(row.get('prev_close') or 0)
                curr_p = float(curr.get('current_price') or 0)
                change_pct = ((curr_p / prev_c - 1) * 100) if prev_c > 0 and curr_p > 0 else ret
                dm = daily_metrics.get(code) or {}

                buy_strength = _calc_buy_strength(today_bars)
                up_arrow = '↑' if buy_strength >= 0.58 else ('→' if buy_strength >= 0.48 else '↓')

                scored.append({
                    'code':             code,
                    'name':             name,
                    'recommend_score':  round(total, 1),
                    'prev_d':           row.get('prev_d', ''),
                    'ret_vs_prevclose': round(ret, 2),
                    'current_change':   round(change_pct, 2),
                    'current_price':    curr_p,
                    'sector_rank':      sector_rank,
                    'trade_theme':      row.get('trade_theme') or row.get('best_sector', ''),
                    'amt_vs_prev':      round(amt_ratio, 2),
                    'buy_strength':     round(buy_strength, 3),
                    'buy_trend':        up_arrow,
                    'distance_30d':     round(dist_high, 1),
                    'pos_in_range':     round(float(row.get('pos_in_range') or 0), 1),
                    'turnover':         round(float(dm.get('turnover') or 0), 2),
                    'continuity_check': cont_score >= 8,
                    'sector_score':     round(sector_score, 1),
                    'vol_score':        round(vol_score, 1),
                    'pos_score':        round(pos_score, 1),
                    'cont_score':       round(cont_score, 1),
                    'risk_tags':        row.get('risk_tags', ''),
                })

            scored.sort(key=lambda x: -x['recommend_score'])
            result = scored[:5]

            self.write(json.dumps({
                'ok': True,
                'date': date,
                'snapshot': snapshot,
                'market_env': market_env,
                'list_mode': _mainline_list_mode(snapshot),
                'candidate_count': len(candidates),
                'recommendations': result,
            }, ensure_ascii=False))

        except Exception as e:
            log.exception('daily_recommend error')
            self.write(json.dumps({'ok': False, 'error': str(e)}, ensure_ascii=False))


def _calc_buy_strength(today_bars: list[dict]) -> float:
    if not today_bars:
        return 0.0
    total_vol = sum(b.get('volume', 0) for b in today_bars)
    if total_vol <= 0:
        return 0.0
    up_vol = 0.0
    for i, b in enumerate(today_bars):
        prev_close = today_bars[i-1]['close'] if i > 0 else b.get('pre_close', b['close'])
        if b.get('close', 0) > prev_close:
            up_vol += b.get('volume', 0)
    return up_vol / total_vol


def _recommend_continuity_score(today_bars: list[dict], yest_bars: list[dict],
                                snapshot: str, row: dict) -> float:
    score = 0.0

    strong = int(row.get('sector_strong_count') or 0)
    if strong >= 5:       score += 5
    elif strong >= 3:     score += 3
    elif strong >= 2:     score += 1

    today_S = _calc_buy_strength(today_bars)
    yest_bars_same = _bars_until(yest_bars, snapshot)
    yest_S = _calc_buy_strength(yest_bars_same)
    if yest_S > 0:
        if today_S > yest_S * 1.1:       score += 5
        elif today_S > yest_S:            score += 3
        elif today_S > yest_S * 0.9:      score += 1
    else:
        score += 2

    open_0930   = float(row.get('open_0930') or 0)
    prev_close  = float(row.get('prev_close') or 0)
    curr_price  = float(row.get('buy_price') or 0)
    if open_0930 > 0 and prev_close > 0:
        if open_0930 > prev_close and curr_price > open_0930:
            score += 5
        elif open_0930 > prev_close and curr_price > prev_close:
            score += 3
        elif open_0930 < prev_close and curr_price > prev_close:
            score += 2
        elif curr_price > prev_close:
            score += 1

    return score
