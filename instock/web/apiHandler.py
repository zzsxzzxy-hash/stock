#!/usr/local/bin/python3
# -*- coding: utf-8 -*-
"""
Vue3 前端所需 REST API：
  GET  /api/meta?name=<table>                          → 返回列定义
  GET  /api/data?name=<table>&date=...&page=1&size=200 → 分页返回表数据
  GET  /api/trade_date                                 → 返回最近交易日
  GET  /instock/api_data/kline?code=...&date=...       → 返回 K 线历史
"""

import json
import datetime
from abc import ABC

from tornado import gen
import instock.lib.trade_time as trd
import instock.core.singleton_stock_web_module_data as sswmd
import instock.core.tablestructure as tbs
import instock.web.base as webBase
from instock.web.dataTableHandler import MyEncoder

__author__ = 'myh'
__date__ = '2024/6/1'

_ATTENTION_TABLE = tbs.TABLE_CN_STOCK_ATTENTION['name']

# 有 order_columns 子查询的表，改用 LEFT JOIN 实现 cdatetime
# 无需在这里枚举，apiHandler 统一检测 wmd.order_columns 是否含子查询关键字
def _build_data_sql(wmd, where_clause):
    """
    构造查询 SQL。
    where_clause 使用无表前缀的列名（如 "`date` = %s"）。
    若 order_columns 含关联子查询，改为 LEFT JOIN，消除 N+1 问题。
    """
    tname    = wmd.table_name
    order_by = f" ORDER BY {wmd.order_by}" if wmd.order_by else ""

    has_attention_join = (
        wmd.order_columns is not None and
        'cn_stock_attention' in wmd.order_columns
    )

    if has_attention_join:
        # 为 JOIN 查询给列加表别名前缀
        prefixed_where = where_clause.replace('`date`', 't.`date`').replace('`code`', 't.`code`').replace('`name`', 't.`name`')
        sql = (
            f"SELECT t.*, a.`datetime` AS `cdatetime`"
            f" FROM `{tname}` t"
            f" LEFT JOIN `{_ATTENTION_TABLE}` a ON a.`code` = t.`code`"
            f"{prefixed_where}"
            f"{order_by}"
        )
    elif wmd.order_columns:
        sql = (
            f"SELECT *, {wmd.order_columns}"
            f" FROM `{tname}`"
            f"{where_clause}"
            f"{order_by}"
        )
    else:
        sql = f"SELECT * FROM `{tname}`{where_clause}{order_by}"

    return sql, has_attention_join


class ApiMetaHandler(webBase.BaseHandler, ABC):
    """返回指定表的列定义"""

    def get(self):
        name = self.get_argument("name", default=None, strip=False)
        self.set_header('Content-Type', 'application/json;charset=UTF-8')
        self.set_header('Access-Control-Allow-Origin', '*')
        try:
            wmd = sswmd.stock_web_module_data().get_data(name)
            columns = wmd.column_names
            result = [
                {
                    'prop':  c['value'],
                    'label': c.get('caption', c.get('cn', c['value'])),
                    'width': c.get('width') or 100,
                }
                for c in columns
            ]
            self.write(json.dumps({'columns': result}, ensure_ascii=False))
        except Exception as e:
            self.set_status(400)
            self.write(json.dumps({'error': str(e)}, ensure_ascii=False))


class ApiDataHandler(webBase.BaseHandler, ABC):
    """分页返回表数据，默认每页 200 条"""

    # 市场板块 → code LIKE 前缀映射
    _MARKET_PREFIXES = {
        'sh':    ['6'],               # 沪市主板
        'sz':    ['0', '2'],          # 深市主板
        'cyb':   ['3'],               # 创业板
        'kcb':   ['688'],             # 科创板
        'bse':   ['920'],             # 北交所
        'etf':   ['1', '5'],          # ETF
    }

    def get(self):
        name      = self.get_argument("name",   default=None, strip=False)
        date      = self.get_argument("date",   default=None, strip=False)
        search    = self.get_argument("search", default=None, strip=False)
        market    = self.get_argument("market", default=None, strip=False)
        page      = int(self.get_argument("page", default="1",   strip=False))
        page_size = int(self.get_argument("size", default="200", strip=False))
        page_size = min(page_size, 5000)
        offset    = (page - 1) * page_size

        self.set_header('Content-Type', 'application/json;charset=UTF-8')
        self.set_header('Access-Control-Allow-Origin', '*')

        try:
            wmd = sswmd.stock_web_module_data().get_data(name)
            has_code = any(c['value'] == 'code' for c in wmd.column_names)

            # 构造 WHERE 条件（不含表别名，由 _build_data_sql 按需添加前缀）
            conditions = []
            params = []
            if date:
                conditions.append("`date` = %s")
                params.append(date)
            if search:
                kw = f"%{search}%"
                if has_code:
                    conditions.append("(`code` LIKE %s OR `name` LIKE %s)")
                    params.extend([kw, kw])
                else:
                    conditions.append("`name` LIKE %s")
                    params.append(kw)
            if market and has_code:
                prefixes = self._MARKET_PREFIXES.get(market, [])
                if prefixes:
                    like_parts = ' OR '.join(['`code` LIKE %s'] * len(prefixes))
                    conditions.append(f"({like_parts})")
                    params.extend([f"{p}%" for p in prefixes])

            where_clause = (" WHERE " + " AND ".join(conditions)) if conditions else ""

            data_sql, _ = _build_data_sql(wmd, where_clause)
            paged_sql   = data_sql + f" LIMIT {page_size} OFFSET {offset}"

            # count（轻量查询）
            cnt_conds = []
            cnt_params = []
            if date:
                cnt_conds.append("`date` = %s");  cnt_params.append(date)
            if search:
                kw = f"%{search}%"
                if has_code:
                    cnt_conds.append("(`code` LIKE %s OR `name` LIKE %s)")
                    cnt_params.extend([kw, kw])
                else:
                    cnt_conds.append("`name` LIKE %s")
                    cnt_params.append(kw)
            if market and has_code:
                prefixes = self._MARKET_PREFIXES.get(market, [])
                if prefixes:
                    like_parts = ' OR '.join(['`code` LIKE %s'] * len(prefixes))
                    cnt_conds.append(f"({like_parts})")
                    cnt_params.extend([f"{p}%" for p in prefixes])

            cnt_where = (" WHERE " + " AND ".join(cnt_conds)) if cnt_conds else ""
            count_sql = f"SELECT COUNT(*) AS cnt FROM `{wmd.table_name}`{cnt_where}"

            total_row = self.db.get(count_sql, *cnt_params)
            data      = self.db.query(paged_sql, *params)

            total = total_row['cnt'] if total_row else 0

            self.write(json.dumps(
                {'total': total, 'page': page, 'size': page_size, 'data': data},
                cls=MyEncoder, ensure_ascii=False
            ))
        except Exception as e:
            self.set_status(400)
            self.write(json.dumps({'error': str(e)}, ensure_ascii=False))


class ApiTradeDateHandler(webBase.BaseHandler, ABC):
    """返回最近交易日"""

    def get(self):
        self.set_header('Content-Type', 'application/json;charset=UTF-8')
        self.set_header('Access-Control-Allow-Origin', '*')
        try:
            run_date, run_date_nph = trd.get_trade_date_last()
            self.write(json.dumps({
                'date':     run_date.strftime('%Y-%m-%d'),
                'date_nph': run_date_nph.strftime('%Y-%m-%d'),
            }))
        except Exception as e:
            self.set_status(500)
            self.write(json.dumps({'error': str(e)}))


class ApiKlineHandler(webBase.BaseHandler, ABC):
    """返回个股 K 线历史数据，支持日/周/月 K"""

    # 实际表名
    _HIST_TABLE = 'cn_stock_hist_data'

    def get(self):
        code   = self.get_argument("code",   default=None,    strip=False)
        date   = self.get_argument("date",   default=None,    strip=False)
        period = self.get_argument("period", default="daily", strip=False)

        self.set_header('Content-Type', 'application/json;charset=UTF-8')
        self.set_header('Access-Control-Allow-Origin', '*')

        if not code:
            self.set_status(400)
            self.write(json.dumps({'error': 'code is required'}))
            return
        try:
            # 日K：直接取最近 500 条
            sql = f"""
                SELECT date, open, close, high, low, volume, amount,
                       quote_change, ups_downs, turnover
                FROM `{self._HIST_TABLE}`
                WHERE code = %s
                ORDER BY date DESC
                LIMIT 500
            """
            rows = list(reversed(self.db.query(sql, code)))

            if not rows:
                self.write(json.dumps([], ensure_ascii=False))
                return

            # 转换为标准格式
            daily = []
            for r in rows:
                d = dict(r)
                if isinstance(d.get('date'), datetime.date):
                    d['date'] = d['date'].isoformat()
                # 统一字段名
                d['change']   = d.pop('ups_downs',    None)
                d['pct_chg']  = d.pop('quote_change', None)
                d['turnover'] = d.get('turnover')
                daily.append(d)

            if period == 'weekly':
                result = self._aggregate(daily, 'W')
            elif period == 'monthly':
                result = self._aggregate(daily, 'M')
            else:
                result = daily

            self.write(json.dumps(result, cls=MyEncoder, ensure_ascii=False))
        except Exception as e:
            self.set_status(500)
            self.write(json.dumps({'error': str(e)}, ensure_ascii=False))

    @staticmethod
    def _aggregate(daily, freq):
        """将日K聚合为周K或月K"""
        from collections import defaultdict
        import datetime as dt

        groups = defaultdict(list)
        for bar in daily:
            d = dt.date.fromisoformat(bar['date'])
            if freq == 'W':
                # 以每周一为 key
                key = (d - dt.timedelta(days=d.weekday())).isoformat()
            else:
                key = f"{d.year}-{d.month:02d}-01"
            groups[key].append(bar)

        result = []
        for key in sorted(groups):
            bars = groups[key]
            closes  = [b['close']  for b in bars if b['close']  is not None]
            opens   = [b['open']   for b in bars if b['open']   is not None]
            highs   = [b['high']   for b in bars if b['high']   is not None]
            lows    = [b['low']    for b in bars if b['low']    is not None]
            volumes = [b['volume'] for b in bars if b['volume'] is not None]
            if not closes: continue
            result.append({
                'date':   bars[-1]['date'],   # 最后一个交易日作为K线时间
                'open':   opens[0],
                'close':  closes[-1],
                'high':   max(highs),
                'low':    min(lows),
                'volume': sum(volumes),
                'amount': sum(b.get('amount') or 0 for b in bars),
                'pct_chg': round((closes[-1] / opens[0] - 1) * 100, 4) if opens[0] else None,
            })
        return result


class ApiWatchlistHandler(webBase.BaseHandler, ABC):
    """
    GET  /api/watchlist          → 返回关注列表（带最新行情）
    POST /api/watchlist          → 添加关注  body: {"code":"000001"}
    DELETE /api/watchlist?code=  → 取消关注
    """

    def _get_latest_date(self):
        row = self.db.get("SELECT MAX(`date`) AS d FROM `cn_stock_spot`")
        return str(row['d']) if row and row['d'] else None

    def get(self):
        self.set_header('Content-Type', 'application/json;charset=UTF-8')
        self.set_header('Access-Control-Allow-Origin', '*')
        try:
            latest = self._get_latest_date()
            if latest:
                rows = self.db.query(
                    """
                    SELECT a.`datetime`, a.`code`,
                           s.`name`, s.`new_price`, s.`change_rate`,
                           s.`volume`, s.`deal_amount`, s.`high_price`,
                           s.`low_price`, s.`open_price`, s.`pre_close_price`,
                           s.`amplitude`, s.`turnoverrate`
                    FROM `cn_stock_attention` a
                    LEFT JOIN `cn_stock_spot` s
                           ON s.`code` = a.`code` AND s.`date` = %s
                    ORDER BY a.`datetime` DESC
                    """,
                    latest
                )
            else:
                rows = self.db.query(
                    "SELECT `datetime`, `code` FROM `cn_stock_attention` ORDER BY `datetime` DESC"
                )
            self.write(json.dumps(
                {'data': rows, 'latest_date': latest},
                cls=MyEncoder, ensure_ascii=False
            ))
        except Exception as e:
            self.set_status(500)
            self.write(json.dumps({'error': str(e)}, ensure_ascii=False))

    def post(self):
        self.set_header('Content-Type', 'application/json;charset=UTF-8')
        self.set_header('Access-Control-Allow-Origin', '*')
        try:
            body = json.loads(self.request.body)
            code = body.get('code', '').strip()
            if not code:
                self.set_status(400)
                self.write(json.dumps({'error': 'code required'}))
                return
            now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')
            self.db.execute(
                "INSERT IGNORE INTO `cn_stock_attention`(`datetime`,`code`) VALUES(%s,%s)",
                now, code
            )
            self.write(json.dumps({'ok': True}))
        except Exception as e:
            self.set_status(500)
            self.write(json.dumps({'error': str(e)}, ensure_ascii=False))

    def delete(self):
        self.set_header('Content-Type', 'application/json;charset=UTF-8')
        self.set_header('Access-Control-Allow-Origin', '*')
        try:
            code = self.get_argument('code', default=None, strip=True)
            if not code:
                self.set_status(400)
                self.write(json.dumps({'error': 'code required'}))
                return
            self.db.execute("DELETE FROM `cn_stock_attention` WHERE `code`=%s", code)
            self.write(json.dumps({'ok': True}))
        except Exception as e:
            self.set_status(500)
            self.write(json.dumps({'error': str(e)}, ensure_ascii=False))


class ApiCustomStrategyHandler(webBase.BaseHandler, ABC):
    """
    自有策略数据接口 — 直接读预计算结果表，不做实时 JOIN 计算。
    GET /api/custom_strategy?table=cn_stock_strategy_volume_surge&date=2026-06-12&page=1&size=200
    返回字段由建表时写入的列决定（signal_close/vol_ratio/turnover/p_change 均已预计算）。
    current_price / chg_from_signal 通过一次 LEFT JOIN cn_stock_spot 最新日补充。
    """

    def get(self):
        self.set_header('Content-Type', 'application/json;charset=UTF-8')
        self.set_header('Access-Control-Allow-Origin', '*')
        try:
            table  = self.get_argument('table',  default=None, strip=True)
            date   = self.get_argument('date',   default=None, strip=True)
            search = self.get_argument('search', default=None, strip=True)
            page   = int(self.get_argument('page', default='1',   strip=True))
            size   = int(self.get_argument('size', default='200', strip=True))
            size   = min(size, 2000)
            offset = (page - 1) * size

            if not table:
                self.set_status(400); self.write(json.dumps({'error': 'table required'})); return

            allowed = {s['name'] for s in tbs.TABLE_CN_STOCK_CUSTOM_STRATEGIES}
            if table not in allowed:
                self.set_status(403); self.write(json.dumps({'error': 'table not allowed'})); return

            conds, params = [], []
            if date:
                conds.append('s.`date` = %s'); params.append(date)
            if search:
                kw = f'%{search}%'
                conds.append('(s.`code` LIKE %s OR s.`name` LIKE %s)')
                params.extend([kw, kw])
            where = ('WHERE ' + ' AND '.join(conds)) if conds else ''

            cnt_r = self.db.get(f"SELECT COUNT(*) AS cnt FROM `{table}` s {where}", *params)
            total = cnt_r['cnt'] if cnt_r else 0

            # 只做一次 LEFT JOIN 拿最新价，其余字段全部直接读库
            data_sql = f"""
                SELECT
                    s.*,
                    CAST(sp.`new_price`   AS DECIMAL(12,4)) AS current_price,
                    CAST(sp.`change_rate` AS DECIMAL(12,4)) AS today_change,
                    CASE WHEN s.`signal_close` > 0
                         THEN ROUND((sp.`new_price` - s.`signal_close`) / s.`signal_close` * 100, 2)
                         ELSE NULL END AS chg_from_signal
                FROM `{table}` s
                LEFT JOIN `cn_stock_spot` sp
                    ON sp.`code` = s.`code`
                    AND sp.`date` = (SELECT MAX(date) FROM `cn_stock_spot`)
                {where}
                ORDER BY s.`date` DESC, chg_from_signal DESC
                LIMIT {size} OFFSET {offset}
            """
            rows = self.db.query(data_sql, *params)

            import decimal
            # 需要强制转float的数值字段
            _float_fields = {'current_price', 'today_change', 'chg_from_signal',
                             'signal_close', 'signal_vol', 'prev_vol', 'vol_ratio', 'turnover', 'p_change'}
            result = []
            for row in (rows or []):
                item = {}
                for k, v in row.items():
                    if isinstance(v, datetime.date):
                        item[k] = v.strftime('%Y-%m-%d')
                    elif isinstance(v, (float, decimal.Decimal)):
                        item[k] = round(float(v), 4)
                    elif isinstance(v, int):
                        item[k] = v
                    elif k in _float_fields and v is not None:
                        try:
                            item[k] = round(float(v), 4)
                        except (ValueError, TypeError):
                            item[k] = v
                    else:
                        item[k] = v
                result.append(item)

            self.write(json.dumps({'total': total, 'page': page, 'size': size, 'data': result},
                                  ensure_ascii=False, cls=MyEncoder))
        except Exception as e:
            self.set_status(500)
            self.write(json.dumps({'error': str(e)}, ensure_ascii=False))


class ApiSinaRealtimeHandler(webBase.BaseHandler, ABC):
    """
    新浪财经实时行情代理
    GET /api/sina_realtime?codes=000001,000002,...
    返回: { "000001": { price, change, change_pct, volume, amount }, ... }
    """
    @gen.coroutine
    def get(self):
        import requests as _req
        codes_str = self.get_argument('codes', '')
        if not codes_str:
            self.write(json.dumps({}))
            return
        try:
            codes = [c.strip() for c in codes_str.split(',') if c.strip()]
            # 转换为新浪格式: 6/9开头 → s_sh, 其余 → s_sz
            sina_codes = []
            for c in codes:
                if c.startswith(('6', '9')):
                    sina_codes.append(f"s_sh{c}")
                else:
                    sina_codes.append(f"s_sz{c}")

            url = f"http://hq.sinajs.cn/list={','.join(sina_codes)}"
            headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "Referer": "https://finance.sina.com.cn/",
            }
            resp = _req.get(url, headers=headers, timeout=8)
            resp.encoding = 'gbk'

            result = {}
            for line in resp.text.strip().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    var_part, data_part = line.split('=', 1)
                    # 提取6位代码
                    key = var_part.strip()  # e.g. var hq_str_s_sh688802
                    code = key[-6:]         # 末6位即股票代码
                    data = data_part.strip().strip('";').strip('"')
                    fields = data.split(',')
                    if len(fields) >= 6:
                        result[code] = {
                            'name':       fields[0],
                            'price':      float(fields[1]) if fields[1] else None,
                            'change':     float(fields[2]) if fields[2] else None,
                            'change_pct': float(fields[3]) if fields[3] else None,
                            'volume':     int(fields[4])   if fields[4] else None,
                            'amount':     float(fields[5]) * 10 if fields[5] else None,  # 新浪返回万元，转为千元(与数据库一致)
                        }
                except Exception:
                    continue

            self.set_header('Content-Type', 'application/json; charset=utf-8')
            self.write(json.dumps(result, ensure_ascii=False))
        except Exception as e:
            self.set_status(500)
            self.write(json.dumps({'error': str(e)}, ensure_ascii=False))

