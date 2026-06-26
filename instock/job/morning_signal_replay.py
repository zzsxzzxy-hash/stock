#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
早盘超短信号离线回放。

默认用指定快照时间（如 09:42）之前已经发生的分钟 K 线计算候选票，
再统计当天收盘、次日开盘、次日 10 点前的结果。
"""

import argparse
import datetime as dt
import os
import sys
from dataclasses import dataclass
from decimal import Decimal

cpath = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, cpath)

import instock.lib.database as mdb


@dataclass
class Thresholds:
    min_score: float = 72.0
    min_ret: float = 1.5
    max_ret: float = 8.5
    min_pos: float = 75.0
    max_pullback: float = 1.8
    min_efficiency: float = 1.0


def _f(v, default=0.0) -> float:
    if v is None:
        return default
    if isinstance(v, Decimal):
        return float(v)
    return float(v)


def _d(v) -> str:
    if hasattr(v, 'strftime'):
        return v.strftime('%Y-%m-%d')
    return str(v)


def score_row(row: dict) -> tuple[float, str, list[str]]:
    ret = _f(row['ret_vs_prevclose'])
    pos = _f(row['pos_in_range'])
    pullback = _f(row['pullback'])
    amt_ratio = _f(row['amt_vs_prev'])
    eff = _f(row['push_efficiency'])
    dist_high = _f(row['distance_to_30d_high'])
    break_high = _f(row['break_30d_high'])
    ma_conv = _f(row['ma_converge_pct'], 99.0)
    ma_bull = bool(row['ma_bull'])
    pressure = int(row['pressure_count'] or 0)

    score = 0.0
    tags: list[str] = []
    risks: list[str] = []

    if 2 <= ret <= 6:
        score += 15; tags.append('涨幅适中')
    elif 1.5 <= ret < 2 or 6 < ret <= 8.5:
        score += 9; tags.append('涨幅可接受')
    elif ret > 8.5:
        score += 3; risks.append('涨幅偏高')

    if pos >= 88:
        score += 22; tags.append('接近早盘高位')
    elif pos >= 80:
        score += 18; tags.append('高位承接')
    elif pos >= 75:
        score += 12; tags.append('承接尚可')

    if pullback <= 0.8:
        score += 16; tags.append('回撤很小')
    elif pullback <= 1.2:
        score += 12; tags.append('回撤可控')
    elif pullback <= 1.8:
        score += 6; risks.append('回撤偏大')
    else:
        risks.append('冲高回落')

    if 0.7 <= amt_ratio <= 2.2:
        score += 12; tags.append('量能健康')
    elif 2.2 < amt_ratio <= 3.5:
        score += 7; tags.append('明显放量')
    elif 0.45 <= amt_ratio < 0.7:
        score += 5; tags.append('量能温和')
    elif amt_ratio > 3.5:
        score += 2; risks.append('巨量')

    if eff >= 3:
        score += 18; tags.append('推进效率高')
    elif eff >= 2:
        score += 14; tags.append('推进效率较高')
    elif eff >= 1:
        score += 8
    else:
        risks.append('推进效率低')

    if dist_high >= 8:
        score += 12; tags.append('上方空间足')
    elif dist_high >= 3:
        score += 8; tags.append('仍有空间')
    elif break_high >= 0 and break_high <= 5:
        score += 7; tags.append('突破近期高点')
    elif break_high > 5:
        score += 3; risks.append('突破过远')
    else:
        risks.append('临近压力')

    if ma_bull and ma_conv <= 8:
        score += 8; tags.append('均线聚合偏多')
    elif ma_conv <= 10:
        score += 4; tags.append('均线聚合')

    if pressure > 0:
        penalty = min(12, pressure * 6)
        score -= penalty
        risks.append(f'前高套牢{pressure}')

    if row['code'].startswith('920'):
        # 北交所票可以做，但默认要求更高的承接质量。
        if pos < 85 or pullback > 1.2:
            score -= 6
            risks.append('北交所承接要求更高')

    if eff >= 3 and dist_high >= 5 and pullback <= 1.2:
        signal_type = '轻筹码启动'
    elif break_high >= 0 and pullback <= 1.2:
        signal_type = '突破加速'
    elif 0.7 <= amt_ratio <= 2.2 and pos >= 80:
        signal_type = '稳步承接'
    else:
        signal_type = '早盘异动'

    return round(score, 2), signal_type, tags + risks


def fetch_rows(start: str, end: str, snapshot: str) -> list[dict]:
    sql = """
    WITH dates AS (
        SELECT DISTINCT date
        FROM cn_stock_minute_bar
        WHERE date BETWEEN %s AND %s
    ),
    ctx AS (
        SELECT d.date,
               c.code,
               (SELECT max(date)
                FROM cn_stock_minute_bar p
                WHERE p.code = c.code AND p.date < d.date) AS prev_d
        FROM dates d
        JOIN (
            SELECT DISTINCT code FROM cn_stock_minute_bar
        ) c ON true
    ),
    now_window AS (
        SELECT ctx.date, ctx.code, ctx.prev_d,
               (array_agg(m.open ORDER BY m.time))[1] AS open_0930,
               (array_agg(m.close ORDER BY m.time DESC))[1] AS buy_price,
               max(m.high) AS high_so_far,
               min(m.low) AS low_so_far,
               sum(m.amount) AS amount_so_far,
               sum(m.volume) AS volume_so_far,
               count(*) FILTER (WHERE m.close >= m.open) AS green_bars,
               count(*) AS bars,
               max(m.time) FILTER (
                   WHERE m.high = (
                       SELECT max(mx.high)
                       FROM cn_stock_minute_bar mx
                       WHERE mx.code = ctx.code
                         AND mx.date = ctx.date
                         AND mx.time BETWEEN '09:30' AND %s
                   )
               ) AS high_time
        FROM ctx
        JOIN cn_stock_minute_bar m
          ON m.code = ctx.code AND m.date = ctx.date
        WHERE m.time BETWEEN '09:30' AND %s
        GROUP BY ctx.date, ctx.code, ctx.prev_d
    ),
    prev_window AS (
        SELECT ctx.date, ctx.code,
               sum(m.amount) AS prev_amount_so_far,
               sum(m.volume) AS prev_volume_so_far
        FROM ctx
        JOIN cn_stock_minute_bar m
          ON m.code = ctx.code AND m.date = ctx.prev_d
        WHERE m.time BETWEEN '09:30' AND %s
        GROUP BY ctx.date, ctx.code
    ),
    after_day AS (
        SELECT n.date, n.code,
               max(m.high) AS day_high_after_buy,
               min(m.low) AS day_low_after_buy,
               (array_agg(m.close ORDER BY m.time DESC))[1] AS day_close,
               max(m.time) AS day_last_time
        FROM now_window n
        JOIN cn_stock_minute_bar m
          ON m.code = n.code AND m.date = n.date
        WHERE m.time > %s
        GROUP BY n.date, n.code
    ),
    next_ctx AS (
        SELECT n.date, n.code,
               (SELECT min(date)
                FROM cn_stock_minute_bar nm
                WHERE nm.code = n.code AND nm.date > n.date) AS next_d
        FROM now_window n
    ),
    next_morning AS (
        SELECT nc.date, nc.code, nc.next_d,
               (array_agg(m.open ORDER BY m.time))[1] AS next_open,
               (array_agg(m.close ORDER BY m.time DESC))[1] AS next_1000_close,
               max(m.high) AS next_1000_high,
               min(m.low) AS next_1000_low
        FROM next_ctx nc
        LEFT JOIN cn_stock_minute_bar m
          ON m.code = nc.code AND m.date = nc.next_d
         AND m.time BETWEEN '09:30' AND '10:00'
        GROUP BY nc.date, nc.code, nc.next_d
    ),
    hist AS (
        SELECT n.date, n.code,
               hp.close AS prev_close,
               hm.ma5, hm.ma10, hm.ma15,
               hm.high_10d, hm.high_30d,
               hm.avg_amount_5d,
               COALESCE(pr.pressure_count, 0) AS pressure_count
        FROM now_window n
        JOIN cn_stock_hist_data hp
          ON hp.code = n.code AND hp.date = n.prev_d
        LEFT JOIN LATERAL (
            SELECT
                avg(close) FILTER (WHERE rn <= 5) AS ma5,
                avg(close) FILTER (WHERE rn <= 10) AS ma10,
                avg(close) FILTER (WHERE rn <= 15) AS ma15,
                max(high) FILTER (WHERE rn <= 10) AS high_10d,
                max(high) FILTER (WHERE rn <= 30) AS high_30d,
                avg(amount) FILTER (WHERE rn <= 5) AS avg_amount_5d
            FROM (
                SELECT h.close, h.high, h.amount,
                       row_number() OVER (ORDER BY h.date DESC) AS rn
                FROM cn_stock_hist_data h
                WHERE h.code = n.code AND h.date < n.date
                ORDER BY h.date DESC
                LIMIT 30
            ) t
        ) hm ON true
        LEFT JOIN LATERAL (
            SELECT count(*) AS pressure_count
            FROM cn_stock_hist_data h
            WHERE h.code = n.code
              AND h.date < n.date
              AND h.date >= n.date - interval '30 days'
              AND h.high BETWEEN n.buy_price * 0.98 AND n.buy_price * 1.02
              AND (h.high - h.close) / NULLIF(h.high, 0) >= 0.05
        ) pr ON true
    )
    SELECT n.date, n.code, n.prev_d, n.open_0930, n.buy_price,
           n.high_so_far, n.low_so_far, n.amount_so_far, n.volume_so_far,
           n.green_bars, n.bars, n.high_time,
           p.prev_amount_so_far, p.prev_volume_so_far,
           h.prev_close, h.ma5, h.ma10, h.ma15, h.high_10d, h.high_30d,
           h.avg_amount_5d, h.pressure_count,
           a.day_high_after_buy, a.day_low_after_buy, a.day_close, a.day_last_time,
           nm.next_d, nm.next_open, nm.next_1000_close,
           nm.next_1000_high, nm.next_1000_low,
           ((n.buy_price / NULLIF(h.prev_close, 0) - 1) * 100) AS ret_vs_prevclose,
           ((n.buy_price / NULLIF(n.open_0930, 0) - 1) * 100) AS ret_from_open,
           ((n.buy_price - n.low_so_far) / NULLIF(n.high_so_far - n.low_so_far, 0) * 100) AS pos_in_range,
           ((n.high_so_far - n.buy_price) / NULLIF(n.high_so_far, 0) * 100) AS pullback,
           (n.amount_so_far / NULLIF(p.prev_amount_so_far, 0)) AS amt_vs_prev,
           (n.volume_so_far / NULLIF(p.prev_volume_so_far, 0)) AS vol_vs_prev,
           (((n.buy_price / NULLIF(h.prev_close, 0) - 1) * 100)
             / NULLIF(n.amount_so_far / 100000000, 0)) AS push_efficiency,
           ((h.high_30d / NULLIF(n.buy_price, 0) - 1) * 100) AS distance_to_30d_high,
           ((n.buy_price / NULLIF(h.high_30d, 0) - 1) * 100) AS break_30d_high,
           ((GREATEST(h.ma5, h.ma10, h.ma15) - LEAST(h.ma5, h.ma10, h.ma15))
             / NULLIF(n.buy_price, 0) * 100) AS ma_converge_pct,
           (h.ma5 >= h.ma10 AND h.ma10 >= h.ma15) AS ma_bull,
           ((a.day_high_after_buy / NULLIF(n.buy_price, 0) - 1) * 100) AS day_max_up_pct,
           ((a.day_low_after_buy / NULLIF(n.buy_price, 0) - 1) * 100) AS day_max_down_pct,
           ((a.day_close / NULLIF(n.buy_price, 0) - 1) * 100) AS day_close_return_pct,
           ((nm.next_open / NULLIF(n.buy_price, 0) - 1) * 100) AS next_open_return_pct,
           ((nm.next_1000_close / NULLIF(n.buy_price, 0) - 1) * 100) AS next_1000_return_pct,
           ((nm.next_1000_high / NULLIF(n.buy_price, 0) - 1) * 100) AS next_1000_max_up_pct,
           ((nm.next_1000_low / NULLIF(n.buy_price, 0) - 1) * 100) AS next_1000_max_down_pct
    FROM now_window n
    JOIN prev_window p ON p.date = n.date AND p.code = n.code
    JOIN hist h ON h.date = n.date AND h.code = n.code
    LEFT JOIN after_day a ON a.date = n.date AND a.code = n.code
    LEFT JOIN next_morning nm ON nm.date = n.date AND nm.code = n.code
    WHERE n.buy_price IS NOT NULL
      AND h.prev_close IS NOT NULL
      AND p.prev_amount_so_far > 0
      AND n.bars >= 5
    ORDER BY n.date, n.code
    """

    with mdb.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (start, end, snapshot, snapshot, snapshot, snapshot))
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]


def is_candidate(row: dict, th: Thresholds) -> bool:
    score = row['score']
    ret = _f(row['ret_vs_prevclose'])
    pos = _f(row['pos_in_range'])
    pullback = _f(row['pullback'])
    eff = _f(row['push_efficiency'])
    return (
        score >= th.min_score and
        th.min_ret <= ret <= th.max_ret and
        pos >= th.min_pos and
        pullback <= th.max_pullback and
        eff >= th.min_efficiency
    )


def enrich(rows: list[dict], th: Thresholds) -> list[dict]:
    out = []
    for row in rows:
        score, signal_type, tags = score_row(row)
        row['score'] = score
        row['signal_type'] = signal_type
        row['tags'] = ','.join(tags[:6])
        row['candidate'] = is_candidate(row, th)
        out.append(row)
    return out


def pct(v) -> str:
    if v is None:
        return 'NA'
    return f'{_f(v):.2f}%'


def summarize(rows: list[dict]) -> None:
    candidates = [r for r in rows if r['candidate']]
    if not candidates:
        print('无候选。')
        return

    def rate(pred) -> float:
        vals = [r for r in candidates if pred(r) is not None]
        if not vals:
            return 0.0
        return sum(1 for r in vals if pred(r)) / len(vals) * 100

    print('\n=== 汇总 ===')
    print(f'候选数量: {len(candidates)}')
    print(f"当天收盘盈利率: {rate(lambda r: _f(r['day_close_return_pct']) > 0):.1f}%")
    print(f"当天最大浮盈>=3%比例: {rate(lambda r: _f(r['day_max_up_pct']) >= 3):.1f}%")
    print(f"当天最大回撤<=-3%比例: {rate(lambda r: _f(r['day_max_down_pct']) <= -3):.1f}%")
    print(f"次日开盘盈利率: {rate(lambda r: None if r['next_open_return_pct'] is None else _f(r['next_open_return_pct']) > 0):.1f}%")
    print(f"次日10点收盘盈利率: {rate(lambda r: None if r['next_1000_return_pct'] is None else _f(r['next_1000_return_pct']) > 0):.1f}%")
    print(f"次日10点前给>=1.5%卖点比例: {rate(lambda r: None if r['next_1000_max_up_pct'] is None else _f(r['next_1000_max_up_pct']) >= 1.5):.1f}%")

    by_date: dict[str, list[dict]] = {}
    for r in candidates:
        by_date.setdefault(_d(r['date']), []).append(r)
    print('\n=== 分日 ===')
    for d, items in sorted(by_date.items()):
        close_win = sum(1 for r in items if _f(r['day_close_return_pct']) > 0)
        next_win = sum(1 for r in items if r['next_1000_return_pct'] is not None and _f(r['next_1000_return_pct']) > 0)
        print(f'{d}: {len(items)} 只，当天收盘盈利 {close_win}/{len(items)}，次日10点盈利 {next_win}/{len(items)}')


def print_rows(rows: list[dict], limit: int) -> None:
    candidates = [r for r in rows if r['candidate']]
    candidates.sort(key=lambda r: (_d(r['date']), -_f(r['score'])))
    print('\n=== 候选明细 ===')
    header = (
        'date code score type buy ret pos pb amtR eff dMax dClose nOpen n10 n10Max tags'
    )
    print(header)
    for r in candidates[:limit]:
        print(
            f"{_d(r['date'])} {r['code']} {r['score']:.1f} {r['signal_type']} "
            f"{_f(r['buy_price']):.2f} {pct(r['ret_vs_prevclose'])} "
            f"{_f(r['pos_in_range']):.1f} {_f(r['pullback']):.2f} "
            f"{_f(r['amt_vs_prev']):.2f} {_f(r['push_efficiency']):.2f} "
            f"{pct(r['day_max_up_pct'])} {pct(r['day_close_return_pct'])} "
            f"{pct(r['next_open_return_pct'])} {pct(r['next_1000_return_pct'])} "
            f"{pct(r['next_1000_max_up_pct'])} {r['tags']}"
        )


def main():
    parser = argparse.ArgumentParser(description='早盘超短信号离线回放')
    parser.add_argument('--start', default='2026-06-22')
    parser.add_argument('--end', default='2026-06-24')
    parser.add_argument('--snapshot', default='09:42')
    parser.add_argument('--min-score', type=float, default=72.0)
    parser.add_argument('--limit', type=int, default=80)
    args = parser.parse_args()

    th = Thresholds(min_score=args.min_score)
    rows = enrich(fetch_rows(args.start, args.end, args.snapshot), th)
    print(f'回放区间: {args.start} ~ {args.end}, 快照时间: {args.snapshot}, 全样本: {len(rows)}')
    summarize(rows)
    print_rows(rows, args.limit)


if __name__ == '__main__':
    main()
