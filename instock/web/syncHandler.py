#!/usr/local/bin/python3
# -*- coding: utf-8 -*-

import json
import logging
import queue
import threading
import datetime
import time
from abc import ABC

import tornado.web
import tornado.iostream
import tornado.ioloop
from tornado import gen

import instock.web.base as webBase
import instock.lib.trade_time as trd
import instock.lib.database as mdb
import instock.core.tablestructure as tbs

__author__ = 'myh '
__date__ = '2024/1/1 '

# ─── 全局状态 ────────────────────────────────────────────────────────────────
_task_status: dict = {}
_task_lock = threading.Lock()

# task_key -> list[Queue]，支持多 SSE 客户端同时监听同一任务
_task_queues: dict[str, list[queue.Queue]] = {}
_queues_lock = threading.Lock()


# ─── 日志推送工具 ─────────────────────────────────────────────────────────────

def _push(task_key: str, line: str):
    with _queues_lock:
        for q in _task_queues.get(task_key, []):
            try:
                q.put_nowait(line)
            except queue.Full:
                pass


def _done(task_key: str):
    _push(task_key, '__DONE__')


# ─── 核心：按日期范围直接调 Tushare daily 入库 ────────────────────────────────

def _sync_stock_spot_by_date(task_key: str, start_date: str, end_date: str):
    """
    直接用 Tushare daily + daily_basic + stock_basic 按日循环入库，
    不依赖 sys.argv / run_template，所有过程实时推送到 SSE。
    """
    try:
        from instock.core.crawling.stock_hist_tushare import fetcher
        import numpy as np
        import pandas as pd

        if not fetcher.is_available():
            _push(task_key, '❌ Tushare 不可用，请检查 token 配置')
            return False

        # 解析日期范围
        sd = datetime.datetime.strptime(start_date, '%Y-%m-%d').date()
        ed = datetime.datetime.strptime(end_date, '%Y-%m-%d').date()

        _push(task_key, f'📋 获取股票基本信息...')
        stock_basic = fetcher.pro.stock_basic(
            exchange='', list_status='L',
            fields='ts_code,symbol,name,industry,list_date,market'
        )
        _push(task_key, f'✔ 股票基本信息：共 {len(stock_basic)} 只')

        # 收集全部交易日（直接查 Tushare，不依赖本地单例缓存）
        trade_days = _get_trade_days(start_date, end_date)

        if not trade_days:
            _push(task_key, f'⚠ {start_date} ~ {end_date} 区间内无交易日，跳过')
            return True

        _push(task_key, f'📅 共 {len(trade_days)} 个交易日待同步')

        success_days, skip_days, fail_days = 0, 0, 0

        for i, trade_date in enumerate(trade_days):
            date_str = trade_date.strftime('%Y%m%d')
            date_disp = trade_date.strftime('%Y-%m-%d')
            _push(task_key, f'[{i+1}/{len(trade_days)}] {date_disp} 开始拉取...')

            try:
                # ── daily ──────────────────────────────────────
                daily = fetcher.pro.daily(trade_date=date_str)
                if daily is None or daily.empty:
                    _push(task_key, f'  ⚠ {date_disp} daily 返回空，跳过')
                    skip_days += 1
                    continue
                _push(task_key, f'  daily: {len(daily)} 条')

                # ── daily_basic ────────────────────────────────
                try:
                    daily_basic = fetcher.pro.daily_basic(trade_date=date_str)
                    daily_basic = daily_basic.drop(columns=['close'], errors='ignore')
                    _push(task_key, f'  daily_basic: {len(daily_basic)} 条')
                except Exception as e:
                    _push(task_key, f'  ⚠ daily_basic 获取失败: {e}，使用空表')
                    daily_basic = pd.DataFrame()

                # ── 合并 ───────────────────────────────────────
                result = pd.merge(stock_basic, daily, on='ts_code', how='inner')
                if not daily_basic.empty:
                    result = pd.merge(result, daily_basic, on=['ts_code', 'trade_date'], how='left')

                # ── 构造入库 DataFrame ─────────────────────────
                result = result.reset_index(drop=True)

                def _col(name, default=np.nan):
                    return result[name] if name in result.columns else pd.Series(
                        [default] * len(result), index=result.index)

                out = pd.DataFrame(index=result.index)
                out['date']               = trade_date.strftime('%Y-%m-%d')
                out['code']               = result['symbol']
                out['name']               = result['name']
                out['new_price']          = _col('close')
                out['change_rate']        = _col('pct_chg')
                out['ups_downs']          = _col('change')
                out['volume']             = _col('vol')
                out['deal_amount']        = _col('amount')
                out['amplitude']          = ((_col('high') - _col('low')) / _col('pre_close') * 100).round(2)
                out['turnoverrate']       = _col('turnover_rate')
                out['volume_ratio']       = _col('volume_ratio')
                out['open_price']         = _col('open')
                out['high_price']         = _col('high')
                out['low_price']          = _col('low')
                out['pre_close_price']    = _col('pre_close')
                out['speed_increase']     = np.nan
                out['speed_increase_5']   = np.nan
                out['speed_increase_60']  = np.nan
                out['speed_increase_all'] = np.nan
                out['dtsyl']              = _col('pe')
                out['pe9']                = _col('pe')
                out['pe']                 = _col('pe_ttm')
                out['pbnewmrq']           = _col('pb')
                out['basic_eps']          = np.nan
                out['bvps']               = np.nan
                out['per_capital_reserve']  = np.nan
                out['per_unassign_profit']  = np.nan
                out['roe_weight']           = np.nan
                out['sale_gpr']             = np.nan
                out['debt_asset_ratio']     = np.nan
                out['total_operate_income'] = np.nan
                out['toi_yoy_ratio']        = np.nan
                out['parent_netprofit']     = np.nan
                out['netprofit_yoy_ratio']  = np.nan
                out['report_date']          = np.nan
                out['total_shares']         = _col('total_share')
                out['free_shares']          = _col('free_share')
                out['total_market_cap']     = _col('total_mv')
                out['free_cap']             = _col('circ_mv')
                out['industry']             = result['industry']
                out['listing_date']         = result['list_date']

                # 过滤无价格行
                out = out[out['new_price'].notna() & (out['new_price'] > 0)].copy()

                # ── 入库 ──────────────────────────────────────
                table_name = tbs.TABLE_CN_STOCK_SPOT['name']
                if mdb.checkTableIsExist(table_name):
                    mdb.executeSql(f"DELETE FROM `{table_name}` WHERE `date` = '{trade_date}'")
                    cols_type = None
                else:
                    cols_type = tbs.get_field_types(tbs.TABLE_CN_STOCK_SPOT['columns'])

                mdb.insert_db_from_df(out, table_name, cols_type, False, '`date`,`code`')
                _push(task_key, f'  ✅ 入库 {len(out)} 条 → {table_name}')
                success_days += 1

            except Exception as e:
                _push(task_key, f'  ❌ {date_disp} 处理失败: {e}')
                fail_days += 1

            # Tushare 限速：每次请求后稍作等待
            time.sleep(0.5)

        _push(task_key, f'')
        _push(task_key, f'📊 汇总：成功 {success_days} 天 / 跳过 {skip_days} 天 / 失败 {fail_days} 天')
        return fail_days == 0

    except Exception as e:
        _push(task_key, f'❌ 同步异常: {e}')
        logging.error(f'_sync_stock_spot_by_date error: {e}', exc_info=True)
        return False


def _get_trade_days(start_date: str, end_date: str) -> list:
    """
    从 Tushare 直接获取区间交易日，不依赖单例缓存。
    返回 list[datetime.date]，按升序排列。
    """
    try:
        from instock.core.crawling.tushare_data import tushare_data as tsd
        sd = start_date.replace('-', '')
        ed = end_date.replace('-', '')
        df = tsd.pro.trade_cal(exchange='SSE', start_date=sd, end_date=ed, is_open='1')
        if df is None or df.empty:
            return []
        dates = sorted([
            datetime.datetime.strptime(str(d), '%Y%m%d').date()
            for d in df['cal_date'].tolist()
        ])
        return dates
    except Exception as e:
        logging.warning(f"Tushare 获取交易日历失败，回退到本地判断: {e}")
        # 回退：遍历日期用本地缓存
        sd = datetime.datetime.strptime(start_date, '%Y-%m-%d').date()
        ed = datetime.datetime.strptime(end_date, '%Y-%m-%d').date()
        result = []
        cur = sd
        while cur <= ed:
            if trd.is_trade_date(cur):
                result.append(cur)
            cur += datetime.timedelta(days=1)
        return result
    """清除 stock_data / stock_hist_data 单例缓存，确保下一次获取新日期的数据"""
    from instock.core.singleton_stock import stock_data, stock_hist_data
    for cls in (stock_data, stock_hist_data):
        try:
            if hasattr(cls, '_instance'):
                del cls._instance
        except Exception:
            pass


def _run_all_strategies(date):
    """对单个日期运行所有 10 种量化策略"""
    import concurrent.futures
    import instock.core.tablestructure as tbs
    import instock.job.strategy_data_daily_job as strategy_job
    with concurrent.futures.ThreadPoolExecutor() as executor:
        futures = [executor.submit(strategy_job.prepare, date, strategy)
                   for strategy in tbs.TABLE_CN_STOCK_STRATEGIES]
        for f in concurrent.futures.as_completed(futures):
            try:
                f.result()
            except Exception as e:
                logging.error(f'_run_all_strategies error for {date}: {e}')


def _run_custom_strategies(date):
    """对单个日期运行所有自有策略并写库"""
    import instock.job.custom_strategy_daily_job as custom_job
    custom_job.prepare_custom(date)


def _run_generic_job_with_dates(task_key: str, job_func, start_date: str, end_date: str):
    """
    通用日期循环执行器：遍历交易日，逐日调用 job_func(date)。
    适用于指标、K线形态、策略等依赖已入库行情数据的任务。
    """
    try:
        import instock.lib.run_template as runt

        # 直接从 Tushare 获取交易日，不依赖本地单例缓存
        trade_days = _get_trade_days(start_date, end_date)

        if not trade_days:
            _push(task_key, f'⚠ 区间内无交易日，跳过')
            return True

        _push(task_key, f'📅 共 {len(trade_days)} 个交易日待处理')
        success, fail = 0, 0
        for i, trade_date in enumerate(trade_days):
            date_disp = trade_date.strftime('%Y-%m-%d')
            _push(task_key, f'[{i+1}/{len(trade_days)}] {date_disp} 处理中...')
            try:
                _clear_singletons()   # 每天清缓存，防止单例跨日复用
                job_func(trade_date)
                _push(task_key, f'  ✅ {date_disp} 完成')
                success += 1
            except Exception as e:
                _push(task_key, f'  ❌ {date_disp} 失败: {e}')
                fail += 1
            time.sleep(0.1)

        _push(task_key, f'')
        _push(task_key, f'📊 汇总：成功 {success} 天 / 失败 {fail} 天')
        return fail == 0

    except Exception as e:
        _push(task_key, f'❌ 执行异常: {e}')
        logging.error(f'_run_generic_job_with_dates error: {e}', exc_info=True)
        return False


# ─── 后台任务入口 ─────────────────────────────────────────────────────────────

def _run_task(task_key: str, job_info: dict, start_date: str, end_date: str):
    """统一的后台执行入口，根据任务类型选择合适的执行器"""
    with _task_lock:
        _task_status[task_key] = {
            'status': 'running',
            'start_time': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'end_time': None,
            'message': '正在执行...',
        }

    _push(task_key, f'▶ [{job_info["name"]}] 开始执行')
    _push(task_key, f'   日期范围: {start_date} ~ {end_date}')

    ok = False
    try:
        runner = job_info.get('runner', 'generic')

        if runner == 'stock_spot':
            # 专用：股票行情直接按日拉 Tushare daily
            ok = _sync_stock_spot_by_date(task_key, start_date, end_date)

        elif runner == 'generic_single':
            # 通用：逐日调用 job_func(date)
            ok = _run_generic_job_with_dates(task_key, job_info['single_func'], start_date, end_date)

        else:
            # 旧式：直接调 main()，不支持日期范围（仅当日）
            _push(task_key, f'  ⚠ 此任务不支持日期范围，将使用系统当前交易日执行')
            job_info['func']()
            _push(task_key, f'  ✅ 执行完成')
            ok = True

        status = 'success' if ok else 'error'
        msg = '执行完成' if ok else '部分失败，请查看日志'
        _push(task_key, f'{"✅" if ok else "⚠"} 任务结束')

    except Exception as e:
        logging.error(f'task [{task_key}] error: {e}', exc_info=True)
        _push(task_key, f'❌ 任务异常终止: {e}')
        status, msg = 'error', str(e)

    with _task_lock:
        _task_status[task_key].update({
            'status': status,
            'end_time': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'message': msg,
        })

    _done(task_key)


# ─── 任务注册表 ───────────────────────────────────────────────────────────────

def _get_jobs():
    import instock.job.basic_data_daily_job as basic_job
    import instock.job.basic_data_other_daily_job as other_job
    import instock.job.indicators_data_daily_job as indicator_job
    import instock.job.klinepattern_data_daily_job as kline_job
    import instock.job.selection_data_daily_job as selection_job
    import instock.job.strategy_data_daily_job as strategy_job
    import instock.job.basic_data_after_close_daily_job as after_close_job
    import instock.job.backtest_data_daily_job as backtest_job

    return [
        {
            'key': 'basic_stock_spot',
            'group': '基础行情数据',
            'name': '股票/ETF 实时行情',
            'desc': '逐日调用 Tushare daily 接口同步 A 股每日行情数据并入库',
            'icon': 'fa-bar-chart',
            'color': '#4a90d9',
            'runner': 'stock_spot',           # 专用执行器
            'func': basic_job.main,
        },
        {
            'key': 'selection',
            'group': '基础行情数据',
            'name': '综合选股数据',
            'desc': '同步 200+ 维综合选股筛选结果',
            'icon': 'fa-filter',
            'color': '#4a90d9',
            'runner': 'generic_single',
            # save_nph_stock_selection_data(date, before=True) — 需传 before=False 才真正执行
            'single_func': lambda d: selection_job.save_nph_stock_selection_data(d, before=False),
            'func': selection_job.main,
        },
        {
            'key': 'lhb_fund_bonus',
            'group': '基础行情数据',
            'name': '龙虎榜/资金流/分红/涨停原因',
            'desc': '同步龙虎榜、个股资金流向、行业概念资金流、分红送配、涨停原因',
            'icon': 'fa-money',
            'color': '#4a90d9',
            'runner': 'default',
            'func': other_job.main,
        },
        {
            'key': 'after_close',
            'group': '基础行情数据',
            'name': '大宗交易/北向资金',
            'desc': '同步大宗交易、ETF 行情等收盘后数据（请收盘2小时后执行）',
            'icon': 'fa-exchange',
            'color': '#4a90d9',
            'runner': 'default',
            'func': after_close_job.main,
        },
        {
            'key': 'indicators',
            'group': '技术分析数据',
            'name': '技术指标（32+ 项）',
            'desc': '计算全市场 MACD、KDJ、BOLL、RSI 等 32+ 项技术指标（需先同步行情）',
            'icon': 'fa-line-chart',
            'color': '#27ae60',
            'runner': 'generic_single',
            'single_func': indicator_job.prepare,
            'func': indicator_job.main,
        },
        {
            'key': 'kline_pattern',
            'group': '技术分析数据',
            'name': 'K 线形态识别（61 种）',
            'desc': '识别全市场 61 种 K 线形态（含买入/卖出信号）',
            'icon': 'fa-area-chart',
            'color': '#27ae60',
            'runner': 'generic_single',
            'single_func': kline_job.prepare,
            'func': kline_job.main,
        },
        {
            'key': 'strategy',
            'group': '策略选股数据',
            'name': '量化策略选股（10 种）',
            'desc': '运行放量上涨、海龟交易、突破平台、回踩年线等 10 种量化策略',
            'icon': 'fa-rocket',
            'color': '#e67e22',
            'runner': 'generic_single',
            'single_func': lambda d: _run_all_strategies(d),
            'func': strategy_job.main,
        },
        {
            'key': 'backtest',
            'group': '策略选股数据',
            'name': '策略回测（1~60 日收益）',
            'desc': '对策略选股结果进行 1/3/5/10/20/60 日历史收益率回测',
            'icon': 'fa-history',
            'runner': 'generic_single',
            'color': '#e67e22',
            'single_func': lambda d: backtest_job.prepare(),
            'func': backtest_job.main,
        },
        {
            'key': 'custom_strategy',
            'group': '自有策略',
            'name': '自有策略（爆量股票等）',
            'desc': '运行所有自定义策略，包括爆量股票等',
            'icon': 'fa-magic',
            'color': '#8e44ad',
            'runner': 'generic_single',
            'single_func': lambda d: _run_custom_strategies(d),
            'func': lambda: None,
        },
    ]


# ─── Tornado Handlers ─────────────────────────────────────────────────────────

class SyncPageHandler(webBase.BaseHandler, ABC):
    @gen.coroutine
    def get(self):
        jobs = _get_jobs()
        groups = {}
        for job in jobs:
            g = job['group']
            if g not in groups:
                groups[g] = []
            groups[g].append(job)

        with _task_lock:
            status_snapshot = dict(_task_status)

        today = datetime.date.today().strftime('%Y-%m-%d')

        self.render(
            'stock_sync.html',
            groups=groups,
            task_status=status_snapshot,
            today=today,
            leftMenu=webBase.GetLeftMenu(self.request.uri),
        )


class SyncApiHandler(webBase.BaseHandler, ABC):
    """POST /instock/api/sync  — 触发任务"""
    def post(self):
        try:
            body = json.loads(self.request.body)
            job_key    = body.get('key', '')
            start_date = body.get('start_date', '') or datetime.date.today().strftime('%Y-%m-%d')
            end_date   = body.get('end_date', '')   or start_date
        except Exception:
            job_key    = self.get_argument('key', '')
            start_date = self.get_argument('start_date', '') or datetime.date.today().strftime('%Y-%m-%d')
            end_date   = self.get_argument('end_date', '')   or start_date

        jobs_map = {j['key']: j for j in _get_jobs()}
        if job_key not in jobs_map:
            self.set_status(400)
            self.write({'status': 'error', 'message': f'未知任务：{job_key}'})
            return

        with _task_lock:
            if _task_status.get(job_key, {}).get('status') == 'running':
                self.write({'status': 'running', 'message': '任务正在执行中，请稍候...'})
                return

        job = jobs_map[job_key]
        t = threading.Thread(
            target=_run_task,
            args=(job_key, job, start_date, end_date),
            daemon=True,
        )
        t.start()

        self.write({'status': 'started', 'key': job_key,
                    'message': f'任务 [{job["name"]}] 已开始执行'})


class SyncLogSSEHandler(webBase.BaseHandler, ABC):
    """GET /instock/api/sync/log?key=xxx  — SSE 实时日志流（Tornado 6+ 兼容）"""

    def get(self):
        task_key = self.get_argument('key', '')
        if not task_key:
            self.set_status(400)
            self.finish('missing key')
            return

        self.set_header('Content-Type', 'text/event-stream; charset=utf-8')
        self.set_header('Cache-Control', 'no-cache')
        self.set_header('X-Accel-Buffering', 'no')
        self.set_header('Connection', 'keep-alive')

        q: queue.Queue = queue.Queue(maxsize=2000)
        with _queues_lock:
            _task_queues.setdefault(task_key, []).append(q)

        self._q = q
        self._task_key = task_key
        self._closed = False
        self._auto_finish = False

        tornado.ioloop.IOLoop.current().call_later(0.1, self._poll)

    def _poll(self):
        if self._closed:
            self._cleanup()
            return
        try:
            stream = self.request.connection.stream
            if stream.closed():
                self._cleanup()
                return
        except Exception:
            self._cleanup()
            return

        try:
            flushed = False
            while True:
                try:
                    line = self._q.get_nowait()
                except queue.Empty:
                    break

                if line == '__DONE__':
                    self.write('data: __DONE__\n\n')
                    self.flush()
                    self._cleanup()
                    self.finish()
                    return

                safe = line.replace('\r', '').replace('\n', ' ')
                self.write(f'data: {safe}\n\n')
                flushed = True

            if flushed:
                self.flush()

        except (tornado.iostream.StreamClosedError, RuntimeError):
            self._cleanup()
            return
        except Exception:
            pass

        tornado.ioloop.IOLoop.current().call_later(0.2, self._poll)

    def on_connection_close(self):
        self._closed = True
        self._cleanup()

    def _cleanup(self):
        with _queues_lock:
            qs = _task_queues.get(self._task_key, [])
            if self._q in qs:
                qs.remove(self._q)


class SyncStatusApiHandler(webBase.BaseHandler, ABC):
    """GET /instock/api/sync/status  — 所有任务状态快照"""
    def get(self):
        with _task_lock:
            snap = dict(_task_status)
        self.set_header('Content-Type', 'application/json;charset=UTF-8')
        self.write(json.dumps(snap, ensure_ascii=False))
