#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回填超短主线接力每分钟候选快照到 PostgreSQL。

默认回填 09:35-09:50 的页面候选池，用于后续统计推荐次数和策略验证。
历史回放从 PostgreSQL 分钟线/日线重建，不依赖 Redis 历史缓存。
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from collections import defaultdict
from typing import Any

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, ROOT)

import instock.lib.database as mdb
from analysis.mainline_candidate_pool_backtest import (
    DEFAULT_MARKETS,
    PARAMS,
    build_snapshot_rows,
    load_bars,
    load_hist,
    load_names,
    load_prev_minute_close,
    load_trade_themes,
    make_candidate_rows,
)
from instock.web.volumeHandler import (
    _mainline_market_environment,
    _mainline_max_streak,
    _mainline_recommend_param_key,
    _market_code_fn,
    _save_mainline_recommend_snapshot,
)


def _d(value: Any) -> str:
    return value.strftime('%Y-%m-%d') if hasattr(value, 'strftime') else str(value)


def _snapshot_minutes(start: str, end: str) -> list[str]:
    def idx(value: str) -> int:
        hh, mm = value.split(':')
        return int(hh) * 60 + int(mm)

    start_i = idx(start)
    end_i = idx(end)
    return [f'{m // 60:02d}:{m % 60:02d}' for m in range(start_i, end_i + 1)]


def trading_dates(start: str, end: str) -> list[str]:
    rows = mdb.executeSqlFetch(
        """
        SELECT date, COUNT(DISTINCT code) AS codes
        FROM cn_stock_minute_bar
        WHERE date BETWEEN %s AND %s
        GROUP BY date
        HAVING COUNT(DISTINCT code) >= 800
        ORDER BY date
        """,
        (start, end),
    ) or []
    return [_d(r[0]) for r in rows]


def previous_trade_date(date: str) -> str:
    rows = mdb.executeSqlFetch(
        """
        SELECT MAX(date)
        FROM cn_stock_minute_bar
        WHERE date < %s
        """,
        (date,),
    ) or []
    return _d(rows[0][0]) if rows and rows[0][0] else ''


def annotate_counts(rows: list[dict], snapshot: str,
                    history: dict[str, dict[str, list[str]]]) -> list[dict]:
    for row in rows:
        code = str(row.get('code') or '').zfill(6)
        if not code:
            continue
        item = history.setdefault(code, {'recommend': []})
        if snapshot not in item['recommend']:
            item['recommend'].append(snapshot)

    out = []
    for row in rows:
        code = str(row.get('code') or '').zfill(6)
        item = history.get(code, {'recommend': []})
        rec = item['recommend']
        payload = dict(row)
        payload['recommend_count'] = len(rec)
        payload['max_consecutive_count'] = _mainline_max_streak(rec)
        payload['recommend_snapshots'] = ','.join(rec)
        payload['payload_json'] = dict(payload)
        out.append(payload)
    return out


def backfill(start: str, end: str, snapshot_start: str, snapshot_end: str,
             limit: int) -> None:
    dates = trading_dates(start, end)
    snapshots = _snapshot_minutes(snapshot_start, snapshot_end)
    if not dates:
        raise RuntimeError(f'没有找到可回填交易日：{start}~{end}')
    if not snapshots:
        raise RuntimeError('快照时间范围为空')

    names = load_names(end)
    sectors, member_count = load_trade_themes()
    market_fn = _market_code_fn(DEFAULT_MARKETS)
    param_key, param_text = _mainline_recommend_param_key(
        int(PARAMS['max_sector_rank']),
        int(PARAMS['min_sector_strong']),
        float(PARAMS['min_ret']),
        float(PARAMS['max_ret']),
        float(PARAMS['min_amt_ratio']),
        str(PARAMS['theme']),
        float(PARAMS['min_amount']),
        DEFAULT_MARKETS,
        limit,
    )

    total_rows = 0
    for index, date in enumerate(dates, start=1):
        prev_date = previous_trade_date(date)
        if not prev_date:
            print(f'[{index}/{len(dates)}] {date} 无上一交易日，跳过', flush=True)
            continue
        print(f'[{index}/{len(dates)}] {date} prev={prev_date}', flush=True)
        hist = load_hist(date)
        prev_close = load_prev_minute_close(prev_date)
        for code, close in prev_close.items():
            if code in hist and close and close > 0:
                hist[code]['prev_close'] = close
        today_map = load_bars(date, snapshot_end)
        prev_map = load_bars(prev_date, snapshot_end)
        if len(today_map) < 800:
            print(f'  数据提醒：{date} 早盘分钟线覆盖股票数偏少 {len(today_map)}', flush=True)

        early_codes = None
        history: dict[str, dict[str, list[str]]] = defaultdict(lambda: {'recommend': []})
        for snapshot in snapshots:
            rows = build_snapshot_rows(
                date, prev_date, snapshot, hist, today_map, prev_map,
                sectors, member_count, market_fn,
            )
            market_env = _mainline_market_environment(rows, DEFAULT_MARKETS, snapshot)
            page_rows, passed_codes = make_candidate_rows(
                date, snapshot, rows, early_codes, names, market_env,
            )
            if snapshot == '09:45':
                early_codes = passed_codes
            page_rows = page_rows[:limit]
            store_rows = annotate_counts(page_rows, snapshot, history)
            _save_mainline_recommend_snapshot(date, snapshot, param_key, param_text, store_rows)
            total_rows += len(store_rows)
            print(f'  {snapshot}: 候选={len(store_rows)}', flush=True)

    print(f'完成：{start}~{end}，分钟 {snapshot_start}-{snapshot_end}，写入/更新 {total_rows} 行候选快照')


def main() -> None:
    parser = argparse.ArgumentParser(description='回填主线接力候选池分钟快照')
    parser.add_argument('--start', default='2026-06-01')
    parser.add_argument('--end', default=dt.date.today().strftime('%Y-%m-%d'))
    parser.add_argument('--snapshot-start', default='09:35')
    parser.add_argument('--snapshot-end', default='09:50')
    parser.add_argument('--limit', type=int, default=300)
    args = parser.parse_args()
    backfill(args.start, args.end, args.snapshot_start, args.snapshot_end, args.limit)


if __name__ == '__main__':
    main()
