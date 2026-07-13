#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
用 6 月全市场 10:00 回放校准“主线核心观察”。

校准目标：
1. 尽量召回用户真实交易中“选股成立/给过机会”的票；
2. 控制每天候选池数量，适合早盘人工筛选；
3. 输出漏选原因和建议参数，而不是直接改页面规则。
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import json
import os
import sys
from collections import Counter, defaultdict
from itertools import product
from pathlib import Path
from typing import Any

cpath = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, cpath)

import instock.lib.database as mdb
from instock.job.morning_signal_replay import Thresholds, enrich, fetch_rows


POSITIVE_LABELS = {'选股成立', '给过机会'}


def _f(v: Any, default: float = 0.0) -> float:
    if v is None or v == '':
        return default
    return float(v)


def _d(v: Any) -> str:
    if hasattr(v, 'strftime'):
        return v.strftime('%Y-%m-%d')
    return str(v)


def _json_number(v: Any) -> Any:
    if hasattr(v, 'strftime'):
        return v.strftime('%Y-%m-%d')
    try:
        from decimal import Decimal
        if isinstance(v, Decimal):
            return float(v)
    except Exception:
        pass
    return v


def trading_dates(start: str, end: str) -> list[str]:
    rows = mdb.executeSqlFetch(
        '''SELECT date, COUNT(DISTINCT code) AS codes
           FROM cn_stock_minute_bar
           WHERE date BETWEEN %s AND %s
           GROUP BY date
           HAVING COUNT(DISTINCT code) >= 3000
           ORDER BY date''',
        (start, end),
    )
    return [_d(r[0]) for r in rows or []]


def load_targets(path: Path) -> tuple[list[dict], set[tuple[str, str]], set[tuple[str, str]]]:
    data = json.loads(path.read_text(encoding='utf-8'))
    rows = data['rows']
    positive: set[tuple[str, str]] = set()
    all_buys: set[tuple[str, str]] = set()
    for r in rows:
        key = (r['buy_date'], str(r['code']).zfill(6))
        all_buys.add(key)
        if r.get('selection_result') in POSITIVE_LABELS:
            positive.add(key)
    return rows, positive, all_buys


def slim_row(row: dict) -> dict:
    fields = [
        'date', 'code', 'score', 'signal_type', 'best_sector',
        'sector_rank', 'sector_strong_count', 'sector_top3_avg_ret',
        'buy_price', 'ret_vs_prevclose', 'pos_in_range', 'pullback',
        'amt_vs_prev', 'push_efficiency', 'distance_to_30d_high',
        'break_30d_high', 'ma_converge_pct', 'ma_bull',
        'amount_so_far', 'day_max_up_pct', 'day_max_down_pct',
        'day_close_return_pct', 'next_open_return_pct',
        'next_1000_return_pct', 'next_1000_max_up_pct',
        'next_1000_max_down_pct', 'tags',
    ]
    out = {k: _json_number(row.get(k)) for k in fields}
    out['date'] = _d(out['date'])
    out['code'] = str(out['code']).zfill(6)
    return out


def load_or_build_features(start: str, end: str, snapshot: str,
                           cache_path: Path, refresh: bool) -> list[dict]:
    if cache_path.exists() and not refresh:
        with gzip.open(cache_path, 'rt', encoding='utf-8') as f:
            return json.load(f)

    dates = trading_dates(start, end)
    th = Thresholds(
        min_score=0,
        min_ret=-999,
        max_ret=999,
        min_pos=0,
        max_pullback=999,
        min_efficiency=-999,
        min_amount=0,
        max_amount_ratio=999,
        max_price=999999,
        max_sector_rank=0,
        min_sector_strong=0,
    )
    all_rows: list[dict] = []
    for i, d in enumerate(dates, 1):
        print(f'[{i}/{len(dates)}] 构建 {d} {snapshot} 全市场画像...')
        day_rows = enrich(fetch_rows(d, d, snapshot), th)
        all_rows.extend(slim_row(r) for r in day_rows)
        print(f'  {d}: {len(day_rows)} rows')

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(cache_path, 'wt', encoding='utf-8') as f:
        json.dump(all_rows, f, ensure_ascii=False)
    return all_rows


def mode_of(row: dict) -> str:
    ret = _f(row.get('ret_vs_prevclose'))
    pos = _f(row.get('pos_in_range'))
    pullback = _f(row.get('pullback'))
    rank = int(row.get('sector_rank') or 999)
    strong = int(row.get('sector_strong_count') or 0)
    dist = _f(row.get('distance_to_30d_high'), 999)
    ma_conv = _f(row.get('ma_converge_pct'), 99)
    ma_bull = bool(row.get('ma_bull'))
    if rank <= 8 and strong >= 3 and ret >= 5 and pos >= 75 and pullback <= 3:
        return '主线核心追强'
    if rank <= 6 and strong >= 1 and ret >= 0 and pos >= 20 and pullback <= 4.8:
        return '核心中位承接'
    if rank <= 10 and strong >= 2 and -2 <= ret <= 7 and dist >= 6 and ma_bull and ma_conv <= 10:
        return '主线低位突破'
    if rank <= 12 and strong >= 0 and -6 <= ret <= 4 and pos >= 30:
        return '修复反包观察'
    return '主线观察'


def row_score(row: dict) -> float:
    rank = int(row.get('sector_rank') or 999)
    strong = int(row.get('sector_strong_count') or 0)
    top3 = _f(row.get('sector_top3_avg_ret'))
    ret = _f(row.get('ret_vs_prevclose'))
    pos = _f(row.get('pos_in_range'))
    pullback = _f(row.get('pullback'))
    amt = _f(row.get('amt_vs_prev'))
    eff = _f(row.get('push_efficiency'))
    dist = _f(row.get('distance_to_30d_high'), 0)
    score = 0.0
    score += max(0, 16 - rank) * 5
    score += strong * 7
    score += max(0, top3) * 2.5
    score += max(0, ret) * 1.5
    score += max(0, min(100, pos)) * 0.16
    score += min(28, max(0, amt - 0.7) * 18)
    score += min(18, max(0, eff))
    score += min(12, max(0, dist) * 0.3)
    score -= max(0, pullback - 2) * 4
    mode = mode_of(row)
    if mode == '主线核心追强':
        score += 18
    elif mode == '核心中位承接':
        score += 22
    elif mode == '主线低位突破':
        score += 12
    elif mode == '修复反包观察':
        score += 6
    return round(score, 2)


def passes(row: dict, p: dict) -> bool:
    if not row.get('best_sector'):
        return False
    rank = int(row.get('sector_rank') or 999)
    strong = int(row.get('sector_strong_count') or 0)
    ret = _f(row.get('ret_vs_prevclose'))
    pos = _f(row.get('pos_in_range'), -999)
    pullback = _f(row.get('pullback'), 999)
    amt_ratio = _f(row.get('amt_vs_prev'))
    amount = _f(row.get('amount_so_far'))
    if amount < p['min_amount']:
        return False
    if rank > p['max_rank'] or strong < p['min_strong']:
        return False
    if ret < p['min_ret'] or ret > p['max_ret']:
        return False
    if amt_ratio < p['min_amt_ratio']:
        return False

    mode = row.get('_mode') or mode_of(row)
    if mode == '主线核心追强':
        return pos >= p['chase_min_pos'] and pullback <= p['chase_max_pullback']
    if mode == '核心中位承接':
        return pullback <= p['mid_max_pullback']
    if mode == '主线低位突破':
        return pullback <= p['breakout_max_pullback']
    if mode == '修复反包观察':
        return p['allow_repair'] and pullback <= p['repair_max_pullback']
    return p['allow_observe'] and pos >= p['observe_min_pos'] and pullback <= p['observe_max_pullback']


def candidate_success(row: dict) -> bool:
    vals = [
        _f(row.get('day_close_return_pct'), -999),
        _f(row.get('day_max_up_pct'), -999),
        _f(row.get('next_open_return_pct'), -999),
        _f(row.get('next_1000_return_pct'), -999),
        _f(row.get('next_1000_max_up_pct'), -999),
    ]
    return max(vals) >= 3 or max(vals[0], vals[2], vals[3]) >= 1


def param_grid() -> list[dict]:
    out: list[dict] = []
    for (max_rank, min_strong, min_ret, max_ret, min_amt_ratio,
         chase_min_pos, chase_pb, top_n, min_amount, allow_repair) in product(
            [8, 12, 20, 40],
            [0, 1, 2, 3],
            [-6.0, -3.0, 0.0],
            [35.0],
            [0.3, 0.7],
            [30.0, 45.0, 65.0],
            [4.5, 7.0],
            [120, 200, 300],
            [1_000_000, 3_000_000, 5_000_000],
            [True],
    ):
        out.append({
            'max_rank': max_rank,
            'min_strong': min_strong,
            'min_ret': min_ret,
            'max_ret': max_ret,
            'min_amt_ratio': min_amt_ratio,
            'chase_min_pos': chase_min_pos,
            'chase_max_pullback': chase_pb,
            'mid_max_pullback': 5.0,
            'breakout_max_pullback': 4.0,
            'allow_repair': allow_repair,
            'repair_max_pullback': 5.0,
            'allow_observe': True,
            'observe_min_pos': 50.0,
            'observe_max_pullback': 4.0,
            'top_n': top_n,
            'min_amount': min_amount,
        })
    return out


def calibrate(rows: list[dict], targets: set[tuple[str, str]],
              all_buys: set[tuple[str, str]]) -> tuple[list[dict], dict[str, list[dict]]]:
    by_date: dict[str, list[dict]] = defaultdict(list)
    row_by_key: dict[tuple[str, str], dict] = {}
    for r in rows:
        r['_mode'] = mode_of(r)
        r['_cal_score'] = row_score(r)
        by_date[r['date']].append(r)
        row_by_key[(r['date'], r['code'])] = r
    for items in by_date.values():
        items.sort(key=lambda r: (-_f(r['_cal_score']), int(r.get('sector_rank') or 999), -_f(r.get('ret_vs_prevclose'))))

    available_targets = {k for k in targets if k in row_by_key}
    available_buys = {k for k in all_buys if k in row_by_key}
    results: list[dict] = []
    for p in param_grid():
        candidate_keys: set[tuple[str, str]] = set()
        candidate_rows: list[dict] = []
        daily_counts: list[int] = []
        for d, items in by_date.items():
            passed = [r for r in items if passes(r, p)]
            passed.sort(key=lambda r: (-_f(r['_cal_score']), int(r.get('sector_rank') or 999), -_f(r.get('ret_vs_prevclose'))))
            picked = passed[:p['top_n']]
            daily_counts.append(len(picked))
            candidate_rows.extend(picked)
            candidate_keys.update((r['date'], r['code']) for r in picked)

        hit_targets = available_targets & candidate_keys
        hit_buys = available_buys & candidate_keys
        success_count = sum(1 for r in candidate_rows if candidate_success(r))
        avg_count = sum(daily_counts) / len(daily_counts) if daily_counts else 0
        max_count = max(daily_counts) if daily_counts else 0
        target_recall = len(hit_targets) / len(available_targets) if available_targets else 0
        buy_recall = len(hit_buys) / len(available_buys) if available_buys else 0
        opp_rate = success_count / len(candidate_rows) if candidate_rows else 0
        results.append({
            **p,
            'target_recall': round(target_recall, 4),
            'buy_recall': round(buy_recall, 4),
            'hit_targets': len(hit_targets),
            'available_targets': len(available_targets),
            'hit_buys': len(hit_buys),
            'available_buys': len(available_buys),
            'avg_daily_count': round(avg_count, 1),
            'max_daily_count': max_count,
            'candidate_count': len(candidate_rows),
            'candidate_opportunity_rate': round(opp_rate, 4),
            'score': round(target_recall * 120 - max(0, avg_count - 180) * 0.08 + opp_rate * 10, 4),
        })

    results.sort(key=lambda x: (-x['target_recall'], x['avg_daily_count'], -x['candidate_opportunity_rate'], -x['score']))
    debug = {
        'row_by_key': row_by_key,
        'by_date': by_date,
    }
    return results, debug


def explain_misses(best: dict, debug: dict, targets_rows: list[dict],
                   targets: set[tuple[str, str]]) -> list[dict]:
    row_by_key = debug['row_by_key']
    by_date = debug['by_date']
    candidate_keys: set[tuple[str, str]] = set()
    for d, items in by_date.items():
        picked = [r for r in items if passes(r, best)]
        picked.sort(key=lambda r: (-_f(r['_cal_score']), int(r.get('sector_rank') or 999), -_f(r.get('ret_vs_prevclose'))))
        candidate_keys.update((r['date'], r['code']) for r in picked[:best['top_n']])

    target_info = {(r['buy_date'], str(r['code']).zfill(6)): r for r in targets_rows}
    misses: list[dict] = []
    for key in sorted(targets):
        if key in candidate_keys:
            continue
        info = target_info.get(key, {})
        row = row_by_key.get(key)
        if not row:
            reason = '10点画像缺失'
            detail = {}
        elif not row.get('best_sector'):
            reason = '交易主线缺失'
            detail = row
        elif not passes(row, best):
            reasons = []
            if int(row.get('sector_rank') or 999) > best['max_rank']:
                reasons.append(f"主线排名#{row.get('sector_rank')}超阈值{best['max_rank']}")
            if int(row.get('sector_strong_count') or 0) < best['min_strong']:
                reasons.append(f"主线共振{row.get('sector_strong_count')}低于{best['min_strong']}")
            if _f(row.get('ret_vs_prevclose')) < best['min_ret']:
                reasons.append('早盘涨幅过低')
            if _f(row.get('ret_vs_prevclose')) > best['max_ret']:
                reasons.append('早盘涨幅过高')
            if _f(row.get('amt_vs_prev')) < best['min_amt_ratio']:
                reasons.append('量能同比不足')
            if _f(row.get('pullback'), 999) > max(best['chase_max_pullback'], best['breakout_max_pullback'], best['repair_max_pullback']):
                reasons.append('早盘回撤偏大')
            reason = ' / '.join(reasons) or '排序/模式条件未通过'
            detail = row
        else:
            reason = f"进入候选但未进每日Top{best['top_n']}"
            detail = row
        misses.append({
            'date': key[0],
            'code': key[1],
            'name': info.get('name', ''),
            'selection_result': info.get('selection_result', ''),
            'actual_return_pct': info.get('actual_return_pct'),
            'reason': reason,
            'best_sector': detail.get('best_sector') if detail else '',
            'sector_rank': detail.get('sector_rank') if detail else '',
            'sector_strong_count': detail.get('sector_strong_count') if detail else '',
            'ret_vs_prevclose': detail.get('ret_vs_prevclose') if detail else '',
            'pos_in_range': detail.get('pos_in_range') if detail else '',
            'pullback': detail.get('pullback') if detail else '',
            'amt_vs_prev': detail.get('amt_vs_prev') if detail else '',
        })
    return misses


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open('w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)


def write_report(path: Path, best_rows: list[dict], misses: list[dict], targets: set[tuple[str, str]], all_buys: set[tuple[str, str]]) -> None:
    best = best_rows[0]
    lines = [
        '# 主线核心观察 6月校准报告',
        '',
        '## 推荐参数',
        f"- 每日候选上限：{best['top_n']}",
        f"- 主线排名 <= {best['max_rank']}",
        f"- 主线共振 >= {best['min_strong']} 只",
        f"- 10点涨幅：{best['min_ret']}% ~ {best['max_ret']}%",
        f"- 量能同比 >= {best['min_amt_ratio']}x",
        f"- 10点成交额 >= {best['min_amount'] / 10000:.0f} 万",
        f"- 追强位置 >= {best['chase_min_pos']}%，追强回撤 <= {best['chase_max_pullback']}%",
        f"- 是否允许修复反包：{'是' if best['allow_repair'] else '否'}",
        '',
        '## 校准结果',
        f"- 可校准正确票召回：{best['hit_targets']} / {best['available_targets']} = {best['target_recall'] * 100:.1f}%",
        f"- 可校准全部买入召回：{best['hit_buys']} / {best['available_buys']} = {best['buy_recall'] * 100:.1f}%",
        f"- 平均每日候选：{best['avg_daily_count']} 只，最多 {best['max_daily_count']} 只",
        f"- 候选池机会率：{best['candidate_opportunity_rate'] * 100:.1f}%",
        '',
        '## Top 参数组合',
    ]
    for r in best_rows[:10]:
        lines.append(
            f"- Top{r['top_n']} rank<={r['max_rank']} strong>={r['min_strong']} "
            f"ret {r['min_ret']}~{r['max_ret']} amt>={r['min_amt_ratio']}："
            f"召回 {r['hit_targets']}/{r['available_targets']}，"
            f"日均 {r['avg_daily_count']}，机会率 {r['candidate_opportunity_rate'] * 100:.1f}%"
        )
    lines.extend(['', '## 漏选正确票'])
    for m in misses:
        lines.append(
            f"- {m['date']} {m['code']} {m['name']}：{m['reason']}；"
            f"主线 {m.get('best_sector') or '-'} #{m.get('sector_rank') or '-'}，"
            f"共振 {m.get('sector_strong_count') or '-'}，"
            f"涨幅 {m.get('ret_vs_prevclose') or '-'}，回撤 {m.get('pullback') or '-'}"
        )
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description='校准主线核心观察参数')
    parser.add_argument('--start', default='2026-06-01')
    parser.add_argument('--end', default='2026-06-30')
    parser.add_argument('--snapshot', default='10:00')
    parser.add_argument('--attribution-json', default='outputs/trade_pattern_20260630/selection_attribution.json')
    parser.add_argument('--output-dir', default='outputs/mainline_calibration_202606')
    parser.add_argument('--refresh-cache', action='store_true')
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    cache_path = output_dir / f'features_{args.start}_{args.end}_{args.snapshot.replace(":", "")}.json.gz'

    target_rows, positive_targets, all_buys = load_targets(Path(args.attribution_json))
    rows = load_or_build_features(args.start, args.end, args.snapshot, cache_path, args.refresh_cache)
    results, debug = calibrate(rows, positive_targets, all_buys)
    misses = explain_misses(results[0], debug, target_rows, positive_targets)

    result_fields = [
        'score', 'target_recall', 'hit_targets', 'available_targets',
        'buy_recall', 'hit_buys', 'available_buys', 'avg_daily_count',
        'max_daily_count', 'candidate_count', 'candidate_opportunity_rate',
        'top_n', 'max_rank', 'min_strong', 'min_ret', 'max_ret',
        'min_amt_ratio', 'chase_min_pos', 'chase_max_pullback',
        'min_amount', 'allow_repair',
    ]
    miss_fields = [
        'date', 'code', 'name', 'selection_result', 'actual_return_pct',
        'reason', 'best_sector', 'sector_rank', 'sector_strong_count',
        'ret_vs_prevclose', 'pos_in_range', 'pullback', 'amt_vs_prev',
    ]
    write_csv(output_dir / 'calibration_grid.csv', results, result_fields)
    write_csv(output_dir / 'misses.csv', misses, miss_fields)
    write_report(output_dir / 'summary.md', results[:20], misses, positive_targets, all_buys)

    best = results[0]
    print('校准完成')
    print(f"最佳: Top{best['top_n']} rank<={best['max_rank']} strong>={best['min_strong']} "
          f"ret {best['min_ret']}~{best['max_ret']} amt>={best['min_amt_ratio']}")
    print(f"正确票召回: {best['hit_targets']}/{best['available_targets']} = {best['target_recall'] * 100:.1f}%")
    print(f"日均候选: {best['avg_daily_count']}，机会率: {best['candidate_opportunity_rate'] * 100:.1f}%")
    print(f"输出: {output_dir / 'summary.md'}")


if __name__ == '__main__':
    main()
