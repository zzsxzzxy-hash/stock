#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
重建“一股一交易主线”映射。

默认 dry-run：只生成审核 CSV，不改原始 cn_stock_sector_map。
--apply：写入 cn_stock_trade_theme，并刷新 Redis 板块缓存。
--replace-sector-map：在 --apply 后把 cn_stock_sector_map 替换为一股一板块。
"""

import argparse
import csv
import datetime as dt
import json
import logging
import math
import os
import re
import sys
from collections import defaultdict

project_path = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, project_path)

import instock.lib.database as mdb

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s', force=True)
log = logging.getLogger(__name__)


NOISE_KEYWORDS = [
    '板块', '重仓', '融资融券', '转融券', '沪股通', '深股通', '陆股通',
    'MSCI', '富时', '标普', '中证', '上证', '深成', '创业板',
    '昨日', '涨停', '连板', '炸板', '破净', '破发', '低价', '高价',
    '百元股', '中字头', '证金', 'QFII', '养老金', '社保', '预盈',
    '预增', '预亏', 'ST', '含H股', 'AH股', '参股', '举牌',
    '风格', '大盘', '小盘', '微盘', '价值股', '成长', '超跌',
    '年报', '一季报', '半年报', '三季报', '预减', '北交所概念',
    '央国企改革', '国企改革', '一带一路', '创投',
]

GENERIC_SECTORS = {
    '电子', '机械设备', '基础化工', '医药生物', '计算机', '通信',
    '电力设备', '汽车', '有色金属', '国防军工', '公用事业',
    '交通运输', '商贸零售', '食品饮料', '建筑材料', '建筑装饰',
    '传媒', '银行', '非银金融', '房地产', '农林牧渔', '煤炭',
    '钢铁', '石油石化', '环保', '美容护理', '纺织服饰',
}


def ensure_tables():
    sql = """
    CREATE TABLE IF NOT EXISTS cn_stock_trade_theme (
        code             VARCHAR(6)   PRIMARY KEY,
        name             VARCHAR(80),
        trade_theme  VARCHAR(80)  NOT NULL,
        confidence       NUMERIC(8,2) DEFAULT 0,
        source           VARCHAR(30)  DEFAULT 'algorithm',
        reason           TEXT,
        candidate_count  INTEGER      DEFAULT 0,
        updated_at       TIMESTAMP    DEFAULT NOW()
    );
    CREATE INDEX IF NOT EXISTS idx_trade_theme_name
        ON cn_stock_trade_theme (trade_theme);
    """
    with mdb.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)


def latest_trade_date() -> str:
    rows = mdb.executeSqlFetch('SELECT MAX(date) FROM cn_stock_hist_data')
    if rows and rows[0][0]:
        d = rows[0][0]
        return d.strftime('%Y-%m-%d') if hasattr(d, 'strftime') else str(d)
    return dt.date.today().strftime('%Y-%m-%d')


def load_names() -> dict[str, str]:
    rows = mdb.executeSqlFetch(
        '''SELECT DISTINCT ON (code) code, name
           FROM cn_stock_spot
           ORDER BY code, date DESC'''
    )
    return {r[0]: r[1] or r[0] for r in rows or []}


def normalize_sector(sector: str) -> str:
    s = (sector or '').strip()
    s = re.sub(r'[ⅠⅡⅢIV]+$', '', s)
    return s


def is_noise_sector(sector: str) -> bool:
    s = normalize_sector(sector).upper()
    if not s:
        return True
    return any(k.upper() in s for k in NOISE_KEYWORDS)


def sector_specificity_score(sector: str, member_count: int) -> float:
    s = normalize_sector(sector)
    score = 0.0
    if member_count < 3:
        return -99.0
    if member_count <= 12:
        score += 14
    elif member_count <= 35:
        score += 18
    elif member_count <= 80:
        score += 9
    else:
        score -= min(25, (member_count - 80) * 0.25)

    if s in GENERIC_SECTORS:
        score -= 8
    if any(x in s for x in ('概念', '设备', '材料', '芯片', '封装', '机器人', '光伏', '电池', '算力', 'CPO', 'AI')):
        score += 5
    if len(s) <= 2:
        score -= 4
    return score


def load_sector_map() -> tuple[dict[str, list[str]], dict[str, int]]:
    rows = mdb.executeSqlFetch('SELECT code, sector FROM cn_stock_sector_map')
    by_code: dict[str, list[str]] = defaultdict(list)
    counts: dict[str, int] = defaultdict(int)
    seen = set()
    for code, sector in rows or []:
        sector = normalize_sector(sector)
        if not code or not sector or is_noise_sector(sector):
            continue
        key = (code, sector)
        if key in seen:
            continue
        seen.add(key)
        by_code[code].append(sector)
        counts[sector] += 1
    return by_code, counts


def load_returns(end_date: str, days: int) -> tuple[list[str], dict[str, dict[str, float]]]:
    date_rows = mdb.executeSqlFetch(
        '''SELECT DISTINCT date
           FROM cn_stock_hist_data
           WHERE date <= %s
           ORDER BY date DESC
           LIMIT %s''',
        (end_date, days)
    )
    dates = sorted(str(r[0]) for r in date_rows or [])
    if not dates:
        return [], {}

    rows = mdb.executeSqlFetch(
        '''SELECT code, date, quote_change
           FROM cn_stock_hist_data
           WHERE date >= %s AND date <= %s''',
        (dates[0], dates[-1])
    )
    ret: dict[str, dict[str, float]] = defaultdict(dict)
    for code, d, chg in rows or []:
        ret[code][str(d)] = float(chg or 0)
    return dates, ret


def corr(xs: list[float], ys: list[float]) -> float:
    if len(xs) < 8 or len(ys) < 8:
        return 0.0
    mx = sum(xs) / len(xs)
    my = sum(ys) / len(ys)
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 0 or vy <= 0:
        return 0.0
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / math.sqrt(vx * vy)


def build_sector_daily(by_code: dict[str, list[str]], ret: dict[str, dict[str, float]], dates: list[str]):
    members: dict[str, list[str]] = defaultdict(list)
    for code, sectors in by_code.items():
        if code not in ret:
            continue
        for s in sectors:
            members[s].append(code)

    sector_daily: dict[str, dict[str, float]] = {}
    sector_strong_days: dict[str, set[str]] = {}
    for sector, codes in members.items():
        if len(codes) < 3:
            continue
        daily = {}
        strong_days = set()
        for d in dates:
            vals = [ret[c].get(d, 0.0) for c in codes if d in ret[c]]
            if not vals:
                continue
            vals.sort(reverse=True)
            top_n = min(5, max(3, len(vals) // 5))
            avg_top = sum(vals[:top_n]) / top_n
            daily[d] = avg_top
            if avg_top >= 2.0 or sum(1 for v in vals if v >= 2.0) >= 3:
                strong_days.add(d)
        sector_daily[sector] = daily
        sector_strong_days[sector] = strong_days
    return sector_daily, sector_strong_days


def choose_trade_theme(code: str, sectors: list[str], sector_counts: dict[str, int],
                       ret: dict[str, dict[str, float]], dates: list[str],
                       sector_daily: dict[str, dict[str, float]],
                       sector_strong_days: dict[str, set[str]]) -> tuple[str, float, str, list[dict]]:
    candidates = []
    stock_ret = ret.get(code, {})
    for sector in sectors:
        cnt = sector_counts.get(sector, 0)
        if sector not in sector_daily:
            continue

        xs, ys = [], []
        for d in dates:
            if d in stock_ret and d in sector_daily[sector]:
                xs.append(stock_ret[d])
                ys.append(sector_daily[sector][d])
        c = corr(xs, ys)

        strong_days = sector_strong_days.get(sector, set())
        if strong_days:
            follow = sum(1 for d in strong_days if stock_ret.get(d, 0) >= 1.0) / len(strong_days)
            lead = sum(1 for d in strong_days if stock_ret.get(d, 0) >= 3.0) / len(strong_days)
        else:
            follow = 0.0
            lead = 0.0

        specificity = sector_specificity_score(sector, cnt)
        score = specificity + c * 28 + follow * 22 + lead * 18 + min(10, len(strong_days) * 0.6)
        candidates.append({
            'sector': sector,
            'score': round(score, 2),
            'corr': round(c, 3),
            'follow': round(follow, 3),
            'lead': round(lead, 3),
            'member_count': cnt,
            'strong_days': len(strong_days),
            'specificity': round(specificity, 2),
        })

    candidates.sort(key=lambda x: x['score'], reverse=True)
    if candidates:
        best = candidates[0]
        confidence = max(0.0, min(100.0, 45 + best['score']))
        reason = (
            f"score={best['score']}; corr={best['corr']}; follow={best['follow']}; "
            f"lead={best['lead']}; members={best['member_count']}; strong_days={best['strong_days']}"
        )
        return best['sector'], round(confidence, 2), reason, candidates[:8]

    # 回退：没有足够行情相关性时，选最具体的非噪音板块。
    fallback = sorted(
        [{'sector': s, 'score': sector_specificity_score(s, sector_counts.get(s, 0)),
          'member_count': sector_counts.get(s, 0)}
         for s in sectors],
        key=lambda x: x['score'],
        reverse=True,
    )
    if fallback:
        best = fallback[0]
        return best['sector'], max(20.0, min(55.0, 35 + best['score'])), 'fallback=specificity', fallback[:8]
    return '', 0.0, 'no_valid_sector', []


def rebuild(end_date: str, days: int) -> list[dict]:
    names = load_names()
    by_code, sector_counts = load_sector_map()
    dates, ret = load_returns(end_date, days)
    sector_daily, sector_strong_days = build_sector_daily(by_code, ret, dates)

    results = []
    for code in sorted(by_code):
        sector, confidence, reason, candidates = choose_trade_theme(
            code, by_code[code], sector_counts, ret, dates, sector_daily, sector_strong_days
        )
        if not sector:
            continue
        results.append({
            'code': code,
            'name': names.get(code, code),
            'trade_theme': sector,
            'confidence': confidence,
            'source': 'algorithm',
            'reason': reason,
            'candidate_count': len(by_code[code]),
            'candidates': json.dumps(candidates, ensure_ascii=False),
            'all_sectors': ' / '.join(by_code[code]),
        })
    return results


def write_csv(rows: list[dict], path: str):
    fields = [
        'code', 'name', 'trade_theme', 'confidence', 'reason',
        'candidate_count', 'candidates', 'all_sectors',
    ]
    with open(path, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
        w.writeheader()
        w.writerows(rows)
    log.info("审核 CSV 已生成: %s (%s 行)", path, len(rows))


def apply_table(rows: list[dict]):
    ensure_tables()
    with mdb.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute('TRUNCATE TABLE cn_stock_trade_theme')
            batch = 1000
            values = [
                (
                    r['code'], r['name'], r['trade_theme'], r['confidence'],
                    r['source'], r['reason'], r['candidate_count'],
                )
                for r in rows
            ]
            for i in range(0, len(values), batch):
                cur.executemany(
                    '''INSERT INTO cn_stock_trade_theme
                       (code, name, trade_theme, confidence, source, reason, candidate_count)
                       VALUES (%s,%s,%s,%s,%s,%s,%s)''',
                    values[i:i + batch]
                )
    log.info("cn_stock_trade_theme 写入完成: %s 行", len(rows))


def replace_sector_map(rows: list[dict]):
    with mdb.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                '''CREATE TABLE IF NOT EXISTS cn_stock_sector_map_raw_backup
                   AS TABLE cn_stock_sector_map WITH NO DATA'''
            )
            cur.execute('TRUNCATE TABLE cn_stock_sector_map_raw_backup')
            cur.execute('INSERT INTO cn_stock_sector_map_raw_backup SELECT * FROM cn_stock_sector_map')
            cur.execute('TRUNCATE TABLE cn_stock_sector_map')
            cur.executemany(
                'INSERT INTO cn_stock_sector_map (code, sector) VALUES (%s, %s)',
                [(r['code'], r['trade_theme']) for r in rows]
            )
    log.warning("cn_stock_sector_map 已替换为一股一交易主线；原始映射备份在 cn_stock_sector_map_raw_backup")


def refresh_sector_cache():
    try:
        from instock.core.volume_pre_calc import _cache_sectors
        _cache_sectors()
        log.info("Redis 板块缓存已刷新")
    except Exception as e:
        log.warning("Redis 板块缓存刷新失败: %s", e)


def main():
    parser = argparse.ArgumentParser(description='重建一股一交易主线映射')
    parser.add_argument('--end-date', default=latest_trade_date())
    parser.add_argument('--days', type=int, default=60)
    parser.add_argument('--csv', default='trade_theme_review.csv')
    parser.add_argument('--apply', action='store_true', help='写入 cn_stock_trade_theme')
    parser.add_argument('--replace-sector-map', action='store_true', help='把 cn_stock_sector_map 替换成一股一板块')
    args = parser.parse_args()

    ensure_tables()
    log.info("开始重建交易主线 end_date=%s days=%s", args.end_date, args.days)
    rows = rebuild(args.end_date, args.days)
    write_csv(rows, args.csv)

    if args.apply:
        apply_table(rows)
        if args.replace_sector_map:
            replace_sector_map(rows)
        refresh_sector_cache()
    else:
        log.info("dry-run 完成。确认 CSV 后可加 --apply 写入新表。")


if __name__ == '__main__':
    main()
