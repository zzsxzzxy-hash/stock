#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
量能异动多因子评分引擎
每分钟（09:35起）对全市场计算评分，缓存结果供前端查询

评分规则（满分10分）：
  A 位置因子 (0/3/4/-99)
  B 效率因子 (0/1/2)
  C 量能因子 (-1/0/1/2)，09:30-10:00期间返回 None（积累中）
  D 板块因子 (0/1/2)
  触发一票否决 → -99

Redis Key:
  volume_rank:{date}        → JSON 排行榜（前15名，30秒有效）
  volume_rank_full:{date}   → JSON 全量评分（60秒有效）
"""
import json
import logging
import datetime
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from instock.core.minute_bar_collector import (
    get_redis, get_day_bars_until, get_day_bars_from, get_all_codes_for_date
)
from instock.core.volume_pre_calc import get_pre_calc, get_sectors
from instock.core.factor_config import (
    load_config,
    apply_factor_a, apply_factor_b, apply_factor_c, apply_factor_d,
)

log = logging.getLogger(__name__)

RANK_TOP_N    = 50
VOL_RATIO_TH  = float(os.environ.get('VOL_RATIO_TH', '1.5'))   # 量比阈值，默认1.5

# ── 辅助：交易时间工具 ────────────────────────────────────────────────────

def _trading_minutes() -> list[str]:
    """返回A股全天240分钟时间列表 09:31~11:30, 13:01~15:00"""
    mins = []
    for h in range(9, 12):
        for m in range(60):
            t = f'{h:02d}:{m:02d}'
            if '09:31' <= t <= '11:30':
                mins.append(t)
    for h in range(13, 16):
        for m in range(60):
            t = f'{h:02d}:{m:02d}'
            if '13:01' <= t <= '15:00':
                mins.append(t)
    return mins


_ALL_TRADE_MINS = _trading_minutes()


def _prev_trade_date(date: str) -> str:
    """
    从 Redis scan 找前一交易日（只看有分钟K线的日期）。
    回退：若 Redis 无数据则用工作日推算。
    """
    try:
        r = get_redis()
        # 扫描所有 minute_bar:YYYY-MM-DD:* 的日期
        dates_in_redis = set()
        cursor = 0
        while True:
            cursor, keys = r.scan(cursor, match='minute_bar:*', count=1000)
            for k in keys:
                parts = k.split(':')
                if len(parts) >= 2:
                    dates_in_redis.add(parts[1])
            if cursor == 0:
                break
        prev_dates = sorted([d for d in dates_in_redis if d < date], reverse=True)
        if prev_dates:
            return prev_dates[0]
    except Exception:
        pass
    # 回退：跳过周末
    d = datetime.date.fromisoformat(date)
    for delta in range(1, 8):
        prev = d - datetime.timedelta(days=delta)
        if prev.weekday() < 5:
            return prev.strftime('%Y-%m-%d')
    return (datetime.date.fromisoformat(date) - datetime.timedelta(days=1)).strftime('%Y-%m-%d')


def _last_n_trade_dates(date: str, n: int) -> list[str]:
    """
    从 Redis scan 取前 n 个有分钟数据的交易日。
    回退：工作日推算。
    """
    try:
        r = get_redis()
        dates_in_redis = set()
        cursor = 0
        while True:
            cursor, keys = r.scan(cursor, match='minute_bar:*', count=1000)
            for k in keys:
                parts = k.split(':')
                if len(parts) >= 2:
                    dates_in_redis.add(parts[1])
            if cursor == 0:
                break
        prev_dates = sorted([d for d in dates_in_redis if d < date], reverse=True)
        if prev_dates:
            return prev_dates[:n]
    except Exception:
        pass
    # 回退
    dates = []
    d = datetime.date.fromisoformat(date)
    delta = 1
    while len(dates) < n:
        prev = d - datetime.timedelta(days=delta)
        if prev.weekday() < 5:
            dates.append(prev.strftime('%Y-%m-%d'))
        delta += 1
    return dates


# ── 因子A：位置因子 ───────────────────────────────────────────────────────

def factor_a(pre: dict, current_close: float) -> int:
    """
    pre: 预计算结果 {position, ma120, high120, ...}
    current_close: 当前价格（分钟收盘）
    """
    pos = pre.get('position', 'other')
    high120 = pre.get('high120', 0)

    # 一票否决：高位巨量滞涨（由调用方结合量比判断，此处仅判断价格位置）
    if pos == 'break' and high120 > 0 and current_close > high120 * 0.95:
        # 是否触发否决由评分主函数结合量比判断
        return 3
    if pos == 'low':
        return 4
    if pos == 'break':
        return 3
    return 0


# ── 因子B：效率因子 ───────────────────────────────────────────────────────

def factor_b(pre: dict, today_change: float, today_turnover: float) -> int:
    """
    E = 涨跌幅 / 换手率
    比较今日效率 vs 昨日效率 vs 前日效率
    """
    def _eff(change, turnover):
        if not turnover or turnover == 0:
            return 0.0
        return change / turnover

    e_today = _eff(today_change,          today_turnover)
    e_y     = _eff(pre.get('change_y', 0),     pre.get('turnover_y', 0))
    e_prev  = _eff(pre.get('change_prev', 0),  pre.get('turnover_prev', 0))

    if e_today > e_y and e_y > e_prev:
        return 2   # 连续2日递增
    if e_today > e_y:
        return 1   # 单日递增
    return 0


# ── 因子C：量能因子 ───────────────────────────────────────────────────────

def factor_c(today_bars: list[dict], yesterday_bars: list[dict],
             yesterday_total_vol: float, current_time: str) -> int:
    """
    today_bars: 今日到当前时刻的分钟K线列表
    yesterday_bars: 昨日到同一时刻的分钟K线列表
    yesterday_total_vol: 昨日全天成交量
    current_time: 当前分钟 HH:MM
    """
    today_vol_accum = sum(b['volume'] for b in today_bars if b.get('volume'))
    yest_vol_accum  = sum(b['volume'] for b in yesterday_bars if b.get('volume'))

    # 虚拟全日量 = 今日累计量 + 昨日同期累计量
    virtual_total = today_vol_accum + yest_vol_accum

    if yesterday_total_vol <= 0:
        return 0

    vol_ratio = virtual_total / yesterday_total_vol

    if vol_ratio < VOL_RATIO_TH:
        return 0

    # 判断拉升质量
    quality = _judge_pull_quality(today_bars)

    if vol_ratio >= 2.0 and quality == 'good':
        return 2
    if vol_ratio >= 1.5 and quality == 'bad':
        return -1
    return 1


def _judge_pull_quality(bars: list[dict]) -> str:
    """
    3分钟窗口判断拉升质量
    良性(good): 最近拉升段均量 > 回调段均量 × 1.5
    可疑(bad):  最近拉升段均量 < 回调段均量
    neutral:    无法判断
    """
    if len(bars) < 6:
        return 'neutral'

    # 识别最近的连续3分钟拉升段和回调段
    up_vols, down_vols = [], []
    i = len(bars) - 1
    # 找最近一个拉升段（连续3根close递增）
    while i >= 2:
        b0, b1, b2 = bars[i-2], bars[i-1], bars[i]
        c0 = b0.get('close', 0)
        c1 = b1.get('close', 0)
        c2 = b2.get('close', 0)
        if c0 < c1 < c2 and c0 > 0:
            up_vols = [b0.get('volume', 0), b1.get('volume', 0), b2.get('volume', 0)]
            break
        i -= 1

    # 找上述拉升段之前的回调段
    j = i - 3 if i >= 3 else 0
    while j >= 2:
        b0, b1, b2 = bars[j-2], bars[j-1], bars[j]
        c0 = b0.get('close', 0)
        c1 = b1.get('close', 0)
        c2 = b2.get('close', 0)
        if c0 > c1 > c2 and c0 > 0:
            down_vols = [b0.get('volume', 0), b1.get('volume', 0), b2.get('volume', 0)]
            break
        j -= 1

    if not up_vols or not down_vols:
        return 'neutral'

    avg_up   = sum(up_vols)   / len(up_vols)
    avg_down = sum(down_vols) / len(down_vols)

    if avg_down == 0:
        return 'neutral'
    if avg_up > avg_down * 1.5:
        return 'good'
    if avg_up < avg_down:
        return 'bad'
    return 'neutral'


# ── 因子D：板块因子 ───────────────────────────────────────────────────────

def factor_d(code: str, sectors: dict, sector_scores: dict) -> int:
    """
    sector_scores: {sector: {"signal_count": n, "avg_change": x}}
    由评分主函数预先计算
    """
    my_sectors = sectors.get(code, [])
    if not my_sectors:
        return 0

    max_score = 0
    for s in my_sectors:
        info = sector_scores.get(s, {})
        if info.get('signal_count', 0) >= 3:
            max_score = max(max_score, 2)
        elif info.get('avg_change', 0) > 1.0:
            max_score = max(max_score, 1)

    return max_score


# ── 涨速计算 ──────────────────────────────────────────────────────────────

def calc_price_slope(bars: list[dict], window: int = 3) -> float:
    """近window分钟价格斜率（简单线性，单位：%/分钟）"""
    if len(bars) < window:
        return 0.0
    recent = bars[-window:]
    closes = [b.get('close', 0) for b in recent]
    if closes[0] <= 0:
        return 0.0
    return round((closes[-1] - closes[0]) / closes[0] * 100, 3)


# ── 实时量比 ──────────────────────────────────────────────────────────────

def calc_realtime_vol_ratio(today_bars: list[dict], yesterday_bars: list[dict]) -> float:
    """实时量比 = 今日累计量 / 昨日同期累计量"""
    today_vol = sum(b.get('volume', 0) for b in today_bars)
    yest_vol  = sum(b.get('volume', 0) for b in yesterday_bars)
    if yest_vol <= 0:
        return 0.0
    return round(today_vol / yest_vol, 2)


def calc_minute_vol_ratio(code: str, current_time: str, date: str,
                          current_vol: float) -> float:
    """
    分钟级量比 = 当前分钟量 / 过去5个交易日同一分钟均量
    从Redis取历史数据
    """
    past_dates = _last_n_trade_dates(date, 5)
    vols = []
    for pd_ in past_dates:
        bars = get_day_bars_until(pd_, code, current_time)
        bar = next((b for b in reversed(bars) if b['time'] == current_time), None)
        if bar and bar.get('volume', 0) > 0:
            vols.append(bar['volume'])
    if not vols:
        return 0.0
    avg = sum(vols) / len(vols)
    if avg <= 0:
        return 0.0
    return round(current_vol / avg, 2)


# ── 量能标签生成 ──────────────────────────────────────────────────────────

def gen_volume_label(virtual_vol_ratio: float, current_time: str,
                     today_change: float) -> dict:
    """
    生成量能标签文本和类型
    返回: {text: str, type: "red"/"purple"/"gray"/"normal"}
    """
    ratio = virtual_vol_ratio
    if ratio <= 0:
        return {'text': '无量', 'type': 'gray'}

    # 判断阶段
    if current_time <= '10:30':
        phase = '开盘'
    elif current_time <= '14:00':
        phase = '盘中'
    else:
        phase = '尾盘'

    if ratio >= 4.0 and today_change > 0:
        return {'text': f'{phase}同比放量{ratio:.1f}x', 'type': 'purple'}
    if ratio >= 2.0 and today_change > 0:
        return {'text': f'{phase}同比放量{ratio:.1f}x', 'type': 'red'}
    if ratio >= 1.5:
        return {'text': f'{phase}同比放量{ratio:.1f}x', 'type': 'red'}
    if today_change > 0 and ratio < 1.0:
        return {'text': '缩量反弹', 'type': 'gray'}
    return {'text': f'放量{ratio:.1f}x', 'type': 'normal'}


# ── 主评分函数 ────────────────────────────────────────────────────────────

def score_all(date: str, current_time: str,
              position_filter: str = 'all',
              vol_ratio_threshold: float = None,
              market_codes: set = None,
              market_filter_fn=None) -> list[dict]:
    """
    对全市场当前有分钟数据的股票计算评分
    返回排序后的列表（最多 RANK_TOP_N 条）

    position_filter:  'low' / 'break' / 'all'
    vol_ratio_threshold: 量比阈值
    market_filter_fn: callable(code)->bool，前置市场过滤函数
    """
    if vol_ratio_threshold is None:
        vol_ratio_threshold = VOL_RATIO_TH

    # 每次评分加载最新因子配置（Redis缓存，毫秒级）
    cfg = load_config()
    c_th = cfg.get('C', {}).get('thresholds', {})
    cfg_vol_ratio_th = c_th.get('vol_ratio_th', {}).get('value', vol_ratio_threshold)

    pre_data  = get_pre_calc(date)
    sectors   = get_sectors()
    yest_date = _prev_trade_date(date)

    if not pre_data:
        log.warning(f"预计算数据不存在 date={date}，尝试临时计算")
        from instock.core.volume_pre_calc import run_pre_calc
        run_pre_calc(date)
        pre_data = get_pre_calc(date)
        if not pre_data:
            return []

    # 获取今日有数据的股票，可选前置市场过滤
    codes = get_all_codes_for_date(date)
    if market_filter_fn is not None:
        codes = [c for c in codes if market_filter_fn(c)]
        log.info(f"市场过滤后股票数: {len(codes)}")
    if not codes:
        log.debug(f"今日 {date} 尚无分钟数据")
        return []

    # ── 第一轮：计算基础指标 ───────────────────────────────────────────
    first_pass = []
    for code in codes:
        pre = pre_data.get(code)
        if not pre:
            continue

        # 位置过滤
        pos = pre.get('position', 'other')
        if position_filter == 'low'   and pos != 'low':
            continue
        if position_filter == 'break' and pos != 'break':
            continue

        today_bars = get_day_bars_until(date, code, current_time)
        if not today_bars:
            continue

        # 昨日同期（用于实时量比）
        yest_bars      = get_day_bars_until(yest_date, code, current_time)
        # 昨日剩余（current_time 之后到收盘，用于虚拟全日量）
        yest_remain    = get_day_bars_from(yest_date, code, current_time)
        yest_all       = get_day_bars_until(yest_date, code, '15:00')
        yest_total_vol = sum(b.get('volume', 0) for b in yest_all)

        # 当前价格和涨跌幅
        last_bar     = today_bars[-1]
        current_close = last_bar.get('close', 0)
        pre_close_y  = pre.get('close_y', 0)
        today_change = round((current_close - pre_close_y) / pre_close_y * 100, 2) \
            if pre_close_y > 0 else 0.0

        # 今日累计换手（无法盘中实时获取，用虚拟全日量估算，此处暂用昨日换手）
        today_turnover = pre.get('turnover_y', 0)   # TODO: 接入实时换手后替换

        # 实时量比
        rt_vol_ratio = calc_realtime_vol_ratio(today_bars, yest_bars)

        # ── 因子A（含一票否决）──
        fa, is_veto = apply_factor_a(cfg, pre, current_close, rt_vol_ratio, today_change)
        if is_veto:
            continue

        # ── 因子C 量能计算 + 过滤 ──
        # apply_factor_c 第二个参数传昨日剩余bars（current_time之后到收盘）
        fc_raw = apply_factor_c(cfg, today_bars, yest_remain, yest_total_vol, current_time)
        if fc_raw == 0 and rt_vol_ratio < cfg_vol_ratio_th:
            continue

        # 虚拟全日量比（今日累计 + 昨日剩余）
        today_vol_accum  = sum(b.get('volume', 0) for b in today_bars)
        yest_remain_vol  = sum(b.get('volume', 0) for b in yest_remain)
        virtual_total    = today_vol_accum + yest_remain_vol
        virt_ratio = round(virtual_total / yest_total_vol, 2) if yest_total_vol > 0 else 0.0

        # ── 因子B：买力强度（今日实时分钟数据）──
        fb = apply_factor_b(cfg, today_bars)
        price_slope = calc_price_slope(today_bars)
        min_vol_ratio = 0.0
        vol_label = gen_volume_label(virt_ratio, current_time, today_change)

        # 效率方向
        e_today = today_change / today_turnover if today_turnover else 0
        e_y     = pre.get('change_y', 0) / pre.get('turnover_y', 1) if pre.get('turnover_y') else 0
        if e_today > e_y:
            eff_dir = 'up'
        elif e_today < e_y:
            eff_dir = 'down'
        else:
            eff_dir = 'flat'

        # 今日各分钟量（供迷你图）
        today_minute_vols = [{'time': b['time'], 'vol': b.get('volume', 0)} for b in today_bars]
        yest_minute_vols  = [{'time': b['time'], 'vol': b.get('volume', 0)} for b in yest_bars]

        first_pass.append({
            'code':          code,
            'name':          '',   # 后面批量补充
            'sectors':       sectors.get(code, []),
            'position':      pos,
            'current_price': round(current_close, 2),
            'today_change':  today_change,
            'rt_vol_ratio':  rt_vol_ratio,
            'min_vol_ratio': min_vol_ratio,
            'virt_ratio':    virt_ratio,
            'vol_label':     vol_label,
            'price_slope':   price_slope,
            'eff_dir':       eff_dir,
            'fa':            fa,
            'fb':            fb,
            'fc':            fc_raw,   # None = 积累中
            'today_minute_vols': today_minute_vols,
            'yest_minute_vols':  yest_minute_vols,
            # 预计算数据备用
            '_pre':          pre,
        })

    if not first_pass:
        return []

    # ── 第二轮：计算板块因子（需要先知道所有股票的初步得分）─────────────
    # 统计每个板块的异动股数量和平均涨幅
    sector_stocks: dict[str, list] = defaultdict(list)
    for item in first_pass:
        score_preliminary = item['fa'] + item['fb'] + (item['fc'] or 0)
        for s in item['sectors']:
            sector_stocks[s].append({
                'change': item['today_change'],
                'signal': score_preliminary > 2,
            })

    sector_scores: dict[str, dict] = {}
    for s, stocks in sector_stocks.items():
        signal_count = sum(1 for st in stocks if st['signal'])
        avg_change   = sum(st['change'] for st in stocks) / len(stocks) if stocks else 0
        sector_scores[s] = {'signal_count': signal_count, 'avg_change': avg_change}

    # ── 第三轮：汇总总分 ──────────────────────────────────────────────
    scored = []
    for item in first_pass:
        fd = apply_factor_d(cfg, item['code'], sectors, sector_scores)
        fa, fb, fc = item['fa'], item['fb'], item['fc']
        total = fa + fb + (fc or 0) + fd
        item['fd']    = fd
        item['score'] = round(total, 1)
        item['fc_display'] = fc
        del item['_pre']
        scored.append(item)

    # ── 排序：总分 DESC，实时量比 DESC ────────────────────────────────
    scored.sort(key=lambda x: (-x['score'], -x['rt_vol_ratio']))

    return scored[:RANK_TOP_N]


# ── 补充股票名称 ──────────────────────────────────────────────────────────

def enrich_names(items: list[dict]) -> list[dict]:
    """批量从数据库补充股票名称"""
    if not items:
        return items
    codes = [i['code'] for i in items]
    ph = ','.join(['%s'] * len(codes))
    rows = mdb.executeSqlFetch(
        f'SELECT DISTINCT ON (code) code, name FROM cn_stock_spot WHERE code IN ({ph}) ORDER BY code, date DESC',
        tuple(codes)
    )
    name_map = {r[0]: r[1] for r in rows} if rows else {}
    for i in items:
        i['name'] = name_map.get(i['code'], i['code'])
    return items


import instock.lib.database as mdb


# ── 缓存与定时刷新 ─────────────────────────────────────────────────────────

CACHE_TTL = 35   # 秒

def refresh_rank_cache(date: str, current_time: str,
                       position_filter: str = 'all',
                       vol_ratio_threshold: float = None,
                       market: str = 'all',
                       market_filter_fn=None,
                       change_filter: str = 'all'):
    """计算并写入缓存"""
    items = score_all(date, current_time, position_filter, vol_ratio_threshold,
                      market_filter_fn=market_filter_fn)
    # 涨跌过滤
    if change_filter == 'up':
        items = [i for i in items if i.get('today_change', 0) > 0]
    elif change_filter == 'down':
        items = [i for i in items if i.get('today_change', 0) < 0]
    items = enrich_names(items)
    r = get_redis()
    cache_key = f'volume_rank:{date}:{position_filter}:{vol_ratio_threshold or VOL_RATIO_TH}:{market}:{change_filter}'
    r.set(cache_key, json.dumps(items, ensure_ascii=False, default=str), ex=CACHE_TTL)
    log.info(f"排行榜缓存刷新: {len(items)} 条 key={cache_key}")
    return items


def get_cached_rank(date: str, position_filter: str = 'all',
                    vol_ratio_threshold: float = None,
                    market: str = 'all',
                    change_filter: str = 'all') -> list[dict]:
    r = get_redis()
    cache_key = f'volume_rank:{date}:{position_filter}:{vol_ratio_threshold or VOL_RATIO_TH}:{market}:{change_filter}'
    raw = r.get(cache_key)
    if raw:
        return json.loads(raw)
    return []
    log.info(f"排行榜缓存刷新: {len(items)} 条 key={cache_key}")
    return items
