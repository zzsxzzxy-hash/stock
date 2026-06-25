#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全量同步东方财富板块（行业+概念+地域）→ cn_stock_sector_map
用法：python3 -m instock.job.sync_sector_map
"""
import logging
import os
import sys
import time

project_path = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, project_path)

import requests
import instock.lib.database as mdb

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s', force=True)
log = logging.getLogger(__name__)

_HEADERS = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'}
_BASE_URL = 'https://push2.eastmoney.com/api/qt/clist/get'
_UT       = 'bd1d9ddb04089700cf9c27f6f7426281'
_PAGE_SIZE = 100   # 东财每页上限

# 三类板块：行业 t:2 / 概念 t:3 / 地域 t:1
_SECTOR_TYPES = [
    ('行业', 'm:90+t:2+f:!50'),
    ('概念', 'm:90+t:3+f:!50'),
    ('地域', 'm:90+t:1+f:!50'),
]


def _fetch_all_pages(fs: str, fields: str = 'f12,f14') -> list[dict]:
    """带翻页地拉取所有板块/成分股，返回 diff 列表"""
    result = []
    pn = 1
    while True:
        params = {
            'pn': pn, 'pz': _PAGE_SIZE, 'po': 1, 'np': 1,
            'ut': _UT, 'fltt': 2, 'invt': 2, 'fid': 'f3',
            'fs': fs, 'fields': fields,
        }
        r = requests.get(_BASE_URL, params=params, headers=_HEADERS, timeout=15)
        r.raise_for_status()
        body = r.json().get('data') or {}
        diff = body.get('diff') or []
        result.extend(diff)
        total = body.get('total', 0)
        if len(result) >= total or not diff:
            break
        pn += 1
        time.sleep(0.1)
    return result


def _fetch_sector_list(fs: str) -> list[dict]:
    """获取某类全部板块，返回 [{code, name}]"""
    rows = _fetch_all_pages(fs)
    return [{'code': d['f12'], 'name': d['f14']} for d in rows if d.get('f12') and d.get('f14')]


def _fetch_sector_stocks(sector_code: str, sector_name: str) -> list[tuple]:
    """获取板块全部成分股，返回 [(stock_code, sector_name)]"""
    rows = _fetch_all_pages(f'b:{sector_code}+f:!50', fields='f12')
    return [(d['f12'], sector_name) for d in rows if d.get('f12')]


def run():
    log.info("开始全量同步板块映射（行业+概念+地域）...")

    # ── 1. 收集三类板块列表 ──────────────────────────────────────────────────
    all_sectors: list[dict] = []
    for type_name, fs in _SECTOR_TYPES:
        sectors = _fetch_sector_list(fs)
        log.info(f"  {type_name}板块：{len(sectors)} 个")
        all_sectors.extend(sectors)

    log.info(f"板块总计：{len(all_sectors)} 个，开始采集成分股...")

    # ── 2. 逐板块爬取成分股 ──────────────────────────────────────────────────
    all_rows: list[tuple] = []
    for i, ind in enumerate(all_sectors):
        try:
            stocks = _fetch_sector_stocks(ind['code'], ind['name'])
            all_rows.extend(stocks)
            if (i + 1) % 50 == 0 or (i + 1) == len(all_sectors):
                log.info(f"  进度 {i+1}/{len(all_sectors)}，已采集 {len(all_rows)} 条映射")
            time.sleep(0.12)
        except Exception as e:
            log.warning(f"  板块 {ind['name']}({ind['code']}) 采集失败: {e}")

    if not all_rows:
        log.error("未采集到任何数据，退出")
        return

    # 去重（同一 code+sector 可能因行业/概念重叠出现两次）
    unique_rows = list({(code, sector) for code, sector in all_rows})
    log.info(f"去重后：{len(unique_rows)} 条（原 {len(all_rows)} 条）")

    # ── 3. 全量写入数据库（TRUNCATE + INSERT）────────────────────────────────
    with mdb.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute('TRUNCATE TABLE cn_stock_sector_map')
            # 分批 INSERT，避免单次 executemany 太大
            batch = 2000
            for start in range(0, len(unique_rows), batch):
                cur.executemany(
                    'INSERT INTO cn_stock_sector_map (code, sector) VALUES (%s, %s)',
                    unique_rows[start:start + batch]
                )
    log.info(f"数据库写入完成：{len(unique_rows)} 条")

    # ── 4. 刷新 Redis 缓存 ───────────────────────────────────────────────────
    try:
        from instock.core.volume_pre_calc import _cache_sectors
        _cache_sectors()
        log.info("Redis 板块缓存已刷新")
    except Exception as e:
        log.warning(f"Redis 缓存刷新失败: {e}")

    log.info("板块映射全量同步完成")


if __name__ == '__main__':
    run()
