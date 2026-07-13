#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按当前主线逻辑回放并补齐每日操作记录的系统判断。"""

from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, ROOT)

import instock.lib.database as mdb
from instock.job.morning_signal_replay import Thresholds, enrich, fetch_rows
from instock.web.apiHandler import _system_judgment_text
from instock.web.volumeHandler import (
    _mainline_core_score,
    _mainline_observe_label,
    _mainline_risk_tags,
    _mainline_trade_mode,
    _stock_signal_detail_for_code,
)


REPLAY_THRESHOLDS = Thresholds(
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


def _risk_text(detail: dict) -> str:
    risks = detail.get('risk_tags') or []
    if not isinstance(risks, list):
        risks = [str(risks)] if risks else []
    if not risks:
        risks = [v.strip() for v in str(detail.get('tags') or '').split(',') if v.strip()][:3]
    parts = [str(v) for v in risks if v]
    warning = str(detail.get('replay_warning') or '').strip()
    if warning and warning not in parts:
        parts.insert(0, warning)
    return ' / '.join(parts)


def _replay_warning(row: dict) -> str:
    if not row:
        return '数据不全/计算不可靠'
    missing = []
    if row.get('prev_close') is None:
        missing.append('昨日收盘')
    if row.get('prev_amount_so_far') in (None, 0):
        missing.append('昨日同期成交额')
    if int(row.get('bars') or 0) < 5:
        missing.append('早盘分钟线')
    if row.get('ma5') is None or row.get('high_30d') is None:
        missing.append('历史日线')
    return '数据不全/计算不可靠' if missing else ''


def _signal_item(detail: dict, trade_time: str) -> dict:
    item = {
        'signal_strategy': 'mainline_core',
        'signal_snapshot_time': trade_time,
        'signal_core_score': detail.get('core_score', detail.get('score')),
        'signal_mode': str(detail.get('trade_mode') or detail.get('signal_type') or '').strip(),
        'signal_buy_status': str(detail.get('observe_label') or '').strip(),
        'signal_amount_ratio': detail.get('amt_vs_prev'),
        'signal_risk': _risk_text(detail),
    }
    item['system_judgment'] = _system_judgment_text(item)
    item['mainline'] = str(
        detail.get('mainline_theme')
        or detail.get('trade_theme')
        or detail.get('best_sector')
        or ''
    ).strip()
    item['name'] = str(detail.get('name') or '').strip()
    return item


def _replay_details(date: str, trade_time: str, codes: list[str]) -> dict[str, dict]:
    """Redis 历史缓存缺失时，按操作代码放宽门槛从分钟线数据库回放。"""
    normalized_codes = [str(code).zfill(6) for code in codes if code]
    rows = enrich(
        fetch_rows(date, date, trade_time, strict=False, codes=normalized_codes),
        REPLAY_THRESHOLDS,
    )
    out = {}
    for row in rows:
        code = str(row.get('code')).zfill(6)
        detail = row.copy()
        detail['mainline_theme'] = row.get('best_sector') or ''
        detail['trade_mode'] = _mainline_trade_mode(detail)
        detail['core_score'] = _mainline_core_score(detail)
        detail['observe_label'] = _mainline_observe_label(detail)
        detail['risk_tags'] = _mainline_risk_tags(detail)
        detail['replay_warning'] = _replay_warning(row)
        out[code] = detail
    for code in normalized_codes:
        out.setdefault(
            code,
            {
                'signal_type': '数据不全/计算不可靠',
                'replay_warning': '数据不全/计算不可靠',
            },
        )
    return out


def backfill(refresh: bool = False) -> tuple[int, int, int]:
    rows = mdb.executeSqlFetch(
        '''SELECT id, trade_date::text, trade_time, code, name, mainline
           FROM cn_trade_operation_log
           WHERE (%s OR system_judgment IS NULL OR system_judgment = '')
           ORDER BY trade_date, trade_time, id''',
        (refresh,),
    ) or []
    cache: dict[tuple[str, str], dict[str, dict]] = {}
    replay_cache: dict[tuple[str, str], dict[str, dict]] = {}
    codes_by_key: dict[tuple[str, str], set[str]] = defaultdict(set)
    for _, date, trade_time, code, *_ in rows:
        codes_by_key[(str(date), str(trade_time or ''))].add(str(code).zfill(6))
    updated = 0
    failed = 0
    for index, (item_id, date, trade_time, code, old_name, old_mainline) in enumerate(rows, start=1):
        key = (str(date), str(trade_time or ''))
        try:
            if key not in cache:
                cache[key] = {}
            normalized_code = str(code).zfill(6)
            if normalized_code not in cache[key]:
                detail = _stock_signal_detail_for_code(normalized_code, key[0], key[1])
                if detail.get('signal_type') == '数据不足' and detail.get('core_score') is None:
                    if key not in replay_cache:
                        print(f'  Redis历史缓存缺失，改用分钟线回放 {key[0]} {key[1]}', flush=True)
                        replay_cache[key] = _replay_details(key[0], key[1], sorted(codes_by_key[key]))
                    detail = replay_cache[key].get(
                        normalized_code,
                        {
                            'signal_type': '数据不全/计算不可靠',
                            'replay_warning': '数据不全/计算不可靠',
                        },
                    )
                cache[key][normalized_code] = detail
            detail = cache[key][normalized_code]
            item = _signal_item(detail, key[1])
            mdb.executeSql(
                '''UPDATE cn_trade_operation_log
                   SET name = COALESCE(NULLIF(name, ''), %s),
                       mainline = COALESCE(NULLIF(mainline, ''), %s),
                       system_judgment = %s,
                       signal_strategy = %s,
                       signal_snapshot_time = %s,
                       signal_core_score = %s,
                       signal_mode = %s,
                       signal_buy_status = %s,
                       signal_amount_ratio = %s,
                       signal_risk = %s,
                       updated_at = CURRENT_TIMESTAMP
                   WHERE id = %s''',
                (
                    item['name'] or old_name or '',
                    item['mainline'] or old_mainline or '',
                    item['system_judgment'],
                    item['signal_strategy'],
                    item['signal_snapshot_time'],
                    item['signal_core_score'],
                    item['signal_mode'],
                    item['signal_buy_status'],
                    item['signal_amount_ratio'],
                    item['signal_risk'],
                    item_id,
                ),
            )
            updated += 1
            print(
                f'[{index}/{len(rows)}] {date} {trade_time} {str(code).zfill(6)} '
                f'{item["signal_mode"] or "数据不足"} / {item["signal_buy_status"] or "-"}'
            )
        except Exception as exc:
            failed += 1
            print(f'[{index}/{len(rows)}] 失败 {date} {trade_time} {code}: {exc}', file=sys.stderr)
    return updated, failed, len(cache)


def main() -> None:
    parser = argparse.ArgumentParser(description='回放当前版本并补齐操作记录系统判断')
    parser.add_argument('--refresh', action='store_true', help='覆盖已经存在的系统判断')
    args = parser.parse_args()
    updated, failed, snapshots = backfill(args.refresh)
    print(f'完成：更新 {updated} 条，失败 {failed} 条，处理 {snapshots} 个日期/时间快照')
    if failed:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
