#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
分钟K线采集服务
- 每分钟整点后第10秒调用第三方接口，拉取全市场分钟K线
- 写入 Redis（TTL 5天）+ PostgreSQL（永久）
- 本模块只负责数据采集和存储，不做计算
- 接口尚未接入时，MinuteBarFetcher.fetch_latest() 留空，等待对接
"""
import json
import logging
import time
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import redis
import instock.lib.database as mdb

log = logging.getLogger(__name__)

# ── Redis 配置 ─────────────────────────────────────────────────────────────
REDIS_HOST = os.environ.get('REDIS_HOST', 'localhost')
REDIS_PORT = int(os.environ.get('REDIS_PORT', 6379))
REDIS_DB   = int(os.environ.get('REDIS_DB',   0))
REDIS_TTL  = 5 * 24 * 3600   # 5天

_redis_client: redis.Redis = None


def get_redis() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.Redis(
            host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB,
            decode_responses=True, socket_connect_timeout=3
        )
    return _redis_client


# ── Redis Key 格式 ─────────────────────────────────────────────────────────
# minute_bar:{date}:{code}  →  JSON list，每个元素为一分钟的bar dict
# 例: minute_bar:2026-06-18:000001 → [{"time":"09:31","open":...}, ...]

def redis_key(date: str, code: str) -> str:
    return f'minute_bar:{date}:{code}'


# ── 写入 Redis ─────────────────────────────────────────────────────────────
def save_to_redis(bars_by_code: dict[str, list[dict]], date: str):
    """
    bars_by_code: { code: [ {time, open, close, high, low, volume, amount, pre_close}, ... ] }
    追加模式：取出已有列表，合并去重（按time），再写回
    """
    r = get_redis()
    pipe = r.pipeline(transaction=False)
    for code, bars in bars_by_code.items():
        key = redis_key(date, code)
        existing_raw = r.get(key)
        if existing_raw:
            existing = {b['time']: b for b in json.loads(existing_raw)}
        else:
            existing = {}
        for b in bars:
            existing[b['time']] = b
        # 按时间排序
        merged = sorted(existing.values(), key=lambda x: x['time'])
        pipe.set(key, json.dumps(merged, ensure_ascii=False), ex=REDIS_TTL)
    pipe.execute()


# ── 写入 PostgreSQL ────────────────────────────────────────────────────────
def save_to_pg(bars: list[dict], date: str):
    """
    bars: [ {code, time, open, close, high, low, volume, amount, pre_close} ]
    """
    if not bars:
        return
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
    rows = [
        (date, b['time'], b['code'],
         b.get('open'), b.get('close'), b.get('high'), b.get('low'),
         b.get('volume'), b.get('amount'), b.get('pre_close'))
        for b in bars
    ]
    with mdb.get_connection() as conn:
        with conn.cursor() as cur:
            cur.executemany(sql, rows)
    log.debug(f"PG写入 {len(rows)} 条分钟K线 date={date}")


# ── 第三方接口适配层（待对接） ─────────────────────────────────────────────
def _load_xtick_token() -> str:
    """从环境变量或配置文件读取 XTick API Token"""
    token = os.environ.get('XTICK_TOKEN', '')
    if token:
        return token
    config_file = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), 'config', 'xtick_token.txt'
    )
    if os.path.exists(config_file):
        with open(config_file, 'r') as f:
            token = f.read().strip()
    return token


# XTick API 基础地址
XTICK_BASE_URL = 'http://api.xtick.top'


class MinuteBarFetcher:
    """
    XTick 分钟K线接口适配器。

    盘中采集：调用 /doc/order/minute?type=1&code=all 拉全市场实时分钟K线
    返回格式（标准化后）：
    [
      {
        "code":      "000001",
        "time":      "09:31",     # HH:MM
        "open":      10.50,
        "close":     10.55,
        "high":      10.58,
        "low":       10.48,
        "volume":    12345.0,     # 手
        "amount":    13000000.0,  # 元
        "pre_close": 10.50,       # 前一分钟收盘价
      },
      ...
    ]
    """

    def __init__(self):
        self.token = _load_xtick_token()
        if not self.token:
            log.warning("XTick token 未配置，分钟数据采集将跳过")

    @staticmethod
    def _decode_response(resp) -> list:
        """
        XTick 接口返回 ZIP 压缩的 JSON，解压后返回 list。
        若非 ZIP 则直接尝试 JSON 解析。
        """
        import zipfile, io
        content = resp.content
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as z:
                raw_bytes = z.read(z.namelist()[0])
            return json.loads(raw_bytes)
        except zipfile.BadZipFile:
            # 非 ZIP，直接解析
            return json.loads(content)

    def fetch_latest(self) -> list[dict]:
        """
        拉取最新一分钟的全市场分钟K线数据（使用盯盘接口 /doc/order/minute?code=all）
        """
        if not self.token:
            log.debug("XTick token 未配置，跳过拉取")
            return []

        try:
            import requests
            url = f"{XTICK_BASE_URL}/doc/order/minute"
            params = {
                'type':  1,      # 沪深京A股
                'code':  'all',  # 全市场
                'token': self.token,
            }
            resp = requests.get(url, params=params, timeout=15)
            resp.raise_for_status()
            raw = self._decode_response(resp)
            return self._normalize(raw)
        except Exception as e:
            log.error(f"XTick分钟接口拉取失败: {e}")
            return []

    def fetch_history(self, code: str, date: str) -> list[dict]:
        """
        拉取单股指定日期的1分钟历史K线（使用通用接口 /doc/kline/market?period=1m）
        date: YYYY-MM-DD
        """
        if not self.token:
            return []
        try:
            import requests
            url = f"{XTICK_BASE_URL}/doc/kline/market"
            params = {
                'type':      1,
                'code':      code,
                'fq':        1,      # 不复权
                'period':    '1m',
                'startDate': date,
                'endDate':   date,
                'token':     self.token,
            }
            resp = requests.get(url, params=params, timeout=15)
            resp.raise_for_status()
            raw = self._decode_response(resp)
            return self._normalize(raw)
        except Exception as e:
            log.error(f"XTick历史分钟接口拉取失败 {code}/{date}: {e}")
            return []

    def fetch_single_realtime(self, code: str) -> list[dict]:
        """
        拉取单股当日全部分钟K线（实时接口 /doc/kline/minute，含当日所有历史分钟）
        用于补全：当 /doc/order/minute?code=all 漏掉某只股票时调用
        """
        if not self.token:
            return []
        try:
            import requests
            url = f"{XTICK_BASE_URL}/doc/kline/minute"
            params = {
                'type':  1,
                'code':  code,
                'fq':    1,
                'token': self.token,
            }
            resp = requests.get(url, params=params, timeout=10)
            resp.raise_for_status()
            raw = self._decode_response(resp)
            return self._normalize(raw)
        except Exception as e:
            log.error(f"XTick单股实时分钟拉取失败 {code}: {e}")
            return []

    def _normalize(self, raw) -> list[dict]:
        """
        将 XTick 接口原始数据转换为标准格式。
        接口字段: type, code, time(long毫秒时间戳), open, close, high, low,
                  volume(手), amount(元), preClose
        """
        result = []
        items = raw if isinstance(raw, list) else []
        for item in items:
            try:
                ts = item.get('time', 0)
                if not ts:
                    continue
                dt = datetime.datetime.fromtimestamp(int(ts) / 1000)
                t_str = dt.strftime('%H:%M')

                result.append({
                    'code':      str(item.get('code', '')).zfill(6),
                    'time':      t_str,
                    'open':      float(item.get('open',     0) or 0),
                    'close':     float(item.get('close',    0) or 0),
                    'high':      float(item.get('high',     0) or 0),
                    'low':       float(item.get('low',      0) or 0),
                    'volume':    float(item.get('volume',   0) or 0),
                    'amount':    float(item.get('amount',   0) or 0),
                    'pre_close': float(item.get('preClose', 0) or 0),
                })
            except Exception as e:
                log.warning(f"normalize失败: {e} item={item}")
        return result


# ── 交易分钟列表工具 ──────────────────────────────────────────────────────

def _expected_minutes_until(until_time: str) -> list[str]:
    """
    返回 09:30 到 until_time（含）之间所有交易分钟。
    交易时段：09:30-11:30, 13:00-15:00
    """
    mins = []
    for h in range(9, 16):
        for m in range(60):
            t = f'{h:02d}:{m:02d}'
            if t > until_time:
                return mins
            if ('09:30' <= t <= '11:30') or ('13:00' <= t <= '15:00'):
                mins.append(t)
    return mins


# ── 单次采集入口 ───────────────────────────────────────────────────────────
_fetcher = MinuteBarFetcher()


def collect_once():
    """
    执行一次全市场分钟数据采集：
    调用 /doc/order/minute?code=all 拉取最新一分钟全市场数据 → Redis + PG
    """
    now   = datetime.datetime.now()
    date  = now.strftime('%Y-%m-%d')
    t_str = now.strftime('%H:%M')

    bars = _fetcher.fetch_latest()
    if not bars:
        log.debug(f"[{t_str}] /doc/order/minute 返回空")
        return 0

    by_code: dict[str, list] = {}
    for b in bars:
        by_code.setdefault(b['code'], []).append(b)

    try:
        save_to_redis(by_code, date)
    except Exception as e:
        log.error(f"Redis写入失败: {e}")
    try:
        save_to_pg(bars, date)
    except Exception as e:
        log.error(f"PG写入失败: {e}")

    log.info(f"[{now.strftime('%H:%M:%S')}] 全推采集: {len(bars)}条 ({len(by_code)}只)")
    return len(bars)


def check_and_fill_today(date: str = None):
    """
    检查今日全市场分钟数据完整性，缺失的用单股接口补全。

    逻辑：
    1. 从 /doc/order/minute?code=all 获取当前全市场最新快照
       → 得到"活跃股票列表"（今日有交易的股票）
    2. 用 /doc/kline/minute?code=单股 查每只股票当日全部分钟数
    3. 对比期望分钟数，缺失 >2 根的批量补全
    4. 首次启动时所有股票都缺失，分批处理（每批50只，间隔0.5s）
    """
    import time as _time
    import instock.lib.database as mdb

    now          = datetime.datetime.now()
    date         = date or now.strftime('%Y-%m-%d')
    current_time = now.strftime('%H:%M')

    if current_time < '09:32':
        log.debug("未到开盘，跳过补全检查")
        return

    expected_count = len(_expected_minutes_until(current_time))
    if expected_count < 3:
        return

    # Step 1: 从全推接口获取当前活跃股票列表
    log.info(f"[{current_time}] 开始分钟数据完整性检查...")
    latest_bars = _fetcher.fetch_latest()
    active_codes = list({b['code'] for b in latest_bars}) if latest_bars else []

    if not active_codes:
        log.warning("无法获取活跃股票列表，跳过补全")
        return

    # Step 2: 查 PG 中今日各股已有分钟数
    try:
        rows = mdb.executeSqlFetch(
            'SELECT code, COUNT(*) AS cnt FROM cn_stock_minute_bar WHERE date=%s GROUP BY code',
            (date,)
        )
        code_counts = {r[0]: int(r[1]) for r in rows} if rows else {}
    except Exception as e:
        log.error(f"查询今日分钟统计失败: {e}")
        return

    # Step 3: 找出需要补全的股票（缺失 >2 根）
    missing = [
        code for code in active_codes
        if expected_count - code_counts.get(code, 0) > 2
    ]

    if not missing:
        log.info(f"[{current_time}] 分钟数据完整（{len(active_codes)}只活跃），无需补全")
        return

    log.info(f"[{current_time}] {len(missing)}/{len(active_codes)} 只需补全"
             f"（期望{expected_count}根，首次启动时属正常）")

    # Step 4: 分批补全，每批 50 只
    batch_size = 50
    total_filled = 0
    for i in range(0, len(missing), batch_size):
        batch = missing[i:i + batch_size]
        for code in batch:
            try:
                bars = _fetcher.fetch_single_realtime(code)
                if not bars:
                    continue
                # 只保留交易时段内数据
                valid = [b for b in bars
                         if ('09:30' <= b['time'] <= '11:30') or
                            ('13:00' <= b['time'] <= '15:00')]
                if not valid:
                    continue
                for b in valid:
                    b['code'] = code
                save_to_redis({code: valid}, date)
                save_to_pg(valid, date)
                total_filled += 1
            except Exception as e:
                log.warning(f"补全 {code} 失败: {e}")
        # 批次间短暂休眠，避免 API 请求过于密集
        if i + batch_size < len(missing):
            _time.sleep(0.5)

    log.info(f"[{current_time}] 补全完成: {total_filled}/{len(missing)} 只")


# ── Redis 读取工具函数（供评分引擎使用）───────────────────────────────────
# 量能监控所有数据全部从 Redis 读取，不走数据库。
# 若 Redis 无数据，由 fill_minute_bars.py 补全后再查询。

def get_day_bars(date: str, code: str) -> list[dict]:
    """从 Redis 获取某日某股票的全部分钟K线，无数据返回空列表"""
    try:
        r = get_redis()
        raw = r.get(redis_key(date, code))
        return json.loads(raw) if raw else []
    except Exception as e:
        log.warning(f"Redis读取失败 {date}/{code}: {e}")
        return []


def get_day_bars_until(date: str, code: str, until_time: str) -> list[dict]:
    """从 Redis 获取某日某股票截止到 until_time（含）的分钟K线"""
    bars = get_day_bars(date, code)
    return [b for b in bars if b['time'] <= until_time]


def get_day_bars_from(date: str, code: str, from_time: str) -> list[dict]:
    """从 Redis 获取某日某股票 from_time（不含）到收盘的分钟K线
    用于虚拟全日量：昨日剩余时间段的量 = 昨日[hhmm+1..15:00]
    """
    bars = get_day_bars(date, code)
    return [b for b in bars if b['time'] > from_time]


def get_minute_bar(date: str, code: str, time_str: str) -> dict | None:
    """从 Redis 获取某日某股票特定分钟的K线"""
    bars = get_day_bars(date, code)
    for b in bars:
        if b['time'] == time_str:
            return b
    return None


def get_all_codes_for_date(date: str) -> list[str]:
    """
    获取某日已有分钟数据的所有股票代码（只查 Redis scan）。
    Redis 无数据时返回空列表 —— 调用方应先确保数据已通过
    fill_minute_bars.py 写入 Redis。
    """
    try:
        r = get_redis()
        pattern = f'minute_bar:{date}:*'
        keys = []
        cursor = 0
        while True:
            cursor, batch = r.scan(cursor, match=pattern, count=500)
            keys.extend(batch)
            if cursor == 0:
                break
        codes = [k.split(':')[-1] for k in keys]
        log.debug(f"get_all_codes_for_date({date}) Redis scan: {len(codes)}只")
        return codes
    except Exception as e:
        log.error(f"Redis scan失败: {e}")
        return []


def get_multi_day_bars_at_time(codes: list[str], dates: list[str], time_str: str) -> dict:
    """
    批量获取多只股票、多个日期在特定分钟的K线数据
    返回: {code: {date: bar_dict}}
    """
    r = get_redis()
    result: dict[str, dict] = {code: {} for code in codes}

    pipe = r.pipeline(transaction=False)
    keys_order = []
    for code in codes:
        for date in dates:
            pipe.get(redis_key(date, code))
            keys_order.append((code, date))

    values = pipe.execute()
    for (code, date), raw in zip(keys_order, values):
        if raw:
            bars = json.loads(raw)
            for b in bars:
                if b['time'] == time_str:
                    result[code][date] = b
                    break
    return result
