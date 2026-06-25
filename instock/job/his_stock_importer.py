#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
his-stock 历史1分钟K线数据导入器
文件格式：市场#股票代码.txt（SH#688001.txt / SZ#000001.txt）
数据格式（制表符分隔）：
  日期         时间   开盘    最高    最低    收盘    成交量      成交额
  2026/06/01  0931  71.50  73.99  71.50  72.96  363800  26402026.00
"""
import os
import logging
import datetime
from typing import Callable

import instock.lib.database as mdb

log = logging.getLogger(__name__)

HIS_STOCK_DIR = os.environ.get(
    'HIS_STOCK_DIR',
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'his-stock')
)

TABLE = 'cn_stock_minute_bar'
BATCH_SIZE = 2000   # 每批次 INSERT 行数


def _parse_time(t_str: str) -> str:
    """'0931' → '09:31'"""
    t = t_str.strip().zfill(4)
    return f'{t[:2]}:{t[2:]}'


def _parse_date(d_str: str) -> str:
    """'2026/06/01' → '2026-06-01'"""
    return d_str.strip().replace('/', '-')


def _parse_file(filepath: str) -> tuple[str, list[tuple]]:
    """
    解析单个txt文件，返回 (code, rows列表)
    rows: list of (date, time, code, open, close, high, low, volume, amount, pre_close)
    pre_close 用前一分钟的 close 填充，第一行用当行 open 填充
    """
    filename = os.path.basename(filepath)
    # 从文件名提取代码：SH#688001.txt → 688001
    code = filename.split('#')[-1].replace('.txt', '').strip()

    rows = []
    prev_close = None

    try:
        with open(filepath, encoding='gbk', errors='replace') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                # 跳过标题行（包含汉字的行）
                if not line[0].isdigit():
                    continue
                parts = line.split('\t')
                if len(parts) < 8:
                    continue
                try:
                    date_str  = _parse_date(parts[0])
                    time_str  = _parse_time(parts[1])
                    open_p    = float(parts[2])
                    high_p    = float(parts[3])
                    low_p     = float(parts[4])
                    close_p   = float(parts[5])
                    volume    = float(parts[6]) / 100   # 原始单位：股，统一转为手
                    amount    = float(parts[7].rstrip('#').strip()) / 100  # 原始单位：元，统一转为百元（与XTick一致）

                    # pre_close：第一行用open，后续用上一分钟的close
                    pre_close = prev_close if prev_close is not None else open_p
                    prev_close = close_p

                    rows.append((
                        date_str, time_str, code,
                        open_p, close_p, high_p, low_p,
                        volume, amount, pre_close
                    ))
                except (ValueError, IndexError):
                    continue
    except Exception as e:
        log.warning(f"解析文件 {filename} 失败: {e}")

    return code, rows


def _insert_batch(rows: list[tuple]) -> int:
    """批量 upsert 到 cn_stock_minute_bar，返回写入行数"""
    if not rows:
        return 0
    sql = """
        INSERT INTO cn_stock_minute_bar
            (date, time, code, open, close, high, low, volume, amount, pre_close)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (date, time, code) DO UPDATE SET
            open      = EXCLUDED.open,
            close     = EXCLUDED.close,
            high      = EXCLUDED.high,
            low       = EXCLUDED.low,
            volume    = EXCLUDED.volume,
            amount    = EXCLUDED.amount,
            pre_close = EXCLUDED.pre_close
    """
    with mdb.get_connection() as conn:
        with conn.cursor() as cur:
            cur.executemany(sql, rows)
    return len(rows)


def import_all(push: Callable[[str], None] = None, his_dir: str = None) -> dict:
    """
    导入 his-stock 目录下所有 txt 文件到数据库
    push: 实时日志回调函数，None 则只打 log
    返回: {'total_files': n, 'total_rows': n, 'skipped': n, 'errors': n}
    """
    def _log(msg: str):
        log.info(msg)
        if push:
            push(msg)

    src_dir = his_dir or HIS_STOCK_DIR
    if not os.path.isdir(src_dir):
        _log(f'❌ 目录不存在: {src_dir}')
        return {'total_files': 0, 'total_rows': 0, 'skipped': 0, 'errors': 0}

    files = sorted([f for f in os.listdir(src_dir) if f.endswith('.txt')])
    total = len(files)
    _log(f'📂 目录: {src_dir}')
    _log(f'📋 共发现 {total} 个文件，开始导入...')

    total_rows = 0
    skipped    = 0
    errors     = 0
    start_time = datetime.datetime.now()

    for idx, fname in enumerate(files, 1):
        fpath = os.path.join(src_dir, fname)
        try:
            code, rows = _parse_file(fpath)
            if not rows:
                _log(f'  [{idx}/{total}] {fname} — 无有效数据，跳过')
                skipped += 1
                continue

            # 分批插入
            inserted = 0
            for i in range(0, len(rows), BATCH_SIZE):
                batch = rows[i:i + BATCH_SIZE]
                inserted += _insert_batch(batch)
            total_rows += inserted

            # 每10个文件推一次日志，避免日志刷屏
            if idx % 10 == 0 or idx == total:
                elapsed = (datetime.datetime.now() - start_time).seconds
                _log(f'  [{idx}/{total}] {fname} — {len(rows)}行 | 累计{total_rows}行 | 耗时{elapsed}s')
            else:
                log.debug(f'[{idx}/{total}] {fname} {len(rows)}行')

        except Exception as e:
            errors += 1
            _log(f'  [{idx}/{total}] ❌ {fname} 失败: {e}')

    elapsed = (datetime.datetime.now() - start_time).seconds
    _log(f'')
    _log(f'✅ 导入完成！')
    _log(f'   文件数: {total}，成功: {total - skipped - errors}，跳过: {skipped}，失败: {errors}')
    _log(f'   总写入: {total_rows} 行')
    _log(f'   耗时: {elapsed}s')

    return {
        'total_files': total,
        'total_rows':  total_rows,
        'skipped':     skipped,
        'errors':      errors,
    }


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
    import_all()
