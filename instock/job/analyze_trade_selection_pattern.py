#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从交割单反推超短选股模式。

核心目标不是只按真实盈亏给交易贴成功/失败标签，而是拆成：
- 选股是否给过合理盈利窗口
- 买点是否追高
- 卖点是否拖累

默认读取用户 6 月交易记录，结合 cn_stock_minute_bar / cn_stock_hist_data /
cn_stock_trade_theme 输出逐笔归因 CSV 和 Markdown 汇总。
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import sys
import zipfile
import xml.etree.ElementTree as ET
from collections import defaultdict, deque
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

cpath = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, cpath)

import instock.lib.database as mdb


NS = {'a': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}


def _f(v: Any, default: float = 0.0) -> float:
    if v is None or v == '':
        return default
    if isinstance(v, Decimal):
        return float(v)
    return float(v)


def _pct(v: Any) -> str:
    if v is None:
        return '-'
    return f'{_f(v):.2f}%'


def _date_str(v: Any) -> str:
    if isinstance(v, (dt.date, dt.datetime)):
        return v.strftime('%Y-%m-%d')
    s = str(v or '').strip()
    if len(s) == 8 and s.isdigit():
        return f'{s[:4]}-{s[4:6]}-{s[6:]}'
    return s


def _json_number(v: Any) -> Any:
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (dt.date, dt.datetime)):
        return v.strftime('%Y-%m-%d')
    return v


def _col_to_idx(cell_ref: str) -> int:
    col = ''.join(ch for ch in cell_ref if ch.isalpha())
    n = 0
    for ch in col:
        n = n * 26 + ord(ch.upper()) - ord('A') + 1
    return n - 1


def _read_xlsx_rows(path: Path) -> list[list[Any]]:
    """用标准库读取简单 xlsx，避免运行环境同时依赖 openpyxl 与 psycopg2。"""
    with zipfile.ZipFile(path) as zf:
        shared: list[str] = []
        if 'xl/sharedStrings.xml' in zf.namelist():
            root = ET.fromstring(zf.read('xl/sharedStrings.xml'))
            for si in root.findall('a:si', NS):
                shared.append(''.join(t.text or '' for t in si.findall('.//a:t', NS)))

        workbook = ET.fromstring(zf.read('xl/workbook.xml'))
        rels = ET.fromstring(zf.read('xl/_rels/workbook.xml.rels'))
        rel_map = {
            rel.attrib['Id']: rel.attrib['Target']
            for rel in rels
        }
        sheet = workbook.find('a:sheets/a:sheet', NS)
        if sheet is None:
            return []
        rid = sheet.attrib['{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id']
        target = rel_map[rid]
        sheet_path = 'xl/' + target.lstrip('/')
        if not sheet_path.startswith('xl/worksheets/'):
            sheet_path = 'xl/' + target

        root = ET.fromstring(zf.read(sheet_path))
        out: list[list[Any]] = []
        for row in root.findall('.//a:sheetData/a:row', NS):
            vals: list[Any] = []
            for cell in row.findall('a:c', NS):
                idx = _col_to_idx(cell.attrib.get('r', 'A1'))
                while len(vals) <= idx:
                    vals.append(None)
                v = cell.find('a:v', NS)
                raw = v.text if v is not None else ''
                if cell.attrib.get('t') == 's':
                    vals[idx] = shared[int(raw)] if raw != '' else ''
                else:
                    vals[idx] = raw
            out.append(vals)
        return out


def load_trades(path: Path) -> list[dict[str, Any]]:
    rows = _read_xlsx_rows(path)
    header_idx = next(
        (i for i, row in enumerate(rows) if row and str(row[0]).strip() == '日期'),
        -1,
    )
    if header_idx < 0:
        raise RuntimeError('没有找到交易记录表头')

    headers = [str(x).strip() if x is not None else '' for x in rows[header_idx]]
    trades: list[dict[str, Any]] = []
    for raw in rows[header_idx + 1:]:
        if not raw or not raw[0]:
            continue
        item = {h: (raw[i] if i < len(raw) else None) for i, h in enumerate(headers) if h}
        if '证券代码' not in item or not item.get('证券代码'):
            continue
        item['日期'] = _date_str(item.get('日期'))
        item['证券代码'] = str(item.get('证券代码')).strip().zfill(6)
        for key in ('成交数量', '成交均价', '佣金', '印花税', '其他费', '发生金额', '资金余额'):
            item[key] = _f(item.get(key))
        item['trade_id'] = f"T{len(trades) + 1:04d}"
        trades.append(item)
    return trades


@dataclass
class Lot:
    trade_id: str
    account: str
    code: str
    name: str
    buy_date: str
    qty: float
    buy_price: float
    total_cost: float
    cost_price: float


def build_fifo(trades: list[dict[str, Any]]):
    lots: dict[tuple[str, str], deque[Lot]] = defaultdict(deque)
    buys: dict[str, dict[str, Any]] = {}
    allocations: list[dict[str, Any]] = []
    unmatched_sells: list[dict[str, Any]] = []

    for tr in trades:
        action = str(tr.get('摘要') or '')
        account = str(tr.get('股东账号') or '')
        code = tr['证券代码']
        qty = _f(tr.get('成交数量'))
        if qty <= 0:
            continue
        key = (account, code)

        if '买入' in action:
            total_cost = abs(_f(tr.get('发生金额')))
            cost_price = total_cost / qty if qty else _f(tr.get('成交均价'))
            lot = Lot(
                trade_id=tr['trade_id'],
                account=account,
                code=code,
                name=str(tr.get('证券名称') or ''),
                buy_date=tr['日期'],
                qty=qty,
                buy_price=_f(tr.get('成交均价')),
                total_cost=total_cost,
                cost_price=cost_price,
            )
            lots[key].append(lot)
            buys[lot.trade_id] = {
                'trade_id': lot.trade_id,
                'buy_date': lot.buy_date,
                'code': lot.code,
                'name': lot.name,
                'account': account,
                'buy_qty': qty,
                'buy_price': lot.buy_price,
                'buy_total_cost': total_cost,
                'buy_cost_price': cost_price,
                'actual_sold_qty': 0.0,
                'actual_sell_proceeds': 0.0,
                'actual_cost_matched': 0.0,
                'actual_sell_dates': set(),
            }
            continue

        if '卖出' not in action:
            continue

        remain = qty
        sell_net_price = _f(tr.get('发生金额')) / qty if qty else 0
        while remain > 1e-9 and lots[key]:
            lot = lots[key][0]
            mqty = min(remain, lot.qty)
            matched_cost = lot.cost_price * mqty
            sell_proceeds = sell_net_price * mqty
            allocations.append({
                'buy_trade_id': lot.trade_id,
                'sell_trade_id': tr['trade_id'],
                'code': code,
                'sell_date': tr['日期'],
                'qty': mqty,
                'sell_net_price': sell_net_price,
                'matched_cost': matched_cost,
                'sell_proceeds': sell_proceeds,
                'pnl': sell_proceeds - matched_cost,
            })
            b = buys[lot.trade_id]
            b['actual_sold_qty'] += mqty
            b['actual_sell_proceeds'] += sell_proceeds
            b['actual_cost_matched'] += matched_cost
            b['actual_sell_dates'].add(tr['日期'])
            lot.qty -= mqty
            remain -= mqty
            if lot.qty <= 1e-9:
                lots[key].popleft()
        if remain > 1e-9:
            unmatched_sells.append({**tr, 'unmatched_qty': remain})

    open_lots: list[Lot] = []
    for q in lots.values():
        open_lots.extend([lot for lot in q if lot.qty > 1e-9])

    for b in buys.values():
        b['actual_unsold_qty'] = b['buy_qty'] - b['actual_sold_qty']
        b['actual_pnl'] = b['actual_sell_proceeds'] - b['actual_cost_matched']
        b['actual_return_pct'] = (
            b['actual_pnl'] / b['actual_cost_matched'] * 100
            if b['actual_cost_matched'] else None
        )
        b['actual_sell_dates'] = ','.join(sorted(b['actual_sell_dates']))

    return list(buys.values()), allocations, unmatched_sells, open_lots


def fetch_daily(codes: list[str], start: str, end: str) -> dict[tuple[str, str], dict[str, Any]]:
    rows = mdb.executeSqlFetch(
        f'''SELECT code, date, open, close, high, low, quote_change, amount
            FROM cn_stock_hist_data
            WHERE code IN ({','.join(['%s'] * len(codes))})
              AND date BETWEEN %s AND %s
            ORDER BY code, date''',
        tuple(codes + [start, end]),
    )
    return {
        (r[0], _date_str(r[1])): {
            'open': _f(r[2]), 'close': _f(r[3]), 'high': _f(r[4]), 'low': _f(r[5]),
            'quote_change': _f(r[6]), 'amount': _f(r[7]),
        }
        for r in rows or []
    }


def fetch_minutes(codes: list[str], dates: list[str]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    if not codes or not dates:
        return {}
    rows = mdb.executeSqlFetch(
        f'''SELECT code, date, time, open, close, high, low, volume, amount, pre_close
            FROM cn_stock_minute_bar
            WHERE code IN ({','.join(['%s'] * len(codes))})
              AND date IN ({','.join(['%s'] * len(dates))})
            ORDER BY code, date, time''',
        tuple(codes + dates),
    )
    out: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in rows or []:
        out[(r[0], _date_str(r[1]))].append({
            'time': str(r[2])[:5],
            'open': _f(r[3]), 'close': _f(r[4]), 'high': _f(r[5]), 'low': _f(r[6]),
            'volume': _f(r[7]), 'amount': _f(r[8]), 'pre_close': _f(r[9]),
        })
    return out


def fetch_trade_dates(codes: list[str], start: str, end: str) -> dict[str, list[str]]:
    rows = mdb.executeSqlFetch(
        f'''SELECT code, date
            FROM cn_stock_hist_data
            WHERE code IN ({','.join(['%s'] * len(codes))})
              AND date BETWEEN %s AND %s
            ORDER BY code, date''',
        tuple(codes + [start, end]),
    )
    out: dict[str, list[str]] = defaultdict(list)
    for code, date in rows or []:
        out[code].append(_date_str(date))
    return out


def next_trade_date(dates_by_code: dict[str, list[str]], code: str, date: str) -> str | None:
    for d in dates_by_code.get(code, []):
        if d > date:
            return d
    return None


def fetch_ten_features(dates: list[str], target_codes: set[str]) -> dict[tuple[str, str], dict[str, Any]]:
    out: dict[tuple[str, str], dict[str, Any]] = {}
    sql = """
    WITH ctx AS (
        SELECT DISTINCT date, code
        FROM cn_stock_minute_bar
        WHERE date = %s
    ),
    prev_dates AS (
        SELECT c.date, c.code,
               (SELECT max(date) FROM cn_stock_minute_bar p
                WHERE p.code = c.code AND p.date < c.date) AS prev_d
        FROM ctx c
    ),
    now_window AS (
        SELECT p.date, p.code, p.prev_d,
               (array_agg(m.open ORDER BY m.time))[1] AS open_0931,
               (array_agg(m.close ORDER BY m.time DESC))[1] AS price_1000,
               max(m.high) AS high_1000,
               min(m.low) AS low_1000,
               sum(m.amount) AS amount_1000,
               count(*) AS bars
        FROM prev_dates p
        JOIN cn_stock_minute_bar m ON m.date = p.date AND m.code = p.code
        WHERE m.time BETWEEN '09:31' AND '10:00'
        GROUP BY p.date, p.code, p.prev_d
    ),
    prev_window AS (
        SELECT p.date, p.code, sum(m.amount) AS prev_amount_1000
        FROM prev_dates p
        JOIN cn_stock_minute_bar m ON m.date = p.prev_d AND m.code = p.code
        WHERE m.time BETWEEN '09:31' AND '10:00'
        GROUP BY p.date, p.code
    ),
    hist AS (
        SELECT n.date, n.code,
               pc.close AS prev_close,
               hm.ma5, hm.ma10, hm.ma15, hm.high_30d
        FROM now_window n
        LEFT JOIN LATERAL (
            SELECT close
            FROM cn_stock_minute_bar m
            WHERE m.code = n.code AND m.date = n.prev_d AND m.time >= '09:31'
            ORDER BY m.time DESC
            LIMIT 1
        ) pc ON true
        LEFT JOIN LATERAL (
            SELECT avg(close) FILTER (WHERE rn <= 5) AS ma5,
                   avg(close) FILTER (WHERE rn <= 10) AS ma10,
                   avg(close) FILTER (WHERE rn <= 15) AS ma15,
                   max(high) FILTER (WHERE rn <= 30) AS high_30d
            FROM (
                SELECT close, high, row_number() OVER (ORDER BY date DESC) AS rn
                FROM cn_stock_hist_data h
                WHERE h.code = n.code AND h.date < n.date
                ORDER BY date DESC
                LIMIT 30
            ) t
        ) hm ON true
    ),
    base AS (
        SELECT n.date, n.code, t.trade_theme,
               n.price_1000, n.high_1000, n.low_1000, n.amount_1000,
               p.prev_amount_1000, h.prev_close, h.ma5, h.ma10, h.ma15, h.high_30d,
               ((n.price_1000 / NULLIF(h.prev_close, 0) - 1) * 100) AS ret_1000,
               ((n.price_1000 - n.low_1000) / NULLIF(n.high_1000 - n.low_1000, 0) * 100) AS pos_1000,
               ((n.high_1000 - n.price_1000) / NULLIF(n.high_1000, 0) * 100) AS pullback_1000,
               (n.amount_1000 / NULLIF(p.prev_amount_1000, 0)) AS amount_ratio_1000
        FROM now_window n
        JOIN hist h ON h.date = n.date AND h.code = n.code
        LEFT JOIN prev_window p ON p.date = n.date AND p.code = n.code
        LEFT JOIN cn_stock_trade_theme t ON t.code = n.code
        WHERE n.bars >= 5 AND h.prev_close IS NOT NULL
    ),
    ranked AS (
        SELECT b.*,
               row_number() OVER (
                   PARTITION BY b.date, b.trade_theme
                   ORDER BY b.ret_1000 DESC NULLS LAST
               ) AS theme_rank,
               count(*) FILTER (WHERE b.ret_1000 >= 2)
                   OVER (PARTITION BY b.date, b.trade_theme) AS theme_strong_count,
               avg(b.ret_1000) FILTER (
                   WHERE b.ret_1000 IS NOT NULL
               ) OVER (PARTITION BY b.date, b.trade_theme) AS theme_avg_ret
        FROM base b
        WHERE b.trade_theme IS NOT NULL AND b.trade_theme <> ''
    )
    SELECT date, code, trade_theme, price_1000, ret_1000, pos_1000,
           pullback_1000, amount_ratio_1000, theme_rank,
           theme_strong_count, theme_avg_ret, ma5, ma10, ma15, high_30d
    FROM ranked
    WHERE code = ANY(%s)
    """
    with mdb.get_connection() as conn:
        with conn.cursor() as cur:
            for d in dates:
                cur.execute(sql, (d, list(target_codes)))
                cols = [x[0] for x in cur.description]
                for row in cur.fetchall():
                    item = {k: _json_number(v) for k, v in zip(cols, row)}
                    code = item['code']
                    date = _date_str(item['date'])
                    ma_vals = [_f(item.get(k)) for k in ('ma5', 'ma10', 'ma15') if _f(item.get(k)) > 0]
                    price = _f(item.get('price_1000'))
                    high_30d = _f(item.get('high_30d'))
                    item['ma_converge_pct'] = (
                        (max(ma_vals) - min(ma_vals)) / price * 100
                        if ma_vals and price else None
                    )
                    item['ma_bull'] = (
                        _f(item.get('ma5')) >= _f(item.get('ma10')) >= _f(item.get('ma15'))
                        if ma_vals else False
                    )
                    item['distance_to_30d_high'] = (
                        (high_30d / price - 1) * 100 if high_30d and price else None
                    )
                    out[(code, date)] = item
    return out


def bars_window(bars: list[dict[str, Any]], start: str = '09:31', end: str = '15:00') -> list[dict[str, Any]]:
    return [b for b in bars if start <= b['time'] <= end]


def close_at_or_before(bars: list[dict[str, Any]], hhmm: str) -> float | None:
    items = [b for b in bars if b['time'] <= hhmm]
    return items[-1]['close'] if items else None


def classify_pattern(feature: dict[str, Any] | None) -> str:
    if not feature:
        return '数据不足'
    ret = _f(feature.get('ret_1000'))
    pos = _f(feature.get('pos_1000'))
    pullback = _f(feature.get('pullback_1000'))
    rank = int(feature.get('theme_rank') or 999)
    strong = int(feature.get('theme_strong_count') or 0)
    dist_high = _f(feature.get('distance_to_30d_high'), 999)
    ma_bull = bool(feature.get('ma_bull'))
    ma_conv = _f(feature.get('ma_converge_pct'), 99)

    if rank <= 6 and strong >= 4 and ret >= 5 and pos >= 80 and pullback <= 2.5:
        return '主线核心追强'
    if rank <= 8 and strong >= 3 and -2 <= ret <= 6 and dist_high >= 8 and ma_bull and ma_conv <= 10:
        return '主线低位突破'
    if -5 <= ret <= 3 and pos >= 45 and strong >= 2:
        return '修复反包观察'
    if ret >= 8 and (pos < 75 or pullback > 3):
        return '高位追强风险'
    if strong < 2 or rank > 15:
        return '非主线/跟风'
    return '主线观察'


def classify_selection(row: dict[str, Any]) -> str:
    max_opportunity = max(
        _f(row.get('buy_day_after_10_max_pct'), -999),
        _f(row.get('next_10_max_pct'), -999),
        _f(row.get('buy_day_close_pct'), -999),
        _f(row.get('next_open_pct'), -999),
    )
    close_like = max(_f(row.get('buy_day_close_pct'), -999), _f(row.get('next_open_pct'), -999), _f(row.get('next_10_close_pct'), -999))
    max_pain = min(_f(row.get('buy_day_after_10_min_pct'), 999), _f(row.get('next_10_min_pct'), 999))
    if close_like >= 1.0 or max_opportunity >= 3.0:
        return '选股成立'
    if max_opportunity >= 1.5 and max_pain > -4.0:
        return '给过机会'
    if close_like >= -1.0:
        return '震荡不确定'
    return '选股失败'


def classify_execution(row: dict[str, Any]) -> str:
    actual = row.get('actual_return_pct')
    if actual is None:
        return '未卖出'
    actual = _f(actual)
    sell_close = row.get('sell_day_close_return_pct')
    sell_pos = row.get('sell_price_range_pos')
    parts: list[str] = []
    if sell_pos is not None and _f(sell_pos) <= 20:
        parts.append('卖在低位')
    if sell_close is not None and _f(sell_close) - actual >= 3:
        parts.append('卖点拖累')
    if actual < 0 and _f(row.get('buy_day_after_10_max_pct'), -999) >= 3:
        parts.append('盘中给过盈利')
    buy_vs_10 = row.get('actual_buy_vs_1000_pct')
    if buy_vs_10 is not None and _f(buy_vs_10) >= 3:
        parts.append('买点偏追高')
    if not parts and actual >= 0:
        return '执行盈利'
    if not parts:
        return '正常亏损'
    return ' / '.join(parts)


def enrich_details(
    buys: list[dict[str, Any]],
    allocations: list[dict[str, Any]],
    daily: dict[tuple[str, str], dict[str, Any]],
    minutes: dict[tuple[str, str], list[dict[str, Any]]],
    dates_by_code: dict[str, list[str]],
    ten_features: dict[tuple[str, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    alloc_by_buy: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for a in allocations:
        alloc_by_buy[a['buy_trade_id']].append(a)

    out: list[dict[str, Any]] = []
    for b in buys:
        code = b['code']
        buy_date = b['buy_date']
        entry = _f(b['buy_cost_price'])
        feature = ten_features.get((code, buy_date))
        buy_bars = minutes.get((code, buy_date), [])
        after_10 = bars_window(buy_bars, '10:01', '15:00')
        day = daily.get((code, buy_date), {})
        next_d = next_trade_date(dates_by_code, code, buy_date)
        next_bars = minutes.get((code, next_d), []) if next_d else []
        next_10 = bars_window(next_bars, '09:31', '10:00')

        row = {
            **b,
            'trade_theme': feature.get('trade_theme') if feature else '',
            'pattern_type': classify_pattern(feature),
            'price_1000': feature.get('price_1000') if feature else None,
            'ret_1000': feature.get('ret_1000') if feature else None,
            'pos_1000': feature.get('pos_1000') if feature else None,
            'pullback_1000': feature.get('pullback_1000') if feature else None,
            'amount_ratio_1000': feature.get('amount_ratio_1000') if feature else None,
            'theme_rank': feature.get('theme_rank') if feature else None,
            'theme_strong_count': feature.get('theme_strong_count') if feature else None,
            'theme_avg_ret': feature.get('theme_avg_ret') if feature else None,
            'ma_converge_pct': feature.get('ma_converge_pct') if feature else None,
            'ma_bull': feature.get('ma_bull') if feature else None,
            'distance_to_30d_high': feature.get('distance_to_30d_high') if feature else None,
            'next_trade_date': next_d,
            'buy_day_close_pct': ((day.get('close') / entry - 1) * 100) if day.get('close') and entry else None,
            'buy_day_high_pct': ((day.get('high') / entry - 1) * 100) if day.get('high') and entry else None,
            'buy_day_low_pct': ((day.get('low') / entry - 1) * 100) if day.get('low') and entry else None,
            'buy_day_after_10_max_pct': ((max(x['high'] for x in after_10) / entry - 1) * 100) if after_10 and entry else None,
            'buy_day_after_10_min_pct': ((min(x['low'] for x in after_10) / entry - 1) * 100) if after_10 and entry else None,
            'next_open_pct': ((next_bars[0]['open'] / entry - 1) * 100) if next_bars and entry else None,
            'next_10_close_pct': ((close_at_or_before(next_bars, '10:00') / entry - 1) * 100) if next_bars and close_at_or_before(next_bars, '10:00') and entry else None,
            'next_10_max_pct': ((max(x['high'] for x in next_10) / entry - 1) * 100) if next_10 and entry else None,
            'next_10_min_pct': ((min(x['low'] for x in next_10) / entry - 1) * 100) if next_10 and entry else None,
            'actual_buy_vs_1000_pct': ((entry / feature.get('price_1000') - 1) * 100) if feature and feature.get('price_1000') and entry else None,
        }

        sell_close_proceeds = 0.0
        sell_high_proceeds = 0.0
        sell_low_proceeds = 0.0
        sell_range_pos_num = 0.0
        sell_range_pos_den = 0.0
        for a in alloc_by_buy.get(b['trade_id'], []):
            sday = daily.get((code, a['sell_date']))
            if not sday:
                continue
            qty = _f(a['qty'])
            sell_close_proceeds += sday['close'] * qty
            sell_high_proceeds += sday['high'] * qty
            sell_low_proceeds += sday['low'] * qty
            day_span = sday['high'] - sday['low']
            if day_span > 0:
                sell_range_pos_num += ((_f(a['sell_net_price']) - sday['low']) / day_span * 100) * qty
                sell_range_pos_den += qty
        matched_cost = _f(b.get('actual_cost_matched'))
        if matched_cost > 0:
            row['sell_day_close_return_pct'] = (sell_close_proceeds / matched_cost - 1) * 100 if sell_close_proceeds else None
            row['sell_day_high_return_pct'] = (sell_high_proceeds / matched_cost - 1) * 100 if sell_high_proceeds else None
            row['sell_day_low_return_pct'] = (sell_low_proceeds / matched_cost - 1) * 100 if sell_low_proceeds else None
        else:
            row['sell_day_close_return_pct'] = None
            row['sell_day_high_return_pct'] = None
            row['sell_day_low_return_pct'] = None
        row['sell_price_range_pos'] = sell_range_pos_num / sell_range_pos_den if sell_range_pos_den else None
        row['selection_result'] = classify_selection(row)
        row['execution_result'] = classify_execution(row)
        out.append(row)
    return out


def summarize(rows: list[dict[str, Any]]) -> str:
    closed = [r for r in rows if r.get('actual_return_pct') is not None]
    selected = [r for r in rows if r['selection_result'] in ('选股成立', '给过机会')]
    actual_wins = [r for r in closed if _f(r['actual_return_pct']) > 0]
    close_wins = [r for r in rows if r.get('buy_day_close_pct') is not None and _f(r['buy_day_close_pct']) > 0]
    next_wins = [r for r in rows if r.get('next_open_pct') is not None and _f(r['next_open_pct']) > 0]
    dragged = [r for r in closed if '卖点拖累' in r['execution_result'] or '卖在低位' in r['execution_result']]

    by_pattern: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_pattern[r['pattern_type']].append(r)

    lines = [
        '# 6月交易模式归因',
        '',
        '## 总览',
        f'- 买入笔数：{len(rows)}',
        f'- 已闭合笔数：{len(closed)}',
        f'- 真实盈利笔数：{len(actual_wins)} / {len(closed)}',
        f'- 买入后按当天收盘盈利：{len(close_wins)} / {len(rows)}',
        f'- 买入后按次日开盘盈利：{len(next_wins)} / {len([r for r in rows if r.get("next_open_pct") is not None])}',
        f'- 选股成立或给过机会：{len(selected)} / {len(rows)}',
        f'- 疑似卖点拖累/卖在低位：{len(dragged)} / {len(closed)}',
        '',
        '## 分模式',
    ]
    for pattern, items in sorted(by_pattern.items(), key=lambda kv: -len(kv[1])):
        wins = sum(1 for r in items if r['selection_result'] in ('选股成立', '给过机会'))
        actual = [r for r in items if r.get('actual_return_pct') is not None]
        actual_win = sum(1 for r in actual if _f(r.get('actual_return_pct')) > 0)
        lines.append(
            f'- {pattern}：{len(items)} 笔，选股成立/给机会 {wins}/{len(items)}，真实盈利 {actual_win}/{len(actual)}'
        )

    lines.extend(['', '## 真实亏损但可能不是选股失败'])
    suspects = [
        r for r in closed
        if _f(r.get('actual_return_pct')) < 0
        and r['selection_result'] in ('选股成立', '给过机会', '震荡不确定')
    ]
    suspects.sort(key=lambda r: (_f(r.get('sell_day_close_return_pct'), -99) - _f(r.get('actual_return_pct'), -99)), reverse=True)
    for r in suspects[:12]:
        lines.append(
            f"- {r['buy_date']} {r['code']} {r['name']}：实际{_pct(r['actual_return_pct'])}，"
            f"当天收盘{_pct(r['buy_day_close_pct'])}，次日开盘{_pct(r['next_open_pct'])}，"
            f"卖出日收盘{_pct(r['sell_day_close_return_pct'])}，{r['selection_result']}，{r['execution_result']}"
        )
    return '\n'.join(lines) + '\n'


def write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    fields = [
        'trade_id', 'buy_date', 'code', 'name', 'trade_theme', 'pattern_type',
        'selection_result', 'execution_result',
        'buy_price', 'buy_cost_price', 'price_1000', 'actual_buy_vs_1000_pct',
        'ret_1000', 'pos_1000', 'pullback_1000', 'amount_ratio_1000',
        'theme_rank', 'theme_strong_count', 'theme_avg_ret',
        'ma_converge_pct', 'ma_bull', 'distance_to_30d_high',
        'buy_day_close_pct', 'buy_day_high_pct', 'buy_day_low_pct',
        'buy_day_after_10_max_pct', 'buy_day_after_10_min_pct',
        'next_trade_date', 'next_open_pct', 'next_10_close_pct',
        'next_10_max_pct', 'next_10_min_pct',
        'actual_sell_dates', 'actual_return_pct', 'sell_day_close_return_pct',
        'sell_day_high_return_pct', 'sell_day_low_return_pct',
        'sell_price_range_pos',
    ]
    with path.open('w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        for r in rows:
            writer.writerow(r)


def main():
    parser = argparse.ArgumentParser(description='分析交割单中的真实选股模式')
    parser.add_argument('--xlsx', default='/Users/x6-mac/Downloads/6月交易记录.xlsx')
    parser.add_argument('--output-dir', default='outputs/trade_pattern_20260630')
    args = parser.parse_args()

    trades = load_trades(Path(args.xlsx))
    buys, allocations, unmatched_sells, open_lots = build_fifo(trades)
    codes = sorted({b['code'] for b in buys} | {a['code'] for a in allocations})
    min_date = min(b['buy_date'] for b in buys)
    max_date = max([b['buy_date'] for b in buys] + [a['sell_date'] for a in allocations])
    end_dt = (dt.date.fromisoformat(max_date) + dt.timedelta(days=10)).strftime('%Y-%m-%d')

    dates_by_code = fetch_trade_dates(codes, min_date, end_dt)
    needed_dates = set()
    for b in buys:
        needed_dates.add(b['buy_date'])
        nd = next_trade_date(dates_by_code, b['code'], b['buy_date'])
        if nd:
            needed_dates.add(nd)
    for a in allocations:
        needed_dates.add(a['sell_date'])

    daily = fetch_daily(codes, min_date, end_dt)
    minutes = fetch_minutes(codes, sorted(needed_dates))
    ten_features = fetch_ten_features(sorted({b['buy_date'] for b in buys}), {b['code'] for b in buys})
    rows = enrich_details(buys, allocations, daily, minutes, dates_by_code, ten_features)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / 'selection_attribution.csv'
    md_path = output_dir / 'summary.md'
    json_path = output_dir / 'selection_attribution.json'
    write_csv(rows, csv_path)
    md_path.write_text(summarize(rows), encoding='utf-8')
    json_path.write_text(json.dumps({
        'source': args.xlsx,
        'trade_count': len(trades),
        'buy_count': len(buys),
        'allocation_count': len(allocations),
        'unmatched_sell_count': len(unmatched_sells),
        'open_lot_count': len(open_lots),
        'rows': rows,
    }, ensure_ascii=False, indent=2, default=_json_number), encoding='utf-8')

    print(f'交易记录: {len(trades)} 行，买入: {len(buys)} 笔，配对卖出: {len(allocations)} 条')
    print(f'输出: {csv_path}')
    print(f'汇总: {md_path}')


if __name__ == '__main__':
    main()
