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
from collections import defaultdict, deque
import math

from tornado import gen
import requests as _req
import instock.lib.trade_time as trd
import instock.core.singleton_stock_web_module_data as sswmd
import instock.core.tablestructure as tbs
import instock.web.base as webBase
from instock.web.dataTableHandler import MyEncoder

__author__ = 'myh'
__date__ = '2024/6/1'

_ATTENTION_TABLE = tbs.TABLE_CN_STOCK_ATTENTION['name']
_OPERATION_LOG_TABLE = 'cn_trade_operation_log'


def _ensure_operation_log_table(db):
    db.execute(f'''
        CREATE TABLE IF NOT EXISTS "{_OPERATION_LOG_TABLE}" (
            id BIGSERIAL PRIMARY KEY,
            trade_date DATE NOT NULL,
            trade_time VARCHAR(5),
            code VARCHAR(6) NOT NULL,
            name VARCHAR(30),
            action VARCHAR(16) NOT NULL,
            price NUMERIC(12, 4),
            quantity NUMERIC(18, 2),
            mainline VARCHAR(80),
            strategy VARCHAR(40),
            reason TEXT,
            result VARCHAR(40),
            follow_plan TEXT,
            system_judgment TEXT,
            signal_strategy VARCHAR(40),
            signal_snapshot_time VARCHAR(5),
            signal_core_score NUMERIC(10, 2),
            signal_mode VARCHAR(40),
            signal_buy_status VARCHAR(24),
            signal_amount_ratio NUMERIC(12, 4),
            signal_risk TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    db.execute(f'''
        ALTER TABLE "{_OPERATION_LOG_TABLE}"
            DROP COLUMN IF EXISTS decision,
            DROP COLUMN IF EXISTS mistake,
            ADD COLUMN IF NOT EXISTS system_judgment TEXT,
            ADD COLUMN IF NOT EXISTS signal_strategy VARCHAR(40),
            ADD COLUMN IF NOT EXISTS signal_snapshot_time VARCHAR(5),
            ADD COLUMN IF NOT EXISTS signal_core_score NUMERIC(10, 2),
            ADD COLUMN IF NOT EXISTS signal_mode VARCHAR(40),
            ADD COLUMN IF NOT EXISTS signal_buy_status VARCHAR(24),
            ADD COLUMN IF NOT EXISTS signal_amount_ratio NUMERIC(12, 4),
            ADD COLUMN IF NOT EXISTS signal_risk TEXT
    ''')
    db.execute(f'''
        CREATE INDEX IF NOT EXISTS "idx_{_OPERATION_LOG_TABLE}_date_code"
        ON "{_OPERATION_LOG_TABLE}" (trade_date DESC, code)
    ''')


def _clean_code(code: str) -> str:
    code = str(code or '').strip()
    return code.zfill(6) if code.isdigit() and len(code) <= 6 else code


def _num_or_none(value):
    if value in (None, ''):
        return None
    try:
        return float(value)
    except Exception:
        return None


def _system_judgment_text(item):
    def display(value, digits=None):
        if value in (None, ''):
            return '-'
        if digits is None:
            return str(value)
        try:
            return f'{float(value):.{digits}f}'
        except Exception:
            return str(value)

    return '；'.join([
        f"核心分：{display(item.get('signal_core_score'), 1)}",
        f"模式：{display(item.get('signal_mode'))}",
        f"量比：{display(item.get('signal_amount_ratio'), 2)}",
        f"风险：{display(item.get('signal_risk') or '无')}",
    ])


def _enrich_operation_signal(item):
    """未携带系统判断时，按交易日期和时间用当前主线逻辑自动补齐。"""
    if item.get('system_judgment'):
        return item
    if not item.get('trade_date') or not item.get('trade_time') or not item.get('code'):
        return item

    try:
        from instock.web.volumeHandler import _stock_signal_detail_for_code

        detail = _stock_signal_detail_for_code(
            item['code'], item['trade_date'], item['trade_time']
        )
        risks = detail.get('risk_tags') or []
        if not isinstance(risks, list):
            risks = [str(risks)] if risks else []
        if not risks:
            tags = detail.get('tags') or ''
            risks = [v.strip() for v in str(tags).split(',') if v.strip()][:3]
        item['signal_strategy'] = item.get('signal_strategy') or 'mainline_core'
        item['signal_snapshot_time'] = item.get('signal_snapshot_time') or item['trade_time']
        item['signal_core_score'] = (
            item.get('signal_core_score')
            if item.get('signal_core_score') is not None
            else _num_or_none(detail.get('core_score', detail.get('score')))
        )
        item['signal_mode'] = (
            item.get('signal_mode')
            or str(detail.get('trade_mode') or detail.get('signal_type') or '').strip()
        )
        item['signal_amount_ratio'] = (
            item.get('signal_amount_ratio')
            if item.get('signal_amount_ratio') is not None
            else _num_or_none(detail.get('amt_vs_prev'))
        )
        item['signal_risk'] = item.get('signal_risk') or ' / '.join(str(v) for v in risks if v)
        item['mainline'] = (
            item.get('mainline')
            or str(detail.get('mainline_theme') or detail.get('trade_theme') or detail.get('best_sector') or '').strip()
        )
        item['name'] = item.get('name') or str(detail.get('name') or '').strip()
        item['system_judgment'] = _system_judgment_text(item)
    except Exception:
        # 操作记录仍可保存；系统判断可以稍后在页面重新识别。
        pass
    return item


def _operation_pnl_map(rows):
    """按股票和交易先后分组，组内所有买卖行显示同一组收益。"""
    lots_by_code = defaultdict(deque)
    group_seq = defaultdict(int)
    groups = {}

    for row in rows or []:
        action = str(row.get('action') or '').lower()
        code = str(row.get('code') or '').zfill(6)
        price = _num_or_none(row.get('price'))
        quantity = _num_or_none(row.get('quantity'))
        if not code or price is None or quantity is None or price <= 0 or quantity <= 0:
            continue

        if action == 'buy':
            if not lots_by_code[code]:
                group_seq[code] += 1
                group_key = (code, group_seq[code])
                groups[group_key] = {
                    'row_ids': set(),
                    'buy_cost': 0.0,
                    'sell_proceeds': 0.0,
                }
            else:
                group_key = lots_by_code[code][0]['group_key']
            groups[group_key]['row_ids'].add(row['id'])
            groups[group_key]['buy_cost'] += price * quantity
            lots_by_code[code].append({
                'id': row['id'],
                'price': price,
                'quantity': quantity,
                'group_key': group_key,
            })
            continue

        if action != 'sell':
            continue

        remaining = quantity
        while remaining > 1e-9 and lots_by_code[code]:
            lot = lots_by_code[code][0]
            matched = min(remaining, lot['quantity'])
            group = groups[lot['group_key']]
            group['row_ids'].add(row['id'])
            group['sell_proceeds'] += price * matched
            lot['quantity'] -= matched
            remaining -= matched
            if lot['quantity'] <= 1e-9:
                lots_by_code[code].popleft()

    pnl_map = {}
    for group in groups.values():
        if group['buy_cost'] <= 0 or group['sell_proceeds'] <= 0:
            continue
        pct = (group['sell_proceeds'] / group['buy_cost'] - 1) * 100
        # 用户示例按两位小数截取，避免 36.66 -> 46.00 显示为 25.48%。
        pct = math.trunc(pct * 100) / 100
        for row_id in group['row_ids']:
            pnl_map[row_id] = pct
    return pnl_map

# 有 order_columns 子查询的表，改用 LEFT JOIN 实现 cdatetime
# 无需在这里枚举，apiHandler 统一检测 wmd.order_columns 是否含子查询关键字
def _build_data_sql(wmd, where_clause):
    """
    构造查询 SQL。
    where_clause 使用无表前缀的列名（如 ""date" = %s"）。
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
        prefixed_where = where_clause.replace('"date"', 't."date"').replace('"code"', 't."code"').replace('"name"', 't."name"')
        sql = (
            f'SELECT t.*, a."datetime" AS "cdatetime"'
            f' FROM "{tname}" t'
            f' LEFT JOIN "{_ATTENTION_TABLE}" a ON a."code" = t."code"'
            f'{prefixed_where}'
            f'{order_by}'
        )
    elif wmd.order_columns:
        sql = (
            f'SELECT *, {wmd.order_columns}'
            f' FROM "{tname}"'
            f'{where_clause}'
            f'{order_by}'
        )
    else:
        sql = 'SELECT * FROM "' + tname + '"' + where_clause + order_by

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
                conditions.append('"date" = %s')
                params.append(date)
            if search:
                kw = f"%{search}%"
                if has_code:
                    conditions.append('("code" LIKE %s OR "name" LIKE %s)')
                    params.extend([kw, kw])
                else:
                    conditions.append('"name" LIKE %s')
                    params.append(kw)
            if market and has_code:
                prefixes = self._MARKET_PREFIXES.get(market, [])
                if prefixes:
                    like_parts = ' OR '.join(['"code" LIKE %s'] * len(prefixes))
                    conditions.append(f"({like_parts})")
                    params.extend([f"{p}%" for p in prefixes])

            where_clause = (" WHERE " + " AND ".join(conditions)) if conditions else ""

            data_sql, _ = _build_data_sql(wmd, where_clause)
            paged_sql   = data_sql + f" LIMIT {page_size} OFFSET {offset}"

            # count（轻量查询）
            cnt_conds = []
            cnt_params = []
            if date:
                cnt_conds.append('"date" = %s');  cnt_params.append(date)
            if search:
                kw = f"%{search}%"
                if has_code:
                    cnt_conds.append('("code" LIKE %s OR "name" LIKE %s)')
                    cnt_params.extend([kw, kw])
                else:
                    cnt_conds.append('"name" LIKE %s')
                    cnt_params.append(kw)
            if market and has_code:
                prefixes = self._MARKET_PREFIXES.get(market, [])
                if prefixes:
                    like_parts = ' OR '.join(['"code" LIKE %s'] * len(prefixes))
                    cnt_conds.append(f"({like_parts})")
                    cnt_params.extend([f"{p}%" for p in prefixes])

            cnt_where = (" WHERE " + " AND ".join(cnt_conds)) if cnt_conds else ""
            count_sql = 'SELECT COUNT(*) AS cnt FROM "' + wmd.table_name + '"'  + cnt_where

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
        code       = self.get_argument("code",       default=None,    strip=False)
        date       = self.get_argument("date",       default=None,    strip=False)
        start_date = self.get_argument("start_date", default=None,    strip=False)
        period     = self.get_argument("period",     default="daily", strip=False)

        self.set_header('Content-Type', 'application/json;charset=UTF-8')
        self.set_header('Access-Control-Allow-Origin', '*')

        if not code:
            self.set_status(400)
            self.write(json.dumps({'error': 'code is required'}))
            return
        try:
            conditions = ['code = %s']
            params = [code]
            if start_date:
                conditions.append('date >= %s')
                params.append(start_date)
            if date:
                conditions.append('date <= %s')
                params.append(date)

            # 默认最多取最近 500 条；前端日K会传 start_date 控制为近 3 个月。
            sql = (
                'SELECT date, open, close, high, low, volume, amount,'
                ' quote_change, ups_downs, turnover'
                ' FROM "' + self._HIST_TABLE + '"'
                ' WHERE ' + ' AND '.join(conditions) +
                ' ORDER BY date DESC LIMIT 500'
            )
            rows = list(reversed(self.db.query(sql, *params)))

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
        row = self.db.get('SELECT MAX("date") AS d FROM "cn_stock_spot"')
        return str(row['d']) if row and row['d'] else None

    def get(self):
        self.set_header('Content-Type', 'application/json;charset=UTF-8')
        self.set_header('Access-Control-Allow-Origin', '*')
        try:
            latest = self._get_latest_date()
            if latest:
                rows = self.db.query(
                    """
                    SELECT a."datetime", a."code",
                           s."name", s."new_price", s."change_rate",
                           s."volume", s."deal_amount", s."high_price",
                           s."low_price", s."open_price", s."pre_close_price",
                           s."amplitude", s."turnoverrate"
                    FROM "cn_stock_attention" a
                    LEFT JOIN "cn_stock_spot" s
                           ON s."code" = a."code" AND s."date" = %s
                    ORDER BY a."datetime" DESC
                    """,
                    latest
                )
            else:
                rows = self.db.query(
                    'SELECT "datetime", "code" FROM "cn_stock_attention" ORDER BY "datetime" DESC'
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
                'INSERT INTO "cn_stock_attention"("datetime","code") VALUES(%s,%s) ON CONFLICT ("code") DO UPDATE SET "datetime"=EXCLUDED."datetime"',
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
            self.db.execute('DELETE FROM "cn_stock_attention" WHERE "code"=%s', code)
            self.write(json.dumps({'ok': True}))
        except Exception as e:
            self.set_status(500)
            self.write(json.dumps({'error': str(e)}, ensure_ascii=False))


class ApiOperationLogHandler(webBase.BaseHandler, ABC):
    """每日操作记录：供超短主线接力复盘使用"""

    _editable_fields = [
        'trade_date', 'trade_time', 'code', 'name', 'action', 'price', 'quantity',
        'mainline', 'strategy', 'reason', 'result', 'follow_plan',
        'system_judgment', 'signal_strategy', 'signal_snapshot_time',
        'signal_core_score', 'signal_mode',
        'signal_amount_ratio', 'signal_risk'
    ]

    def _write_headers(self):
        self.set_header('Content-Type', 'application/json;charset=UTF-8')
        self.set_header('Access-Control-Allow-Origin', '*')

    def _payload(self):
        body = json.loads(self.request.body or '{}')
        return {
            'trade_date': str(body.get('trade_date') or '').strip(),
            'trade_time': str(body.get('trade_time') or '').strip()[:5],
            'code': _clean_code(body.get('code')),
            'name': str(body.get('name') or '').strip(),
            'action': str(body.get('action') or '').strip(),
            'price': _num_or_none(body.get('price')),
            'quantity': _num_or_none(body.get('quantity')),
            'mainline': str(body.get('mainline') or '').strip(),
            'strategy': str(body.get('strategy') or '超短主线接力').strip(),
            'reason': str(body.get('reason') or '').strip(),
            'result': str(body.get('result') or '').strip(),
            'follow_plan': str(body.get('follow_plan') or '').strip(),
            'system_judgment': str(body.get('system_judgment') or '').strip(),
            'signal_strategy': str(body.get('signal_strategy') or '').strip(),
            'signal_snapshot_time': str(body.get('signal_snapshot_time') or '').strip()[:5],
            'signal_core_score': _num_or_none(body.get('signal_core_score')),
            'signal_mode': str(body.get('signal_mode') or '').strip(),
            'signal_amount_ratio': _num_or_none(body.get('signal_amount_ratio')),
            'signal_risk': str(body.get('signal_risk') or '').strip(),
        }

    def get(self):
        self._write_headers()
        try:
            _ensure_operation_log_table(self.db)
            date = self.get_argument('date', default='', strip=True)
            code = _clean_code(self.get_argument('code', default='', strip=True))
            page = int(self.get_argument('page', default='1', strip=True))
            page_size = min(int(self.get_argument('size', default='200', strip=True)), 1000)
            offset = (max(page, 1) - 1) * page_size

            conditions = []
            params = []
            if date:
                conditions.append('trade_date = %s')
                params.append(date)
            if code:
                conditions.append('code = %s')
                params.append(code)
            where_sql = (' WHERE ' + ' AND '.join(conditions)) if conditions else ''
            total_row = self.db.get(
                f'SELECT COUNT(*) AS cnt FROM "{_OPERATION_LOG_TABLE}"{where_sql}',
                *params
            )
            rows = self.db.query(
                f'''
                SELECT *
                FROM "{_OPERATION_LOG_TABLE}"
                {where_sql}
                ORDER BY trade_date DESC, trade_time DESC NULLS LAST, id DESC
                LIMIT %s OFFSET %s
                ''',
                *(params + [page_size, offset])
            )
            all_operation_rows = self.db.query(
                f'''
                SELECT id, trade_date, trade_time, code, action, price, quantity
                FROM "{_OPERATION_LOG_TABLE}"
                ORDER BY trade_date ASC, trade_time ASC NULLS LAST, id ASC
                '''
            )
            pnl_map = _operation_pnl_map(all_operation_rows)
            for row in rows or []:
                row['pnl_pct'] = pnl_map.get(row['id'])
            self.write(json.dumps({
                'ok': True,
                'total': total_row['cnt'] if total_row else 0,
                'page': page,
                'size': page_size,
                'data': rows,
            }, cls=MyEncoder, ensure_ascii=False))
        except Exception as e:
            self.set_status(500)
            self.write(json.dumps({'ok': False, 'error': str(e)}, ensure_ascii=False))

    def post(self):
        self._write_headers()
        try:
            _ensure_operation_log_table(self.db)
            item = _enrich_operation_signal(self._payload())
            if not item['trade_date'] or not item['code'] or not item['action']:
                self.set_status(400)
                self.write(json.dumps({'ok': False, 'error': 'trade_date, code and action required'}, ensure_ascii=False))
                return
            insert_fields = [
                'trade_date', 'trade_time', 'code', 'name', 'action', 'price', 'quantity',
                'mainline', 'strategy', 'reason', 'result', 'follow_plan',
                'system_judgment', 'signal_strategy', 'signal_snapshot_time',
                'signal_core_score', 'signal_mode',
                'signal_amount_ratio', 'signal_risk'
            ]
            insert_columns = ', '.join(insert_fields)
            insert_placeholders = ','.join(['%s'] * len(insert_fields))
            row = self.db.get(
                f'''
                INSERT INTO "{_OPERATION_LOG_TABLE}"
                ({insert_columns})
                VALUES ({insert_placeholders})
                RETURNING *
                ''',
                *[item[field] for field in insert_fields]
            )
            self.write(json.dumps({'ok': True, 'data': row}, cls=MyEncoder, ensure_ascii=False))
        except Exception as e:
            self.set_status(500)
            self.write(json.dumps({'ok': False, 'error': str(e)}, ensure_ascii=False))

    def put(self):
        self._write_headers()
        try:
            _ensure_operation_log_table(self.db)
            body = json.loads(self.request.body or '{}')
            item_id = int(body.get('id') or 0)
            if not item_id:
                self.set_status(400)
                self.write(json.dumps({'ok': False, 'error': 'id required'}, ensure_ascii=False))
                return
            item = _enrich_operation_signal(self._payload())
            if not item['trade_date'] or not item['code'] or not item['action']:
                self.set_status(400)
                self.write(json.dumps({'ok': False, 'error': 'trade_date, code and action required'}, ensure_ascii=False))
                return
            row = self.db.get(
                f'''
                UPDATE "{_OPERATION_LOG_TABLE}"
                SET trade_date=%s, trade_time=%s, code=%s, name=%s, action=%s,
                    price=%s, quantity=%s, mainline=%s, strategy=%s,
                    reason=%s, result=%s, follow_plan=%s,
                    system_judgment=%s, signal_strategy=%s, signal_snapshot_time=%s,
                    signal_core_score=%s, signal_mode=%s,
                    signal_amount_ratio=%s, signal_risk=%s,
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=%s
                RETURNING *
                ''',
                item['trade_date'], item['trade_time'], item['code'], item['name'], item['action'],
                item['price'], item['quantity'], item['mainline'], item['strategy'],
                item['reason'], item['result'], item['follow_plan'],
                item['system_judgment'], item['signal_strategy'], item['signal_snapshot_time'],
                item['signal_core_score'], item['signal_mode'],
                item['signal_amount_ratio'], item['signal_risk'],
                item_id
            )
            self.write(json.dumps({'ok': True, 'data': row}, cls=MyEncoder, ensure_ascii=False))
        except Exception as e:
            self.set_status(500)
            self.write(json.dumps({'ok': False, 'error': str(e)}, ensure_ascii=False))

    def delete(self):
        self._write_headers()
        try:
            _ensure_operation_log_table(self.db)
            item_id = int(self.get_argument('id', default='0', strip=True))
            if not item_id:
                self.set_status(400)
                self.write(json.dumps({'ok': False, 'error': 'id required'}, ensure_ascii=False))
                return
            self.db.execute(f'DELETE FROM "{_OPERATION_LOG_TABLE}" WHERE id=%s', item_id)
            self.write(json.dumps({'ok': True}))
        except Exception as e:
            self.set_status(500)
            self.write(json.dumps({'ok': False, 'error': str(e)}, ensure_ascii=False))


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
                conds.append('s."date" = %s'); params.append(date)
            if search:
                kw = f'%{search}%'
                conds.append('(s."code" LIKE %s OR s."name" LIKE %s)')
                params.extend([kw, kw])
            where = ('WHERE ' + ' AND '.join(conds)) if conds else ''

            cnt_r = self.db.get('SELECT COUNT(*) AS cnt FROM "' + table + '" s ' + where, *params)
            total = cnt_r['cnt'] if cnt_r else 0

            # 只做一次 LEFT JOIN 拿最新价，其余字段全部直接读库
            data_sql = (
                'SELECT s.*, '
                'CAST(sp."new_price" AS NUMERIC(12,4)) AS current_price, '
                'CAST(sp."change_rate" AS NUMERIC(12,4)) AS today_change, '
                'CASE WHEN CAST(s."signal_close" AS NUMERIC) > 0 '
                '     THEN ROUND((CAST(sp."new_price" AS NUMERIC) - CAST(s."signal_close" AS NUMERIC)) '
                '          / CAST(s."signal_close" AS NUMERIC) * 100, 2) '
                '     ELSE NULL END AS chg_from_signal '
                'FROM "' + table + '" s '
                'LEFT JOIN "cn_stock_spot" sp '
                '    ON sp."code" = s."code" '
                '    AND sp."date" = (SELECT MAX("date") FROM "cn_stock_spot") '
                + where +
                ' ORDER BY s."date" DESC, chg_from_signal DESC'
                ' LIMIT ' + str(size) + ' OFFSET ' + str(offset)
            )
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


class ApiMinuteKlineHandler(webBase.BaseHandler, ABC):
    """
    GET /api/minute_kline?code=000001&date=2026-06-01
    返回指定日期的1分钟K线数据。
    优先从 XTick API 拉取（实时/历史），数据库作回退。
    """

    def get(self):
        code = self.get_argument('code', default=None, strip=False)
        date = self.get_argument('date', default=None, strip=False)

        self.set_header('Content-Type', 'application/json;charset=UTF-8')
        self.set_header('Access-Control-Allow-Origin', '*')

        if not code or not date:
            self.set_status(400)
            self.write(json.dumps({'error': 'code and date required'}))
            return

        try:
            result = self._fetch_from_xtick(code, date)
            if not result:
                result = self._fetch_from_db(code, date)
            self.write(json.dumps(result, ensure_ascii=False))
        except Exception as e:
            self.set_status(500)
            self.write(json.dumps({'error': str(e)}, ensure_ascii=False))

    def _fetch_from_xtick(self, code: str, date: str) -> list:
        """从 XTick /doc/kline/market?period=1m 拉取历史分钟数据"""
        try:
            from instock.core.minute_bar_collector import MinuteBarFetcher
            fetcher = MinuteBarFetcher()
            bars = fetcher.fetch_history(code, date)
            if not bars:
                return []
            # 按时间过滤只保留当天（time字段已是HH:MM，date由时间戳决定）
            result = []
            for b in bars:
                result.append({
                    'time':      b['time'],
                    'open':      b['open'],
                    'close':     b['close'],
                    'high':      b['high'],
                    'low':       b['low'],
                    'volume':    b['volume'],
                    'amount':    b['amount'],
                    'pre_close': b['pre_close'],
                })
            return sorted(result, key=lambda x: x['time'])
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"XTick分钟数据拉取失败，回退到数据库: {e}")
            return []

    def _fetch_from_db(self, code: str, date: str) -> list:
        """从本地数据库 cn_stock_minute_bar 读取"""
        try:
            sql = (
                'SELECT time, open, close, high, low, volume, amount, pre_close'
                ' FROM cn_stock_minute_bar'
                ' WHERE code = %s AND date = %s'
                ' ORDER BY time'
            )
            rows = self.db.query(sql, code, date)
            result = []
            for r in rows:
                result.append({
                    'time':      r['time'],
                    'open':      float(r['open']      or 0),
                    'close':     float(r['close']     or 0),
                    'high':      float(r['high']      or 0),
                    'low':       float(r['low']       or 0),
                    'volume':    float(r['volume']    or 0),
                    'amount':    float(r['amount']    or 0),
                    'pre_close': float(r['pre_close'] or 0),
                })
            return result
        except Exception:
            return []
