#!/usr/bin/env python3
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
量能监控 API Handler
注册到 web_service.py

接口列表：
  GET  /api/volume_rank          → 排行榜
  GET  /api/volume_detail        → 个股深度数据（右栏）
  GET  /api/sector_list          → 板块列表
  GET  /api/sector_stocks        → 板块下的股票列表
  POST /api/sector_map           → 新增股票-板块映射
  DELETE /api/sector_map         → 删除股票-板块映射
  PUT  /api/sector_map/batch     → 批量设置某股票的全部板块
  POST /api/score_single         → 单股票因子得分计算
"""
import json
import datetime
import logging
from abc import ABC

from tornado import gen
import instock.web.base as webBase
import instock.lib.database as mdb
from instock.core.minute_bar_collector import (
    get_day_bars_until, get_day_bars_from, get_day_bars, get_redis
)
from instock.core.volume_pre_calc import get_pre_calc, _cache_sectors
from instock.core.volume_rank_engine import (
    refresh_rank_cache, get_cached_rank, VOL_RATIO_TH,
    calc_price_slope, gen_volume_label, _last_n_trade_dates, _prev_trade_date
)

log = logging.getLogger(__name__)


def _today() -> str:
    return datetime.date.today().strftime('%Y-%m-%d')


def _current_time() -> str:
    return datetime.datetime.now().strftime('%H:%M')


def _latest_data_date() -> str:
    """返回 cn_stock_minute_bar 中最近有数据的日期"""
    try:
        rows = mdb.executeSqlFetch(
            'SELECT MAX(date) FROM cn_stock_minute_bar'
        )
        if rows and rows[0][0]:
            d = rows[0][0]
            return d.strftime('%Y-%m-%d') if hasattr(d, 'strftime') else str(d)
    except Exception:
        pass
    return _today()


# ── 市场过滤规则 ──────────────────────────────────────────────────────────
# 实际数据中的代码分布：
#   沪市A股:  6xxxxx（含科创板688/689）
#   深市A股:  00xxxx
#   创业板:   300xxx, 301xxx
#   科创板:   688xxx, 689xxx
#   京市A股:  9xxxxx（北交所，如920xxx）
MARKET_RULES = {
    'sh':   lambda c: c.startswith('6') and not (c.startswith('688') or c.startswith('689')),
    'sz':   lambda c: c.startswith('00'),
    'cyb':  lambda c: c.startswith('300') or c.startswith('301'),
    'kcb':  lambda c: c.startswith('688') or c.startswith('689'),
    'bj':   lambda c: c.startswith('9'),
}

def _market_code_fn(markets: list):
    """返回市场过滤函数，无过滤时返回 None"""
    if not markets or 'all' in markets:
        return None
    rules = [MARKET_RULES[m] for m in markets if m in MARKET_RULES]
    if not rules:
        return None
    return lambda code: any(r(code) for r in rules)


# ── /api/volume_rank ─────────────────────────────────────────────────────
class ApiVolumeRankHandler(webBase.BaseHandler, ABC):
    """
    GET /api/volume_rank
    参数：
      date           YYYY-MM-DD，默认自动取最近有数据的日期
      position       all/low/break，默认all
      vol_threshold  量比阈值，默认1.5
      market         逗号分隔，可选 sh/sz/cyb/kcb/bj/all，默认all
      change         all/up/down，默认all
      refresh        1=强制刷新缓存
      fa_min         因子A最低分（含），不传=不过滤
      fb_min         因子B最低分（含），不传=不过滤
      fc_min         因子C最低分（含），不传=不过滤
      fd_min         因子D最低分（含），不传=不过滤
      factor_mode    and=全部因子满足(默认) / or=任一因子满足
    """
    def get(self):
        date          = self.get_argument('date',          '')
        position      = self.get_argument('position',      'all')
        vol_threshold = float(self.get_argument('vol_threshold', str(VOL_RATIO_TH)))
        force_refresh = self.get_argument('refresh', '0') == '1'
        market_str    = self.get_argument('market', 'all')
        change_filter = self.get_argument('change', 'all')
        markets       = [m.strip() for m in market_str.split(',') if m.strip()]
        market_key    = ','.join(sorted(markets)) if markets else 'all'

        # 因子过滤参数
        def _parse_float(key):
            v = self.get_argument(key, '')
            try: return float(v) if v != '' else None
            except: return None

        fa_min      = _parse_float('fa_min')
        fb_min      = _parse_float('fb_min')
        fc_min      = _parse_float('fc_min')
        fd_min      = _parse_float('fd_min')
        factor_mode = self.get_argument('factor_mode', 'and')  # and / or

        if not date or date == _today():
            latest = _latest_data_date()
            if latest != date:
                date = latest

        self.set_header('Content-Type', 'application/json; charset=utf-8')
        try:
            now_time  = _current_time()
            market_fn = _market_code_fn(markets)

            if force_refresh:
                items = refresh_rank_cache(date, now_time, position, vol_threshold,
                                           market=market_key, market_filter_fn=market_fn,
                                           change_filter=change_filter)
            else:
                items = get_cached_rank(date, position, vol_threshold,
                                        market=market_key, change_filter=change_filter)
                if not items:
                    items = refresh_rank_cache(date, now_time, position, vol_threshold,
                                               market=market_key, market_filter_fn=market_fn,
                                               change_filter=change_filter)

            # 因子过滤（在缓存结果上做，不影响缓存本身）
            factor_filters = [
                ('fa', fa_min), ('fb', fb_min),
                ('fc', fc_min), ('fd', fd_min),
            ]
            active = [(k, v) for k, v in factor_filters if v is not None]
            if active:
                def _pass(item):
                    results = [item.get(k, 0) is not None and item.get(k, 0) >= v
                               for k, v in active]
                    return all(results) if factor_mode == 'and' else any(results)
                items = [i for i in items if _pass(i)]

            self.write(json.dumps({
                'date':      date,
                'time':      now_time,
                'count':     len(items),
                'threshold': vol_threshold,
                'market':    markets,
                'data':      items,
            }, ensure_ascii=False, default=str))
        except Exception as e:
            log.error(f"volume_rank error: {e}", exc_info=True)
            self.set_status(500)
            self.write(json.dumps({'error': str(e)}))


# ── /api/volume_detail ───────────────────────────────────────────────────
class ApiVolumeDetailHandler(webBase.BaseHandler, ABC):
    """
    GET /api/volume_detail?code=000001&date=2026-06-18
    返回个股深度数据（右栏）
    """
    def get(self):
        code = self.get_argument('code', '')
        date = self.get_argument('date', _today())

        self.set_header('Content-Type', 'application/json; charset=utf-8')
        if not code:
            self.set_status(400)
            self.write(json.dumps({'error': 'code required'}))
            return

        try:
            now_time  = _current_time()
            yest_date = _prev_trade_date(date)
            pre_data  = get_pre_calc(date)
            pre       = pre_data.get(code, {})

            # 今日分时数据
            today_bars = get_day_bars_until(date, code, now_time)
            yest_bars  = get_day_bars(yest_date, code)

            # ── 信号时间轴（记录每次触发量比阈值的分钟）────────────────
            yest_vol_map = {b['time']: b.get('volume', 0) for b in yest_bars}
            signal_timeline = []
            for b in today_bars:
                t     = b['time']
                t_vol = b.get('volume', 0)
                y_vol = yest_vol_map.get(t, 0)
                if y_vol > 0:
                    min_ratio = round(t_vol / y_vol, 2)
                    if min_ratio >= VOL_RATIO_TH:
                        close = b.get('close', 0)
                        pre_c = pre.get('close_y', 0)
                        chg   = round((close - pre_c) / pre_c * 100, 2) if pre_c else 0
                        signal_timeline.append({
                            'time':      t,
                            'min_ratio': min_ratio,
                            'change':    chg,
                        })
            trigger_count = len(signal_timeline)

            # ── 近10日效率走势 ────────────────────────────────────────
            past10 = _last_n_trade_dates(date, 10)
            past10.reverse()  # 从早到晚
            efficiency_trend = []
            for d in past10:
                rows = mdb.executeSqlFetch(
                    'SELECT quote_change, turnover FROM cn_stock_hist_data WHERE date=%s AND code=%s',
                    (d, code)
                )
                if rows and rows[0][1] and float(rows[0][1]) > 0:
                    e = round(float(rows[0][0]) / float(rows[0][1]), 3)
                    efficiency_trend.append({'date': d, 'efficiency': e})
                else:
                    efficiency_trend.append({'date': d, 'efficiency': 0})

            # ── 近10日K线（带位置色带）───────────────────────────────
            kline_rows = mdb.executeSqlFetch(
                '''SELECT date, open, close, high, low, volume, quote_change, turnover
                   FROM cn_stock_hist_data
                   WHERE code=%s AND date <= %s
                   ORDER BY date DESC LIMIT 60''',
                (code, date)
            )
            kline_data = []
            for r in reversed(kline_rows or []):
                kline_data.append({
                    'date':   str(r[0]),
                    'open':   float(r[1] or 0),
                    'close':  float(r[2] or 0),
                    'high':   float(r[3] or 0),
                    'low':    float(r[4] or 0),
                    'volume': float(r[5] or 0),
                    'change': float(r[6] or 0),
                })

            # ── 分时对比（今日+昨日价格线 + 量柱）──────────────────
            today_timeline = [
                {'time': b['time'], 'close': b.get('close', 0), 'volume': b.get('volume', 0)}
                for b in today_bars
            ]
            yest_timeline = [
                {'time': b['time'], 'close': b.get('close', 0), 'volume': b.get('volume', 0)}
                for b in yest_bars
            ]

            # 位置信息
            position_info = {
                'position': pre.get('position', 'other'),
                'ma120':    pre.get('ma120', 0),
                'high120':  pre.get('high120', 0),
                'close_y':  pre.get('close_y', 0),
            }

            self.write(json.dumps({
                'code':             code,
                'date':             date,
                'today_timeline':   today_timeline,
                'yest_timeline':    yest_timeline,
                'signal_timeline':  signal_timeline,
                'trigger_count':    trigger_count,
                'efficiency_trend': efficiency_trend,
                'kline_data':       kline_data,
                'position_info':    position_info,
            }, ensure_ascii=False, default=str))

        except Exception as e:
            log.error(f"volume_detail error: {e}", exc_info=True)
            self.set_status(500)
            self.write(json.dumps({'error': str(e)}))


# ── /api/sector_list ─────────────────────────────────────────────────────
class ApiSectorListHandler(webBase.BaseHandler, ABC):
    """GET /api/sector_list → 返回所有板块名称及股票数"""
    def get(self):
        self.set_header('Content-Type', 'application/json; charset=utf-8')
        try:
            rows = mdb.executeSqlFetch(
                'SELECT sector, COUNT(*) AS cnt FROM cn_stock_sector_map GROUP BY sector ORDER BY cnt DESC'
            )
            data = [{'sector': r[0], 'count': r[1]} for r in (rows or [])]
            self.write(json.dumps({'data': data}, ensure_ascii=False))
        except Exception as e:
            self.set_status(500)
            self.write(json.dumps({'error': str(e)}))


# ── /api/sector_stocks ───────────────────────────────────────────────────
class ApiSectorStocksHandler(webBase.BaseHandler, ABC):
    """GET /api/sector_stocks?sector=机器人 → 返回板块内股票列表"""
    def get(self):
        sector = self.get_argument('sector', '')
        self.set_header('Content-Type', 'application/json; charset=utf-8')
        if not sector:
            self.set_status(400)
            self.write(json.dumps({'error': 'sector required'}))
            return
        try:
            rows = mdb.executeSqlFetch(
                '''SELECT m.code, s.name
                   FROM cn_stock_sector_map m
                   LEFT JOIN (
                       SELECT DISTINCT ON (code) code, name FROM cn_stock_spot ORDER BY code, date DESC
                   ) s ON s.code = m.code
                   WHERE m.sector = %s
                   ORDER BY m.code''',
                (sector,)
            )
            data = [{'code': r[0], 'name': r[1] or r[0]} for r in (rows or [])]
            self.write(json.dumps({'sector': sector, 'data': data}, ensure_ascii=False))
        except Exception as e:
            self.set_status(500)
            self.write(json.dumps({'error': str(e)}))


# ── /api/sector_map（增删）──────────────────────────────────────────────
class ApiSectorMapHandler(webBase.BaseHandler, ABC):
    """
    POST   /api/sector_map        body: {code, sector}  → 新增映射
    DELETE /api/sector_map        body: {code, sector}  → 删除映射
    """
    def post(self):
        self.set_header('Content-Type', 'application/json; charset=utf-8')
        try:
            body   = json.loads(self.request.body)
            code   = str(body.get('code', '')).strip()
            sector = str(body.get('sector', '')).strip()
            if not code or not sector:
                self.set_status(400)
                self.write(json.dumps({'error': 'code and sector required'}))
                return
            mdb.executeSql(
                'INSERT INTO cn_stock_sector_map (code, sector) VALUES (%s, %s) ON CONFLICT DO NOTHING',
                (code, sector)
            )
            _cache_sectors()   # 刷新Redis缓存
            self.write(json.dumps({'ok': True}))
        except Exception as e:
            self.set_status(500)
            self.write(json.dumps({'error': str(e)}))

    def delete(self):
        self.set_header('Content-Type', 'application/json; charset=utf-8')
        try:
            body   = json.loads(self.request.body)
            code   = str(body.get('code', '')).strip()
            sector = str(body.get('sector', '')).strip()
            if not code or not sector:
                self.set_status(400)
                self.write(json.dumps({'error': 'code and sector required'}))
                return
            mdb.executeSql(
                'DELETE FROM cn_stock_sector_map WHERE code=%s AND sector=%s',
                (code, sector)
            )
            _cache_sectors()
            self.write(json.dumps({'ok': True}))
        except Exception as e:
            self.set_status(500)
            self.write(json.dumps({'error': str(e)}))


# ── /api/sector_map/batch ────────────────────────────────────────────────
class ApiSectorMapBatchHandler(webBase.BaseHandler, ABC):
    """
    PUT /api/sector_map/batch
    body: {code: "000001", sectors: ["机器人", "新能源"]}
    覆盖设置该股票的所有板块
    """
    def put(self):
        self.set_header('Content-Type', 'application/json; charset=utf-8')
        try:
            body    = json.loads(self.request.body)
            code    = str(body.get('code', '')).strip()
            sectors = body.get('sectors', [])
            if not code:
                self.set_status(400)
                self.write(json.dumps({'error': 'code required'}))
                return
            # 先删除该股票所有板块，再插入
            mdb.executeSql('DELETE FROM cn_stock_sector_map WHERE code=%s', (code,))
            for s in sectors:
                s = str(s).strip()
                if s:
                    mdb.executeSql(
                        'INSERT INTO cn_stock_sector_map (code, sector) VALUES (%s,%s) ON CONFLICT DO NOTHING',
                        (code, s)
                    )
            _cache_sectors()
            self.write(json.dumps({'ok': True, 'code': code, 'sectors': sectors}))
        except Exception as e:
            self.set_status(500)
            self.write(json.dumps({'error': str(e)}))


# ── /api/sector_map/stock ────────────────────────────────────────────────
class ApiSectorMapStockHandler(webBase.BaseHandler, ABC):
    """GET /api/sector_map/stock?code=000001 → 返回该股票的所有板块"""
    def get(self):
        code = self.get_argument('code', '')
        self.set_header('Content-Type', 'application/json; charset=utf-8')
        if not code:
            self.set_status(400)
            self.write(json.dumps({'error': 'code required'}))
            return
        try:
            rows = mdb.executeSqlFetch(
                'SELECT sector FROM cn_stock_sector_map WHERE code=%s ORDER BY sector',
                (code,)
            )
            sectors = [r[0] for r in (rows or [])]
            self.write(json.dumps({'code': code, 'sectors': sectors}, ensure_ascii=False))
        except Exception as e:
            self.set_status(500)
            self.write(json.dumps({'error': str(e)}))


# ── /api/redis_dates ─────────────────────────────────────────────────────
class ApiRedisDatesHandler(webBase.BaseHandler, ABC):
    """GET /api/redis_dates → 返回 Redis 中有分钟数据的日期列表"""
    def get(self):
        self.set_header('Content-Type', 'application/json; charset=utf-8')
        try:
            from instock.core.minute_bar_collector import get_redis
            r = get_redis()
            dates = set()
            cursor = 0
            while True:
                cursor, keys = r.scan(cursor, match='minute_bar:*', count=2000)
                for k in keys:
                    parts = k.split(':')
                    if len(parts) >= 2:
                        dates.add(parts[1])
                if cursor == 0:
                    break
            sorted_dates = sorted(dates, reverse=True)
            self.write(json.dumps({'dates': sorted_dates}, ensure_ascii=False))
        except Exception as e:
            self.set_status(500)
            self.write(json.dumps({'error': str(e)}))


# ── /api/redis_query ─────────────────────────────────────────────────────
class ApiRedisQueryHandler(webBase.BaseHandler, ABC):
    """
    GET /api/redis_query?date=2026-06-23&code=000001
    返回 Redis 中该股该日的分钟K线数据，以及统计信息
    """
    def get(self):
        date = self.get_argument('date', '')
        code = self.get_argument('code', '').strip().zfill(6) if self.get_argument('code', '') else ''
        self.set_header('Content-Type', 'application/json; charset=utf-8')

        if not date:
            self.set_status(400)
            self.write(json.dumps({'error': 'date required'}))
            return

        try:
            from instock.core.minute_bar_collector import get_redis, redis_key, get_day_bars, get_all_codes_for_date
            import json as _json

            r = get_redis()

            if code:
                # 查单股
                bars = get_day_bars(date, code)
                # 从 PG 补充股票名称
                name = ''
                try:
                    rows = mdb.executeSqlFetch(
                        'SELECT name FROM cn_stock_spot WHERE code=%s ORDER BY date DESC LIMIT 1',
                        (code,)
                    )
                    if rows:
                        name = rows[0][0] or ''
                except Exception:
                    pass

                stats = {}
                if bars:
                    vols   = [b.get('volume', 0) for b in bars]
                    closes = [b.get('close', 0) for b in bars]
                    stats = {
                        'bar_count':  len(bars),
                        'time_range': f"{bars[0]['time']}~{bars[-1]['time']}",
                        'total_vol':  round(sum(vols), 0),
                        'avg_vol':    round(sum(vols) / len(vols), 0) if vols else 0,
                        'price_open': bars[0].get('open', 0),
                        'price_last': closes[-1] if closes else 0,
                        'price_high': max(b.get('high', 0) for b in bars),
                        'price_low':  min(b.get('low', 0) for b in bars),
                    }

                self.write(_json.dumps({
                    'date':  date,
                    'code':  code,
                    'name':  name,
                    'stats': stats,
                    'bars':  bars,
                }, ensure_ascii=False, default=str))

            else:
                # 查日期概览：返回当日所有股票的汇总统计
                codes = get_all_codes_for_date(date)
                total_codes = len(codes)

                # 抽样5只展示示例
                sample_codes = codes[:5] if codes else []
                samples = []
                for c in sample_codes:
                    b = get_day_bars(date, c)
                    if b:
                        samples.append({
                            'code':      c,
                            'bar_count': len(b),
                            'time_range': f"{b[0]['time']}~{b[-1]['time']}",
                        })

                self.write(_json.dumps({
                    'date':        date,
                    'total_codes': total_codes,
                    'samples':     samples,
                }, ensure_ascii=False, default=str))

        except Exception as e:
            log.error(f"redis_query error: {e}", exc_info=True)
            self.set_status(500)
            self.write(json.dumps({'error': str(e)}))


# ── 分钟K线查询 API ───────────────────────────────────────────────────────────

class ApiMinuteBarsHandler(webBase.BaseHandler, ABC):
    """GET /api/minute_bars?code=000001&date=2026-06-25 → 完整分钟K线"""
    def get(self):
        code = self.get_argument('code', '').strip().zfill(6)
        date = self.get_argument('date', _today())
        self.set_header('Content-Type', 'application/json; charset=utf-8')
        try:
            bars = get_day_bars(date, code)
            self.write(json.dumps({'ok': True, 'code': code, 'date': date, 'bars': bars},
                                  ensure_ascii=False, default=str))
        except Exception as e:
            self.set_status(500)
            self.write(json.dumps({'ok': False, 'error': str(e)}))


# ── 因子配置 API ──────────────────────────────────────────────────────────────

class ApiFactorConfigHandler(webBase.BaseHandler, ABC):
    """GET → 读取配置；POST → 保存配置；DELETE → 重置默认"""

    def get(self):
        from instock.core.factor_config import load_config
        self.write(json.dumps(load_config(), ensure_ascii=False))

    def post(self):
        from instock.core.factor_config import save_config
        try:
            body = json.loads(self.request.body or '{}')
            ok = save_config(body)
            self.write(json.dumps({'ok': ok}, ensure_ascii=False))
        except Exception as e:
            self.set_status(400)
            self.write(json.dumps({'ok': False, 'error': str(e)}))

    def delete(self):
        from instock.core.factor_config import reset_config
        cfg = reset_config()
        self.write(json.dumps({'ok': True, 'config': cfg}, ensure_ascii=False))


# ── 单股票因子得分计算 API ──────────────────────────────────────────────────────

class ApiScoreSingleHandler(webBase.BaseHandler, ABC):
    """
    POST /api/score_single
    body: { code, date, hhmm, factors: ['A','B','C','D'] }
    """

    def post(self):
        try:
            body    = json.loads(self.request.body or '{}')
            code    = str(body.get('code', '')).strip().zfill(6)
            date    = body.get('date', datetime.date.today().strftime('%Y-%m-%d'))
            hhmm    = body.get('hhmm', '15:00')
            factors = body.get('factors', ['A', 'B', 'C', 'D'])

            if not code:
                self.set_status(400)
                self.write(json.dumps({'ok': False, 'error': '股票代码不能为空'}))
                return

            result = _score_single(code, date, hhmm, factors)
            self.write(json.dumps(result, ensure_ascii=False, default=str))
        except Exception as e:
            log.error(f"score_single error: {e}", exc_info=True)
            self.set_status(500)
            self.write(json.dumps({'ok': False, 'error': str(e)}))


def _score_single(code: str, date: str, hhmm: str, factors: list) -> dict:
    """计算单只股票在指定时刻的因子得分明细"""
    from instock.core.factor_config import (
        load_config, apply_factor_a, apply_factor_b,
        apply_factor_c, apply_factor_d, _get_pull_quality_avgs,
    )
    from instock.core.volume_pre_calc import get_pre_calc, get_sectors
    from instock.core.volume_rank_engine import (
        _prev_trade_date, calc_realtime_vol_ratio, calc_price_slope,
        gen_volume_label,
    )
    from collections import defaultdict

    cfg = load_config()

    # ── 基础数据 ──────────────────────────────────────────────────────────────
    pre_data  = get_pre_calc(date)
    sectors   = get_sectors()
    yest_date = _prev_trade_date(date)

    # 股票名称
    name_rows = mdb.executeSqlFetch(
        'SELECT name FROM cn_stock_spot WHERE code=%s ORDER BY date DESC LIMIT 1',
        (code,)
    )
    stock_name = name_rows[0][0] if name_rows else code

    # pre_calc
    pre = pre_data.get(code)
    pre_missing = pre is None
    if pre_missing:
        # 尝试从 DB 直接补算（pre_calc 未生成时）
        pre = _calc_pre_from_db(code, date)

    # ── 分钟K线 ───────────────────────────────────────────────────────────────
    today_bars  = get_day_bars_until(date,      code, hhmm)
    yest_bars   = get_day_bars_until(yest_date, code, hhmm)   # 昨日同期（用于实时量比）
    yest_remain = get_day_bars_from(yest_date,  code, hhmm)   # 昨日剩余（用于虚拟全日量）
    yest_all    = get_day_bars_until(yest_date, code, '15:00')
    yest_total_vol = sum(b.get('volume', 0) for b in yest_all)

    bars_missing = not today_bars

    # ── 当前价格与涨跌幅 ──────────────────────────────────────────────────────
    if today_bars:
        current_close = today_bars[-1].get('close', 0)
    else:
        current_close = 0.0
    pre_close_y   = pre.get('close_y', 0) if pre else 0
    today_change  = round((current_close - pre_close_y) / pre_close_y * 100, 2) \
        if pre_close_y > 0 and current_close > 0 else 0.0
    today_turnover = pre.get('turnover_y', 0) if pre else 0   # 昨日换手率代替

    # 量比
    rt_vol_ratio = calc_realtime_vol_ratio(today_bars, yest_bars)
    today_vol_accum  = sum(b.get('volume', 0) for b in today_bars)
    yest_remain_vol  = sum(b.get('volume', 0) for b in yest_remain)
    virt_ratio = round((today_vol_accum + yest_remain_vol) / yest_total_vol, 2) \
        if yest_total_vol > 0 else 0.0

    price_slope = calc_price_slope(today_bars)

    # ── 因子A ─────────────────────────────────────────────────────────────────
    fa_detail = _factor_a_detail(cfg, pre, current_close, rt_vol_ratio, today_change, factors)

    # ── 因子B：买力强度（用今日实时分钟数据）────────────────────────────────────
    fb_detail = _factor_b_detail(cfg, today_bars, factors)

    # ── 因子C ─────────────────────────────────────────────────────────────────
    fc_detail = _factor_c_detail(cfg, today_bars, yest_remain, yest_total_vol, hhmm, factors)

    # ── 因子D：用全市场第一轮数据（Redis缓存的排行结果）估算板块信息 ──────────
    fd_detail = _factor_d_detail(cfg, code, sectors, date, hhmm, factors)

    # ── 汇总 ──────────────────────────────────────────────────────────────────
    fa = fa_detail['score']
    fb = fb_detail['score']
    fc = fc_detail['score']
    fd = fd_detail['score']
    is_veto = fa_detail.get('is_veto', False)
    total = round(fa + fb + fc + fd, 2) if not is_veto else fa

    vol_label = gen_volume_label(virt_ratio, hhmm, today_change)

    # 效率方向
    e_today = today_change / today_turnover if today_turnover else 0
    e_y     = pre.get('change_y', 0) / pre.get('turnover_y', 1) if pre and pre.get('turnover_y') else 0
    eff_dir = 'up' if e_today > e_y else ('down' if e_today < e_y else 'flat')

    return {
        'ok': True,
        'code': code,
        'name': stock_name,
        'date': date,
        'hhmm': hhmm,
        'factors_used': factors,
        # 摘要
        'summary': {
            'score':         total,
            'is_veto':       is_veto,
            'current_price': round(current_close, 2),
            'today_change':  today_change,
            'rt_vol_ratio':  rt_vol_ratio,
            'virt_ratio':    virt_ratio,
            'price_slope':   price_slope,
            'eff_dir':       eff_dir,
            'vol_label':     vol_label,
            'position':      pre.get('position', 'unknown') if pre else 'unknown',
        },
        # 预计算数据
        'pre_calc': {
            'available': not pre_missing,
            'ma120':          round(pre.get('ma120', 0), 3)   if pre else None,
            'high120':        round(pre.get('high120', 0), 3) if pre else None,
            'position':       pre.get('position', '-')        if pre else None,
            'close_y':        pre.get('close_y', 0)           if pre else None,
            'turnover_y':     pre.get('turnover_y', 0)        if pre else None,
            'change_y':       pre.get('change_y', 0)          if pre else None,
            'turnover_prev':  pre.get('turnover_prev', 0)     if pre else None,
            'change_prev':    pre.get('change_prev', 0)       if pre else None,
        },
        # K线数据
        'bars_info': {
            'available':       not bars_missing,
            'today_bar_count': len(today_bars),
            'yest_bar_count':  len(yest_bars),
            'yest_total_vol':  yest_total_vol,
            'yest_date':       yest_date,
        },
        # 完整分钟K线（供图表）
        'minute_bars': {
            'today': today_bars,
            'yest':  get_day_bars(yest_date, code) if yest_date else [],
        },
        # 四因子明细
        'factor_a': fa_detail,
        'factor_b': fb_detail,
        'factor_c': fc_detail,
        'factor_d': fd_detail,
    }


def _calc_pre_from_db(code: str, date: str) -> dict:
    """pre_calc 不存在时，从 DB 临时计算基础字段"""
    try:
        rows = mdb.executeSqlFetch(
            '''SELECT date, close, high, turnover, quote_change
               FROM cn_stock_hist_data WHERE code=%s AND date<=%s
               ORDER BY date DESC LIMIT 122''',
            (code, date)
        )
        if not rows:
            return {}
        rows = list(reversed(rows))
        recent = rows[-120:]
        closes  = [float(r[1] or 0) for r in recent if r[1]]
        highs   = [float(r[2] or 0) for r in recent if r[2]]
        ma120   = sum(closes) / len(closes) if closes else 0
        high120 = max(highs) if highs else 0
        last    = rows[-1]
        prev    = rows[-2] if len(rows) >= 2 else last
        close_y = float(last[1] or 0)

        def _pos(c, m, h):
            if not c or not m: return 'other'
            if c <= m * 1.05: return 'low'
            if c >= h * 0.98: return 'break'
            if c > m * 1.2:   return 'high'
            return 'other'

        return {
            'ma120': round(ma120, 4), 'high120': round(high120, 4),
            'position': _pos(close_y, ma120, high120),
            'close_y': close_y,
            'turnover_y':    float(last[3] or 0),
            'change_y':      float(last[4] or 0),
            'turnover_prev': float(prev[3] or 0),
            'change_prev':   float(prev[4] or 0),
        }
    except Exception as e:
        log.warning(f"_calc_pre_from_db failed: {e}")
        return {}


def _factor_a_detail(cfg, pre, current_close, rt_vol_ratio, today_change, factors) -> dict:
    from instock.core.factor_config import apply_factor_a
    if 'A' not in factors or not cfg.get('A', {}).get('enabled', True):
        return {'score': 0, 'enabled': False, 'skipped': True, 'steps': []}
    if not pre:
        return {'score': 0, 'enabled': True, 'skipped': True, 'reason': 'pre_calc缺失', 'steps': []}

    a_cfg  = cfg.get('A', {})
    th     = a_cfg.get('thresholds', {})
    veto_c = a_cfg.get('veto', {})
    scores = a_cfg.get('scores', {})

    high1   = pre.get('high1',   pre.get('high120', 0))
    high2   = pre.get('high2',   0)
    close_y = pre.get('close_y', 0)
    pos     = pre.get('position', 'other')
    ratio   = th.get('ratio', {}).get('value', 1.2)

    h1_above = high1 > close_y * ratio if close_y else False
    h2_above = high2 > close_y * ratio if close_y else False

    steps = [
        {'name': '昨日收盘价 (close_y)',      'value': round(close_y, 3), 'unit': '元'},
        {'name': 'High1（120日最高价）',       'value': round(high1, 3),  'unit': '元'},
        {'name': 'High2（120日最高收盘价）',   'value': round(high2, 3),  'unit': '元'},
        {'name': '低位判断倍数 ratio',         'value': ratio,            'unit': ''},
        {'name': f'High1 > 昨收×{ratio} = {round(close_y*ratio,3)}', 'value': '✓' if h1_above else '✗', 'unit': ''},
        {'name': f'High2 > 昨收×{ratio} = {round(close_y*ratio,3)}', 'value': '✓' if h2_above else '✗', 'unit': ''},
        {'name': '位置分类',                   'value': pos,              'unit': ''},
        {'name': '当前实时价格',               'value': round(current_close, 3), 'unit': '元'},
        {'name': '今日涨跌幅',                 'value': today_change,     'unit': '%'},
    ]

    # 一票否决（默认关闭）
    veto_triggered = False
    veto_detail    = {}
    if veto_c.get('enabled', False):
        h_ratio    = veto_c.get('high120_ratio', {}).get('value', 0.95)
        vol_min    = veto_c.get('vol_ratio_min', {}).get('value', 2.0)
        change_max = veto_c.get('change_max',    {}).get('value', 1.0)
        cond1 = high1 > 0 and current_close > high1 * h_ratio
        cond2 = rt_vol_ratio > vol_min
        cond3 = today_change < change_max
        veto_triggered = cond1 and cond2 and cond3
        veto_detail = {
            'high1_ratio_check': f'收盘{current_close:.2f} > High1×{h_ratio}={high1*h_ratio:.2f} → {"✓" if cond1 else "✗"}',
            'vol_ratio_check':   f'量比{rt_vol_ratio:.2f} > {vol_min} → {"✓" if cond2 else "✗"}',
            'change_check':      f'涨幅{today_change:.2f}% < {change_max}% → {"✓" if cond3 else "✗"}',
            'triggered':         veto_triggered,
        }
        steps.append({'name': '一票否决', 'value': '触发' if veto_triggered else '未触发', 'unit': ''})

    score, _ = apply_factor_a(cfg, pre, current_close, rt_vol_ratio, today_change)
    hit_rule  = 'veto' if veto_triggered else pos

    return {
        'score':    score,
        'enabled':  True,
        'is_veto':  veto_triggered,
        'hit_rule': hit_rule,
        'score_def': scores.get(hit_rule if hit_rule in scores else 'score0', {}),
        'steps':    steps,
        'veto':     veto_detail,
    }


def _factor_b_detail(cfg, today_bars, factors) -> dict:
    from instock.core.factor_config import apply_factor_b
    if 'B' not in factors or not cfg.get('B', {}).get('enabled', True):
        return {'score': 0, 'enabled': False, 'skipped': True, 'steps': []}
    if not today_bars:
        return {'score': 0, 'enabled': True, 'skipped': True, 'reason': '无分钟K线数据', 'steps': []}

    th     = cfg.get('B', {}).get('thresholds', {})
    scores = cfg.get('B', {}).get('scores', {})
    strong_th  = th.get('strong',  {}).get('value', 0.62)
    normal_th  = th.get('normal',  {}).get('value', 0.55)
    neutral_th = th.get('neutral', {}).get('value', 0.48)

    total_vol = sum(b.get('volume', 0) for b in today_bars)
    up_vol = 0.0
    for i, b in enumerate(today_bars):
        prev_close = today_bars[i-1]['close'] if i > 0 else b.get('pre_close', b['close'])
        if b.get('close', 0) > prev_close:
            up_vol += b.get('volume', 0)

    S = round(up_vol / total_vol, 4) if total_vol > 0 else 0.0

    if S >= strong_th:   hit_rule = 'strong'
    elif S >= normal_th: hit_rule = 'normal'
    elif S >= neutral_th:hit_rule = 'neutral'
    else:                hit_rule = 'weak'

    score = apply_factor_b(cfg, today_bars)

    steps = [
        {'name': '今日总成交量',               'value': int(total_vol),              'unit': '手'},
        {'name': '涨分钟累计量',               'value': int(up_vol),                 'unit': '手'},
        {'name': '买力强度 S = 涨量/总量',     'value': f'{S:.4f} ({S*100:.1f}%)',   'unit': ''},
        {'name': f'强势阈值 {strong_th}',      'value': '✓ 命中' if S >= strong_th  else '✗', 'unit': ''},
        {'name': f'温和阈值 {normal_th}',      'value': '✓ 命中' if S >= normal_th  else '✗', 'unit': ''},
        {'name': f'均衡阈值 {neutral_th}',     'value': '✓ 命中' if S >= neutral_th else '✗', 'unit': ''},
        {'name': '命中规则',                   'value': hit_rule,                    'unit': ''},
    ]

    return {
        'score':    score,
        'enabled':  True,
        'hit_rule': hit_rule,
        'score_def': scores.get(hit_rule, {}),
        'steps':    steps,
        'buy_strength': S,
    }


def _factor_c_detail(cfg, today_bars, yest_bars, yest_total_vol, hhmm, factors) -> dict:
    from instock.core.factor_config import apply_factor_c
    if 'C' not in factors or not cfg.get('C', {}).get('enabled', True):
        return {'score': 0, 'enabled': False, 'skipped': True, 'steps': []}
    if not today_bars:
        return {'score': 0, 'enabled': True, 'skipped': True, 'reason': '今日无分钟K线', 'steps': []}

    c_cfg  = cfg.get('C', {})
    th     = c_cfg.get('thresholds', {})
    scores = c_cfg.get('scores', {})

    vol_ratio_th    = th.get('vol_ratio_th',    {}).get('value', 1.5)
    high_ratio      = th.get('high_ratio',      {}).get('value', 2.0)
    buy_strength_th = th.get('buy_strength_th', {}).get('value', 0.55)

    today_vol_accum = sum(b.get('volume', 0) for b in today_bars)
    yest_remain_vol = sum(b.get('volume', 0) for b in yest_bars)
    virtual_total   = today_vol_accum + yest_remain_vol
    vol_ratio       = round(virtual_total / yest_total_vol, 4) if yest_total_vol > 0 else 0.0

    # 全程买力强度
    up_vol = 0.0
    for i, b in enumerate(today_bars):
        prev_close = today_bars[i-1]['close'] if i > 0 else b.get('pre_close', b.get('close', 0))
        if b.get('close', 0) > prev_close:
            up_vol += b.get('volume', 0)
    buy_strength = round(up_vol / today_vol_accum, 4) if today_vol_accum > 0 else 0.0

    # 命中规则
    if vol_ratio < vol_ratio_th:
        hit_rule = 'none'
    elif vol_ratio >= high_ratio:
        if buy_strength >= buy_strength_th: hit_rule = 'high_good'
        elif buy_strength < 0.45:           hit_rule = 'high_bad'
        else:                               hit_rule = 'normal'
    else:
        hit_rule = 'normal'

    score = apply_factor_c(cfg, today_bars, yest_bars, yest_total_vol, hhmm)

    steps = [
        {'name': '今日累计量 [09:31..T]',         'value': int(today_vol_accum),              'unit': '手'},
        {'name': '昨日剩余量 [T+1..15:00]',       'value': int(yest_remain_vol),              'unit': '手'},
        {'name': '昨日全天量',                    'value': int(yest_total_vol),               'unit': '手'},
        {'name': '虚拟全日量 = 今日 + 昨日剩余',  'value': int(virtual_total),                'unit': '手'},
        {'name': 'vol_ratio = 虚拟全日 / 昨全天', 'value': vol_ratio,                         'unit': 'x'},
        {'name': '量比阈值',                      'value': vol_ratio_th,                      'unit': ''},
        {'name': '高量比阈值',                    'value': high_ratio,                        'unit': ''},
        {'name': '涨分钟累计量',                  'value': int(up_vol),                       'unit': '手'},
        {'name': '全程买力强度 S = 涨量/总量',    'value': f'{buy_strength:.4f} ({buy_strength*100:.1f}%)', 'unit': ''},
        {'name': f'买力强度阈值 {buy_strength_th}','value': '✓' if buy_strength >= buy_strength_th else '✗', 'unit': ''},
    ]

    return {
        'score':        score,
        'enabled':      True,
        'hit_rule':     hit_rule,
        'score_def':    scores.get(hit_rule, {}),
        'vol_ratio':    vol_ratio,
        'buy_strength': buy_strength,
        'steps':        steps,
    }


def _factor_d_detail(cfg, code, sectors, date, hhmm, factors) -> dict:
    from instock.core.factor_config import apply_factor_d
    if 'D' not in factors or not cfg.get('D', {}).get('enabled', True):
        return {'score': 0, 'enabled': False, 'skipped': True, 'steps': []}

    d_cfg   = cfg.get('D', {})
    th      = d_cfg.get('thresholds', {})
    d_scores= d_cfg.get('scores', {})

    strong_n    = th.get('signal_count_strong', {}).get('value', 3)
    weak_change = th.get('avg_change_weak',     {}).get('value', 1.0)

    my_sectors = sectors.get(code, [])

    # 从 Redis 缓存的排行榜估算 sector_scores
    sector_scores = _get_sector_scores_from_cache(date)

    sector_rows = []
    max_score = 0.0
    hit_rule  = 'none'

    for s in my_sectors:
        info         = sector_scores.get(s, {})
        signal_count = info.get('signal_count', 0)
        avg_change   = info.get('avg_change', 0.0)
        from instock.core.factor_config import _score_item
        if signal_count >= strong_n:
            sc = _score_item(d_scores.get('strong', {'type':'fixed','value':2}),
                             {'signal_count': signal_count, 'avg_change': avg_change})
            if sc > max_score:
                max_score = sc; hit_rule = 'strong'
        elif avg_change > weak_change:
            sc = _score_item(d_scores.get('weak', {'type':'fixed','value':1}),
                             {'signal_count': signal_count, 'avg_change': avg_change})
            if sc > max_score:
                max_score = sc; hit_rule = 'weak'
        sector_rows.append({
            'sector': s,
            'signal_count': signal_count,
            'avg_change':   round(avg_change, 2),
        })

    steps = [
        {'name': '所属板块数量', 'value': len(my_sectors), 'unit': '个'},
        {'name': '强板块阈值（信号股数≥）', 'value': strong_n,    'unit': '只'},
        {'name': '弱板块阈值（均涨幅>）',   'value': weak_change, 'unit': '%'},
    ]

    return {
        'score':       max_score,
        'enabled':     True,
        'hit_rule':    hit_rule,
        'score_def':   d_scores.get(hit_rule, {}),
        'my_sectors':  sector_rows,
        'steps':       steps,
        'cache_source': len(sector_scores) > 0,
    }


def _get_sector_scores_from_cache(date: str) -> dict:
    """从当日排行榜 Redis 缓存反向推算 sector_scores"""
    try:
        r = get_redis()
        cursor = 0
        items  = []
        while True:
            cursor, keys = r.scan(cursor, match=f'volume_rank:{date}:*', count=100)
            for k in keys:
                raw = r.get(k)
                if raw:
                    try:
                        items.extend(json.loads(raw))
                    except Exception:
                        pass
            if cursor == 0:
                break

        if not items:
            return {}

        # 去重（同一code可能在多个cache key里出现）
        seen  = set()
        uniq  = []
        for it in items:
            if it.get('code') not in seen:
                seen.add(it['code'])
                uniq.append(it)

        from collections import defaultdict
        bucket: dict = defaultdict(list)
        for it in uniq:
            score = (it.get('fa', 0) or 0) + (it.get('fb', 0) or 0) + (it.get('fc', 0) or 0)
            for s in it.get('sectors', []):
                bucket[s].append({'change': it.get('today_change', 0), 'signal': score > 2})

        result = {}
        for s, lst in bucket.items():
            result[s] = {
                'signal_count': sum(1 for x in lst if x['signal']),
                'avg_change':   sum(x['change'] for x in lst) / len(lst),
            }
        return result
    except Exception as e:
        log.warning(f"_get_sector_scores_from_cache: {e}")
        return {}
