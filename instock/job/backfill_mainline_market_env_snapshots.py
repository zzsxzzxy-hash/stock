#!/usr/bin/env python3
"""补齐主线接力的早盘市场环境快照，不删除任何既有记录。"""

import argparse
import logging
import os
import sys


PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from instock.job.morning_signal_replay import fetch_rows
from instock.lib import database as mdb
from instock.web.volumeHandler import (
    _mainline_market_environment,
    _save_mainline_market_env_snapshot,
)


DEFAULT_SNAPSHOTS = ('09:35', '09:40', '09:45', '09:50')
DEFAULT_MARKETS = ['cyb', 'kcb', 'bj']


def _dates(start: str, end: str, one_date: str) -> list[str]:
    if one_date:
        return [one_date]
    rows = mdb.executeSqlFetch(
        '''
        SELECT DISTINCT trade_date
        FROM cn_mainline_recommend_snapshot_meta
        WHERE trade_date BETWEEN %s AND %s
        ORDER BY trade_date
        ''',
        (start, end),
    ) or []
    return [str(row[0]) for row in rows]


def _incomplete_environment(snapshot: str) -> dict:
    return {
        'status': '数据不足',
        'action': '等待分钟线补齐',
        'severity': 'info',
        'trade_allowed': False,
        'reason': '该时点缺少可用于回放的分钟线，不能判定市场强弱。',
        'snapshot': snapshot,
        'all': {},
        'pool': {},
        'all_pullback': {},
        'pool_pullback': {},
        'markets': DEFAULT_MARKETS,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description='补齐主线接力市场环境快照')
    parser.add_argument('--start', default='2026-06-02')
    parser.add_argument('--end', default='2026-08-06')
    parser.add_argument('--date', default='')
    parser.add_argument('--snapshots', default=','.join(DEFAULT_SNAPSHOTS))
    args = parser.parse_args()

    snapshots = [item.strip() for item in args.snapshots.split(',') if item.strip()]
    dates = _dates(args.start, args.end, args.date)
    completed = 0
    incomplete = 0
    for date in dates:
        for snapshot in snapshots:
            try:
                rows = fetch_rows(date, date, snapshot, strict=False)
                if rows:
                    env = _mainline_market_environment(rows, DEFAULT_MARKETS, snapshot)
                    _save_mainline_market_env_snapshot(
                        date, snapshot, DEFAULT_MARKETS, env,
                        raw_count=len(rows), source='replay',
                    )
                    completed += 1
                else:
                    _save_mainline_market_env_snapshot(
                        date, snapshot, DEFAULT_MARKETS, _incomplete_environment(snapshot),
                        source='replay_incomplete',
                    )
                    incomplete += 1
                print(f'{date} {snapshot}: {len(rows)}')
            except Exception as exc:
                logging.exception('market environment replay failed: %s %s', date, snapshot)
                _save_mainline_market_env_snapshot(
                    date, snapshot, DEFAULT_MARKETS, _incomplete_environment(snapshot),
                    source='replay_error',
                )
                incomplete += 1
                print(f'{date} {snapshot}: error {exc}')
    print(f'completed={completed}, incomplete={incomplete}, dates={len(dates)}')


if __name__ == '__main__':
    main()
