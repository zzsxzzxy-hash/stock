#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tushare API 数据获取封装模块
替代东方财富网爬虫，提供龙虎榜/资金流/大宗交易/分红/ETF/选股等数据
"""
import os
import sys
import time
import logging
import pandas as pd
import datetime

cpath_current = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))

try:
    import tushare as ts
    TUSHARE_AVAILABLE = True
except ImportError:
    TUSHARE_AVAILABLE = False


class TushareData:
    def __init__(self):
        self.token = self._get_token()
        self.pro = None
        if TUSHARE_AVAILABLE and self.token:
            self._init_pro()

    def _get_token(self):
        token = os.environ.get('TUSHARE_TOKEN')
        if token:
            return token
        token_file = os.path.join(cpath_current, 'config', 'tushare_token.txt')
        if os.path.exists(token_file):
            with open(token_file, 'r') as f:
                token = f.read().strip()
                if token:
                    return token
        return None

    def _init_pro(self):
        try:
            self.pro = ts.pro_api(self.token)
            return True
        except Exception as e:
            print(f"Tushare初始化失败: {e}")
            return False

    def is_available(self):
        return TUSHARE_AVAILABLE and self.token is not None and self.pro is not None

    @staticmethod
    def _to_ts_code(symbol):
        """将6位股票代码转为ts_code格式"""
        symbol = str(symbol).strip()
        if '.' in symbol:
            return symbol
        if symbol.startswith(('6', '9')):
            return f"{symbol}.SH"
        return f"{symbol}.SZ"

    @staticmethod
    def _extract_symbol(ts_code):
        """从ts_code提取6位代码"""
        return ts_code.split('.')[0] if '.' in str(ts_code) else str(ts_code)

    # ==================== 龙虎榜 ====================

    def get_lhb_detail(self, start_date, end_date):
        """
        龙虎榜详情（兼容东方财富stock_lhb_detail_em输出格式）
        Tushare接口: top_list
        列: trade_date, ts_code, name, close, pct_change, turnover_rate, amount,
            l_sell, l_buy, l_amount, net_amount, net_rate, amount_rate, float_values, reason
        """
        if not self.is_available():
            return pd.DataFrame()
        try:
            start_dt = start_date.replace('-', '')
            end_dt = end_date.replace('-', '')
            # 逐日获取
            all_dfs = []
            current = datetime.datetime.strptime(start_dt, '%Y%m%d')
            end = datetime.datetime.strptime(end_dt, '%Y%m%d')
            while current <= end:
                trade_date = current.strftime('%Y%m%d')
                try:
                    df = self.pro.top_list(trade_date=trade_date)
                    if df is not None and not df.empty:
                        all_dfs.append(df)
                except Exception:
                    pass
                current += datetime.timedelta(days=1)
                time.sleep(0.3)

            if not all_dfs:
                return pd.DataFrame()

            big_df = pd.concat(all_dfs, ignore_index=True)
            big_df['symbol'] = big_df['ts_code'].apply(self._extract_symbol)

            # 安全获取列
            def _safe_col(col, default=0):
                return pd.to_numeric(big_df[col], errors='coerce').fillna(default) if col in big_df.columns else default

            result = pd.DataFrame({
                '代码': big_df['symbol'],
                '名称': big_df['name'],
                '上榜日': pd.to_datetime(big_df['trade_date']).dt.date,
                '解读': '',
                '收盘价': _safe_col('close'),
                '涨跌幅': _safe_col('pct_change'),
                '龙虎榜净买额': _safe_col('net_amount'),
                '龙虎榜买入额': _safe_col('l_buy'),
                '龙虎榜卖出额': _safe_col('l_sell'),
                '龙虎榜成交额': _safe_col('l_amount'),
                '市场总成交额': _safe_col('amount'),
                '净买额占总成交比': _safe_col('net_rate'),
                '成交额占总成交比': _safe_col('amount_rate'),
                '换手率': _safe_col('turnover_rate'),
                '流通市值': _safe_col('float_values'),
                '上榜原因': big_df['reason'] if 'reason' in big_df.columns else '',
                '上榜后1日': 0,
                '上榜后2日': 0,
                '上榜后5日': 0,
                '上榜后10日': 0,
            })
            return result
        except Exception as e:
            logging.error(f"Tushare获取龙虎榜详情失败: {e}")
            return pd.DataFrame()

    def get_lhb_jgmmtj(self, start_date, end_date):
        """
        龙虎榜机构买卖每日统计（兼容东方财富stock_lhb_jgmmtj_em输出格式）
        Tushare接口: top_list
        """
        if not self.is_available():
            return pd.DataFrame()
        try:
            start_dt = start_date.replace('-', '')
            end_dt = end_date.replace('-', '')
            all_dfs = []
            current = datetime.datetime.strptime(start_dt, '%Y%m%d')
            end = datetime.datetime.strptime(end_dt, '%Y%m%d')
            while current <= end:
                trade_date = current.strftime('%Y%m%d')
                try:
                    df = self.pro.top_list(trade_date=trade_date)
                    if df is not None and not df.empty:
                        all_dfs.append(df)
                except Exception:
                    pass
                current += datetime.timedelta(days=1)
                time.sleep(0.3)

            if not all_dfs:
                return pd.DataFrame()

            big_df = pd.concat(all_dfs, ignore_index=True)
            big_df['symbol'] = big_df['ts_code'].apply(self._extract_symbol)

            def _safe_col(col, default=0):
                return pd.to_numeric(big_df[col], errors='coerce').fillna(default) if col in big_df.columns else default

            result = pd.DataFrame({
                '序号': range(1, len(big_df) + 1),
                '代码': big_df['symbol'],
                '名称': big_df['name'],
                '收盘价': _safe_col('close'),
                '涨跌幅': _safe_col('pct_change'),
                '买方机构数': 0,
                '卖方机构数': 0,
                '机构买入总额': _safe_col('l_buy'),
                '机构卖出总额': _safe_col('l_sell'),
                '机构买入净额': _safe_col('net_amount'),
                '市场总成交额': _safe_col('amount'),
                '机构净买额占总成交额比': _safe_col('net_rate'),
                '换手率': _safe_col('turnover_rate'),
                '流通市值': _safe_col('float_values'),
                '上榜原因': big_df['reason'] if 'reason' in big_df.columns else '',
                '上榜日期': pd.to_datetime(big_df['trade_date']).dt.date,
            })
            return result
        except Exception as e:
            logging.error(f"Tushare获取机构买卖统计失败: {e}")
            return pd.DataFrame()

    # ==================== 资金流向 ====================

    def _get_latest_trade_date(self):
        """获取最近有数据的交易日（从近到远尝试）"""
        try:
            now = datetime.datetime.now()
            start_cal = (now - datetime.timedelta(days=30)).strftime('%Y%m%d')
            end_cal = now.strftime('%Y%m%d')
            trade_cal = self.pro.trade_cal(exchange='SSE', start_date=start_cal, end_date=end_cal)
            trade_dates = trade_cal[trade_cal.is_open == 1]['cal_date'].tolist()
            trade_dates.sort(reverse=True)
            return trade_dates[0] if trade_dates else None
        except Exception:
            return None

    def _get_latest_trade_date_with_data(self, api_func, date_col='trade_date'):
        """获取最近有数据的交易日（逐日回退尝试）"""
        try:
            now = datetime.datetime.now()
            start_cal = (now - datetime.timedelta(days=30)).strftime('%Y%m%d')
            end_cal = now.strftime('%Y%m%d')
            trade_cal = self.pro.trade_cal(exchange='SSE', start_date=start_cal, end_date=end_cal)
            trade_dates = trade_cal[trade_cal.is_open == 1]['cal_date'].tolist()
            trade_dates.sort(reverse=True)
            for td in trade_dates[:5]:
                try:
                    df = api_func(trade_date=td)
                    if df is not None and not df.empty:
                        return td
                except Exception:
                    continue
                time.sleep(0.3)
            return trade_dates[0] if trade_dates else None
        except Exception:
            return None

    def get_individual_fund_flow_rank(self, indicator="今日"):
        """
        个股资金流向排名（兼容东方财富stock_individual_fund_flow_rank输出格式）
        Tushare接口: moneyflow
        注：Tushare免费版 moneyflow 只提供单日数据，3日/5日/10日用相同数据填充。
        """
        if not self.is_available():
            return pd.DataFrame()
        try:
            trade_date = self._get_latest_trade_date_with_data(self.pro.moneyflow)
            if not trade_date:
                return pd.DataFrame()
            df = self.pro.moneyflow(trade_date=trade_date)
            if df is None or df.empty:
                return pd.DataFrame()

            df['symbol'] = df['ts_code'].apply(self._extract_symbol)

            # 获取股票名称
            try:
                stock_basic = self.pro.stock_basic(
                    exchange='', list_status='L',
                    fields='ts_code,name'
                )
                if stock_basic is not None and not stock_basic.empty:
                    df = df.merge(stock_basic[['ts_code', 'name']], on='ts_code', how='left')
                    df['name'] = df['name'].fillna('')
                else:
                    df['name'] = ''
            except Exception:
                df['name'] = ''

            # 获取最新价和涨跌幅
            try:
                daily = self.pro.daily(trade_date=trade_date, fields='ts_code,close,pct_chg')
                if daily is not None and not daily.empty:
                    df = df.merge(daily[['ts_code', 'close', 'pct_chg']], on='ts_code', how='left')
                    df['close'] = pd.to_numeric(df['close'], errors='coerce').fillna(0)
                    df['pct_chg'] = pd.to_numeric(df['pct_chg'], errors='coerce').fillna(0)
                else:
                    df['close'] = 0
                    df['pct_chg'] = 0
            except Exception:
                df['close'] = 0
                df['pct_chg'] = 0

            def _amt(col):
                """安全取金额列（万元）"""
                if col in df.columns:
                    return pd.to_numeric(df[col], errors='coerce').fillna(0)
                return pd.Series([0] * len(df), index=df.index)

            # 计算净占比：主力净流入 / 当日成交额 * 100
            total_amount = _amt('buy_elg_amount') + _amt('sell_elg_amount') + \
                           _amt('buy_lg_amount')  + _amt('sell_lg_amount')  + \
                           _amt('buy_md_amount')  + _amt('sell_md_amount')  + \
                           _amt('buy_sm_amount')  + _amt('sell_sm_amount')
            net_main = _amt('buy_elg_amount') - _amt('sell_elg_amount') + \
                       _amt('buy_lg_amount')  - _amt('sell_lg_amount')
            net_main_pct = (net_main / total_amount.replace(0, float('nan')) * 100).fillna(0).round(2)

            prefix = indicator
            result = pd.DataFrame({
                '代码':                          df['symbol'],
                '名称':                          df['name'],
                '最新价':                        df['close'],
                f'{prefix}涨跌幅':              df['pct_chg'],
                f'{prefix}主力净流入-净额':     net_main,
                f'{prefix}主力净流入-净占比':   net_main_pct,
                f'{prefix}超大单净流入-净额':   _amt('buy_elg_amount') - _amt('sell_elg_amount'),
                f'{prefix}超大单净流入-净占比': 0,
                f'{prefix}大单净流入-净额':     _amt('buy_lg_amount')  - _amt('sell_lg_amount'),
                f'{prefix}大单净流入-净占比':   0,
                f'{prefix}中单净流入-净额':     _amt('buy_md_amount')  - _amt('sell_md_amount'),
                f'{prefix}中单净流入-净占比':   0,
                f'{prefix}小单净流入-净额':     _amt('buy_sm_amount')  - _amt('sell_sm_amount'),
                f'{prefix}小单净流入-净占比':   0,
            })
            return result
        except Exception as e:
            logging.error(f"Tushare获取资金流向排名失败: {e}")
            return pd.DataFrame()

    def get_sector_fund_flow_rank(self, indicator="今日", sector_type="行业资金流"):
        """
        板块资金流向排名（兼容东方财富stock_sector_fund_flow_rank输出格式）
        Tushare接口: moneyflow + stock_basic 聚合
        sector_type: '行业资金流' 按行业分类，'概念资金流' 按概念分类（降级为行业）
        """
        if not self.is_available():
            return pd.DataFrame()
        try:
            trade_date = self._get_latest_trade_date_with_data(self.pro.moneyflow)
            if not trade_date:
                return pd.DataFrame()
            df = self.pro.moneyflow(trade_date=trade_date)
            if df is None or df.empty:
                return pd.DataFrame()

            # 获取行业分类和股票名称
            stock_basic = self.pro.stock_basic(
                exchange='', list_status='L',
                fields='ts_code,name,industry'
            )
            if stock_basic is None or stock_basic.empty:
                return pd.DataFrame()

            merged = df.merge(stock_basic, on='ts_code', how='left')
            merged = merged[merged['industry'].notna() & (merged['industry'] != '')]

            # 使用 _amount 字段（万元）而非 _vol（股数）
            def _amt(col):
                if col in merged.columns:
                    return pd.to_numeric(merged[col], errors='coerce').fillna(0)
                return pd.Series([0.0] * len(merged), index=merged.index)

            merged['_net_main'] = (
                _amt('buy_elg_amount') - _amt('sell_elg_amount') +
                _amt('buy_lg_amount')  - _amt('sell_lg_amount')
            )

            # 按行业聚合
            agg = merged.groupby('industry').agg(
                net_mf_amount   =('net_mf_amount',   'sum'),
                buy_elg_amount  =('buy_elg_amount',  'sum'),
                sell_elg_amount =('sell_elg_amount', 'sum'),
                buy_lg_amount   =('buy_lg_amount',   'sum'),
                sell_lg_amount  =('sell_lg_amount',  'sum'),
                buy_md_amount   =('buy_md_amount',   'sum'),
                sell_md_amount  =('sell_md_amount',  'sum'),
                buy_sm_amount   =('buy_sm_amount',   'sum'),
                sell_sm_amount  =('sell_sm_amount',  'sum'),
            ).reset_index()

            # 每个行业净流入最大股（stock_name）
            idx_max = merged.groupby('industry')['_net_main'].idxmax()
            top_stock = merged.loc[idx_max, ['industry', 'name']].set_index('industry')['name']
            agg['stock_name'] = agg['industry'].map(top_stock).fillna('')

            # 行业平均涨跌幅：需要 daily 数据
            try:
                daily = self.pro.daily(trade_date=trade_date, fields='ts_code,pct_chg')
                if daily is not None and not daily.empty:
                    m2 = merged[['ts_code', 'industry']].merge(daily[['ts_code', 'pct_chg']], on='ts_code', how='left')
                    m2['pct_chg'] = pd.to_numeric(m2['pct_chg'], errors='coerce').fillna(0)
                    avg_chg = m2.groupby('industry')['pct_chg'].mean().reset_index()
                    avg_chg.columns = ['industry', 'avg_pct_chg']
                    agg = agg.merge(avg_chg, on='industry', how='left')
                    agg['avg_pct_chg'] = agg['avg_pct_chg'].fillna(0).round(2)
                else:
                    agg['avg_pct_chg'] = 0
            except Exception:
                agg['avg_pct_chg'] = 0

            prefix = indicator
            result = pd.DataFrame({
                '名称':                          agg['industry'],
                f'{prefix}涨跌幅':              agg['avg_pct_chg'],
                f'{prefix}主力净流入-净额':     pd.to_numeric(agg['net_mf_amount'], errors='coerce').fillna(0),
                f'{prefix}主力净流入-净占比':   0,
                f'{prefix}超大单净流入-净额':   pd.to_numeric(agg['buy_elg_amount'] - agg['sell_elg_amount'], errors='coerce').fillna(0),
                f'{prefix}超大单净流入-净占比': 0,
                f'{prefix}大单净流入-净额':     pd.to_numeric(agg['buy_lg_amount']  - agg['sell_lg_amount'],  errors='coerce').fillna(0),
                f'{prefix}大单净流入-净占比':   0,
                f'{prefix}中单净流入-净额':     pd.to_numeric(agg['buy_md_amount']  - agg['sell_md_amount'],  errors='coerce').fillna(0),
                f'{prefix}中单净流入-净占比':   0,
                f'{prefix}小单净流入-净额':     pd.to_numeric(agg['buy_sm_amount']  - agg['sell_sm_amount'],  errors='coerce').fillna(0),
                f'{prefix}小单净流入-净占比':   0,
                f'{prefix}主力净流入最大股':    agg['stock_name'],
            })
            return result
        except Exception as e:
            logging.error(f"Tushare获取板块资金流向失败: {e}")
            return pd.DataFrame()

    # ==================== 大宗交易 ====================

    def get_block_trade(self, start_date, end_date):
        """
        大宗交易每日明细（兼容东方财富stock_dzjy_mrmx输出格式）
        Tushare接口: block_trade
        """
        if not self.is_available():
            return pd.DataFrame()
        try:
            start_dt = start_date.replace('-', '')
            end_dt = end_date.replace('-', '')
            df = self.pro.block_trade(start_date=start_dt, end_date=end_dt)
            if df is None or df.empty:
                return pd.DataFrame()

            df['symbol'] = df['ts_code'].apply(self._extract_symbol)

            def _safe_col(col, default=0):
                return pd.to_numeric(df[col], errors='coerce').fillna(default) if col in df.columns else default

            result = pd.DataFrame({
                '序号': range(1, len(df) + 1),
                '交易日期': pd.to_datetime(df['trade_date']).dt.date,
                '证券代码': df['symbol'],
                '证券简称': '',
                '涨跌幅': 0,
                '收盘价': 0,
                '成交价': _safe_col('price'),
                '折溢率': 0,
                '成交量': _safe_col('vol'),
                '成交额': _safe_col('amount'),
                '成交额/流通市值': 0,
                '买方营业部': df['buyer'] if 'buyer' in df.columns else '',
                '卖方营业部': df['seller'] if 'seller' in df.columns else '',
            })
            return result
        except Exception as e:
            logging.error(f"Tushare获取大宗交易失败: {e}")
            return pd.DataFrame()

    def get_block_trade_daily_stat(self, start_date, end_date):
        """
        大宗交易每日统计（兼容东方财富stock_dzjy_mrtj输出格式）
        Tushare接口: block_trade 聚合
        """
        if not self.is_available():
            return pd.DataFrame()
        try:
            start_dt = start_date.replace('-', '')
            end_dt = end_date.replace('-', '')
            df = self.pro.block_trade(start_date=start_dt, end_date=end_dt)
            if df is None or df.empty:
                return pd.DataFrame()

            df['symbol'] = df['ts_code'].apply(self._extract_symbol)
            grouped = df.groupby(['trade_date', 'ts_code']).agg({
                'price': 'mean',
                'vol': 'sum',
                'amount': 'sum',
            }).reset_index()
            grouped['deal_num'] = df.groupby(['trade_date', 'ts_code']).size().values

            result = pd.DataFrame({
                '序号': range(1, len(grouped) + 1),
                '交易日期': pd.to_datetime(grouped['trade_date']).dt.date,
                '证券代码': grouped['ts_code'].apply(self._extract_symbol),
                '证券简称': '',
                '收盘价': 0,
                '涨跌幅': 0,
                '成交价': pd.to_numeric(grouped['price'], errors='coerce'),
                '折溢率': 0,
                '成交笔数': grouped['deal_num'],
                '成交总量': pd.to_numeric(grouped['vol'], errors='coerce'),
                '成交总额': pd.to_numeric(grouped['amount'], errors='coerce'),
                '成交总额/流通市值': 0,
            })
            return result
        except Exception as e:
            logging.error(f"Tushare获取大宗交易统计失败: {e}")
            return pd.DataFrame()

    # ==================== 分红送配 ====================

    def get_dividend(self, report_date=None, ts_code=None):
        """
        分红送配（兼容东方财富stock_fhps_em输出格式）
        Tushare接口: dividend
        """
        if not self.is_available():
            return pd.DataFrame()
        try:
            kwargs = {}
            if ts_code:
                kwargs['ts_code'] = ts_code
            if report_date:
                # 将报告期格式转为Tushare格式
                rp = report_date.replace('-', '')
                kwargs['end_date'] = rp

            df = self.pro.dividend(**kwargs)
            if df is None or df.empty:
                return pd.DataFrame()

            df['symbol'] = df['ts_code'].apply(self._extract_symbol)

            # 安全获取列并转为数值
            def _safe_num(col_name, default=0):
                if col_name in df.columns:
                    return pd.to_numeric(df[col_name], errors='coerce').fillna(default)
                return default

            result = pd.DataFrame({
                '代码': df['symbol'],
                '名称': df.get('name', '') if 'name' in df.columns else '',
                '送转股份-送转总比例': _safe_num('stk_div') + _safe_num('stk_boom'),
                '送转股份-送转比例': _safe_num('stk_div'),
                '送转股份-转股比例': _safe_num('stk_boom'),
                '现金分红-现金分红比例': _safe_num('cash_div'),
                '现金分红-股息率': 0,
                '每股收益': 0,
                '每股净资产': 0,
                '每股公积金': 0,
                '每股未分配利润': 0,
                '净利润同比增长': 0,
                '总股本': 0,
                '预案公告日': pd.to_datetime(df['ann_date'], errors='coerce').dt.date if 'ann_date' in df.columns else None,
                '股权登记日': pd.to_datetime(df['record_date'], errors='coerce').dt.date if 'record_date' in df.columns else None,
                '除权除息日': pd.to_datetime(df['ex_date'], errors='coerce').dt.date if 'ex_date' in df.columns else None,
                '方案进度': df['div_proc'] if 'div_proc' in df.columns else '',
                '最新公告日期': pd.to_datetime(df['ann_date'], errors='coerce').dt.date if 'ann_date' in df.columns else None,
            })
            return result
        except Exception as e:
            logging.error(f"Tushare获取分红数据失败: {e}")
            return pd.DataFrame()

    # ==================== ETF行情 ====================

    def get_etf_spot(self):
        """
        ETF实时行情（兼容东方财富fund_etf_spot_em输出格式）
        Tushare接口: fund_basic + daily
        """
        if not self.is_available():
            return pd.DataFrame()
        try:
            # 获取ETF列表
            fund_list = self.pro.fund_basic(market='E', fields='ts_code,symbol,name,list_date')
            if fund_list is None or fund_list.empty:
                return pd.DataFrame()

            # 获取最新日线数据
            trade_date = datetime.datetime.now().strftime('%Y%m%d')
            daily = self.pro.fund_daily(trade_date=trade_date)
            if daily is None or daily.empty:
                # 尝试上一个交易日
                yesterday = (datetime.datetime.now() - datetime.timedelta(days=1)).strftime('%Y%m%d')
                daily = self.pro.fund_daily(trade_date=yesterday)

            if daily is None or daily.empty:
                return pd.DataFrame()

            # 合并
            merged = fund_list.merge(daily, on='ts_code', how='inner')
            merged['symbol'] = merged['ts_code'].apply(self._extract_symbol)

            result = pd.DataFrame({
                '代码': merged['symbol'],
                '名称': merged['name'],
                '最新价': pd.to_numeric(merged.get('close', 0), errors='coerce'),
                '涨跌幅': pd.to_numeric(merged.get('pct_chg', 0), errors='coerce'),
                '涨跌额': pd.to_numeric(merged.get('change', 0), errors='coerce'),
                '成交量': pd.to_numeric(merged.get('vol', 0), errors='coerce'),
                '成交额': pd.to_numeric(merged.get('amount', 0), errors='coerce'),
                '开盘价': pd.to_numeric(merged.get('open', 0), errors='coerce'),
                '最高价': pd.to_numeric(merged.get('high', 0), errors='coerce'),
                '最低价': pd.to_numeric(merged.get('low', 0), errors='coerce'),
                '昨收': pd.to_numeric(merged.get('pre_close', 0), errors='coerce'),
                '换手率': 0,
                '流通市值': 0,
                '总市值': 0,
            })
            return result
        except Exception as e:
            logging.error(f"Tushare获取ETF行情失败: {e}")
            return pd.DataFrame()

    def get_etf_hist(self, symbol, period="daily", start_date="19700101", end_date="20500101", adjust="qfq"):
        """
        ETF历史行情（兼容东方财富fund_etf_hist_em输出格式）
        Tushare接口: fund_daily
        """
        if not self.is_available():
            return pd.DataFrame()
        try:
            ts_code = self._to_ts_code(symbol)
            # ETF在Tushare中代码格式可能不同，尝试直接用fund_daily
            df = self.pro.fund_daily(ts_code=ts_code, start_date=start_date.replace('-', ''),
                                     end_date=end_date.replace('-', ''))
            if df is None or df.empty:
                # 尝试ETF代码格式
                if symbol.startswith(('51', '52', '56', '58', '15', '16', '18')):
                    ts_code = f"{symbol}.SH" if symbol.startswith(('51', '52', '56', '58')) else f"{symbol}.SZ"
                    df = self.pro.fund_daily(ts_code=ts_code, start_date=start_date.replace('-', ''),
                                             end_date=end_date.replace('-', ''))
            if df is None or df.empty:
                return pd.DataFrame()

            result = pd.DataFrame({
                '日期': pd.to_datetime(df['trade_date']).dt.strftime('%Y-%m-%d'),
                '开盘': pd.to_numeric(df['open'], errors='coerce'),
                '收盘': pd.to_numeric(df['close'], errors='coerce'),
                '最高': pd.to_numeric(df['high'], errors='coerce'),
                '最低': pd.to_numeric(df['low'], errors='coerce'),
                '成交量': pd.to_numeric(df['vol'], errors='coerce'),
                '成交额': pd.to_numeric(df['amount'], errors='coerce'),
                '振幅': 0,
                '涨跌幅': pd.to_numeric(df['pct_chg'], errors='coerce'),
                '涨跌额': pd.to_numeric(df['change'], errors='coerce'),
                '换手率': 0,
            })
            return result
        except Exception as e:
            logging.error(f"Tushare获取ETF历史行情失败: {e}")
            return pd.DataFrame()

    # ==================== 选股器 ====================

    def get_stock_selection(self):
        """
        综合选股（兼容东方财富stock_selection输出格式）
        Tushare接口: stock_basic + daily + daily_basic + fina_indicator
        返回与TABLE_CN_STOCK_SELECTION列数一致的DataFrame
        """
        if not self.is_available():
            return pd.DataFrame()
        try:
            # 获取股票列表
            stock_list = self.pro.stock_basic(exchange='', list_status='L',
                                              fields='ts_code,symbol,name,industry,list_date,area')
            if stock_list is None or stock_list.empty:
                return pd.DataFrame()

            # 获取最新交易日
            trade_date = self._get_latest_trade_date_with_data()
            if not trade_date:
                return pd.DataFrame()

            # 获取日线行情
            daily = self.pro.daily(trade_date=trade_date,
                                   fields='ts_code,open,high,low,close,pre_close,change,pct_chg,vol,amount')
            if daily is None or daily.empty:
                return pd.DataFrame()

            # 获取日线指标
            daily_basic = self.pro.daily_basic(trade_date=trade_date,
                                               fields='ts_code,turnover_rate,volume_ratio,pe,pb,dv_ratio,total_mv,circ_mv')
            if daily_basic is None or daily_basic.empty:
                daily_basic = pd.DataFrame(columns=['ts_code'])

            # 获取财务指标（最近一期）
            fina = self.pro.fina_indicator(
                fields='ts_code,eps,bps,roe,debt_to_assets,grossprofit_margin,netprofit_yoy,revenue_yoy',
                start_date=trade_date[:4] + '0101'
            )
            fina_latest = None
            if fina is not None and not fina.empty:
                fina_latest = fina.drop_duplicates(subset='ts_code', keep='first')

            # 合并数据
            merged = stock_list.merge(daily, on='ts_code', how='inner')
            merged = merged.merge(daily_basic, on='ts_code', how='left')
            if fina_latest is not None:
                merged = merged.merge(fina_latest, on='ts_code', how='left')

            # 构建与 TABLE_CN_STOCK_SELECTION 对齐的 DataFrame
            today = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}"
            rows = []
            for _, row in merged.iterrows():
                list_date_val = row.get('list_date', '')
                if list_date_val and str(list_date_val) != 'nan':
                    ld = str(int(list_date_val)) if isinstance(list_date_val, float) else str(list_date_val)
                    list_date_fmt = f"{ld[:4]}-{ld[4:6]}-{ld[6:8]}" if len(ld) >= 8 else None
                else:
                    list_date_fmt = None

                rows.append({
                    'date': today,
                    'code': self._extract_symbol(row['ts_code']),
                    'name': row.get('name', ''),
                    'new_price': float(row.get('close', 0) or 0),
                    'change_rate': float(row.get('pct_chg', 0) or 0),
                    'volume_ratio': float(row.get('volume_ratio', 0) or 0),
                    'high_price': float(row.get('high', 0) or 0),
                    'low_price': float(row.get('low', 0) or 0),
                    'pre_close_price': float(row.get('pre_close', 0) or 0),
                    'volume': int(float(row.get('vol', 0) or 0) * 100),  # Tushare单位:手→股
                    'deal_amount': int(float(row.get('amount', 0) or 0) * 1000),  # Tushare单位:千元→元
                    'turnoverrate': float(row.get('turnover_rate', 0) or 0),
                    'listing_date': list_date_fmt,
                    'industry': row.get('industry', ''),
                    'area': row.get('area', ''),
                    'pe9': float(row.get('pe', 0) or 0),
                    'pbnewmrq': float(row.get('pb', 0) or 0),
                    'total_market_cap': float(row.get('total_mv', 0) or 0),
                    'free_cap': float(row.get('circ_mv', 0) or 0),
                    'zxgxl': float(row.get('dv_ratio', 0) or 0),
                    'basic_eps': float(row.get('eps', 0) or 0),
                    'bvps': float(row.get('bps', 0) or 0),
                    'roe_weight': float(row.get('roe', 0) or 0),
                    'debt_asset_ratio': float(row.get('debt_to_assets', 0) or 0),
                    'sale_gpr': float(row.get('grossprofit_margin', 0) or 0),
                    'netprofit_yoy_ratio': float(row.get('netprofit_yoy', 0) or 0),
                    'toi_yoy_ratio': float(row.get('revenue_yoy', 0) or 0),
                })

            if not rows:
                return pd.DataFrame()

            result = pd.DataFrame(rows)
            from instock.core.tablestructure import TABLE_CN_STOCK_SELECTION
            expected_cols = list(TABLE_CN_STOCK_SELECTION['columns'])
            for col in expected_cols:
                if col not in result.columns:
                    result[col] = None
            return result[expected_cols]
        except Exception as e:
            logging.error(f"Tushare获取综合选股失败: {e}")
            return None

    # ==================== 通用方法 ====================

    def get_stock_moneyflow(self, symbol):
        """
        单只股票资金流向（兼容东方财富stock_zjlx_em输出格式）
        Tushare接口: moneyflow
        :param symbol: 6位股票代码
        :return: 个股资金流向DataFrame
        """
        if not self.is_available():
            return pd.DataFrame()
        try:
            ts_code = self._to_ts_code(symbol)
            # 获取最近30天的资金流向数据
            import datetime
            now = datetime.datetime.now()
            start_date = (now - datetime.timedelta(days=30)).strftime('%Y%m%d')
            end_date = now.strftime('%Y%m%d')
            df = self.pro.moneyflow(ts_code=ts_code, start_date=start_date, end_date=end_date)
            if df is None or df.empty:
                return pd.DataFrame()

            df = df.sort_values('trade_date', ascending=False)

            result = pd.DataFrame({
                '日期': df['trade_date'],
                '主力净流入额': pd.to_numeric(df.get('net_mf_vol', 0), errors='coerce').fillna(0),
                '小单净流入额': pd.to_numeric(df.get('buy_sm_vol', 0), errors='coerce').fillna(0) - pd.to_numeric(df.get('sell_sm_vol', 0), errors='coerce').fillna(0),
                '中单净流入额': pd.to_numeric(df.get('buy_md_vol', 0), errors='coerce').fillna(0) - pd.to_numeric(df.get('sell_md_vol', 0), errors='coerce').fillna(0),
                '大单净流入额': pd.to_numeric(df.get('buy_lg_vol', 0), errors='coerce').fillna(0) - pd.to_numeric(df.get('sell_lg_vol', 0), errors='coerce').fillna(0),
                '超大单净流入额': pd.to_numeric(df.get('buy_elg_vol', 0), errors='coerce').fillna(0) - pd.to_numeric(df.get('sell_elg_vol', 0), errors='coerce').fillna(0),
                '主力净流入占比': pd.to_numeric(df.get('net_mf_amount', 0), errors='coerce').fillna(0),
                '小单净流入占比': 0,
                '中单净流入占比': 0,
                '大单净流入占比': 0,
                '超大单净流入占比': 0,
                '收盘价': 0,
                '涨跌幅': 0,
            })
            return result
        except Exception as e:
            logging.error(f"Tushare获取个股资金流向失败: {e}")
            return pd.DataFrame()

    def get_stock_list(self):
        """获取股票列表"""
        if not self.is_available():
            return pd.DataFrame()
        try:
            df = self.pro.stock_basic(exchange='', list_status='L',
                                      fields='ts_code,symbol,name,industry,list_date,market,area')
            df = df.rename(columns={
                'ts_code': '代码',
                'symbol': '交易代码',
                'name': '名称',
                'industry': '行业',
                'list_date': '上市日期',
                'market': '市场',
                'area': '地区'
            })
            return df
        except Exception as e:
            print(f"Tushare获取股票列表失败: {e}")
            return pd.DataFrame()

    def get_daily(self, ts_code, start_date=None, end_date=None):
        """获取日线数据"""
        if not self.is_available():
            return pd.DataFrame()
        try:
            return self.pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
        except Exception as e:
            print(f"Tushare获取日线数据失败 {ts_code}: {e}")
            return pd.DataFrame()

    def get_fina_indicator(self, ts_code=None, start_date=None, end_date=None):
        """获取财务指标"""
        if not self.is_available():
            return pd.DataFrame()
        try:
            df = self.pro.fina_indicator(ts_code=ts_code, start_date=start_date, end_date=end_date)
            if df is None or df.empty:
                return pd.DataFrame()
            return df
        except Exception as e:
            print(f"Tushare获取财务指标失败: {e}")
            return pd.DataFrame()

    # ==================== 交易日历 ====================

    def get_trade_cal(self, start_date=None, end_date=None):
        """
        交易日历（替代新浪tool_trade_date_hist_sina）
        Tushare接口: trade_cal
        返回: DataFrame，含 trade_date 列（datetime.date），仅交易日(is_open=1)
        """
        if not self.is_available():
            return pd.DataFrame()
        try:
            if start_date is None:
                start_date = "19901219"
            if end_date is None:
                end_date = (datetime.datetime.now() + datetime.timedelta(days=365)).strftime('%Y%m%d')
            df = self.pro.trade_cal(exchange='SSE', start_date=start_date,
                                    end_date=end_date, is_open='1')
            if df is None or df.empty:
                return pd.DataFrame()
            result = pd.DataFrame({
                'trade_date': pd.to_datetime(df['cal_date']).dt.date
            })
            return result
        except Exception as e:
            logging.error(f"Tushare获取交易日历失败: {e}")
            return pd.DataFrame()

    # ==================== 龙虎榜个股上榜统计 ====================

    def get_lhb_ggtj(self, start_date, end_date):
        """
        龙虎榜个股上榜统计（替代新浪stock_lhb_ggtj_sina）
        按代码聚合区间内 top_list 数据。
        列: 股票代码, 股票名称, 上榜次数, 累积购买额, 累积卖出额, 净额, 买入席位数, 卖出席位数
        注: Tushare无席位数据，买入/卖出席位数填0。
        """
        if not self.is_available():
            return pd.DataFrame()
        try:
            start_dt = start_date.replace('-', '')
            end_dt = end_date.replace('-', '')
            all_dfs = []
            current = datetime.datetime.strptime(start_dt, '%Y%m%d')
            end = datetime.datetime.strptime(end_dt, '%Y%m%d')
            while current <= end:
                trade_date = current.strftime('%Y%m%d')
                try:
                    df = self.pro.top_list(trade_date=trade_date)
                    if df is not None and not df.empty:
                        all_dfs.append(df)
                except Exception:
                    pass
                current += datetime.timedelta(days=1)
                time.sleep(0.3)

            if not all_dfs:
                return pd.DataFrame()

            big_df = pd.concat(all_dfs, ignore_index=True)
            big_df['symbol'] = big_df['ts_code'].apply(self._extract_symbol)
            big_df['l_buy'] = pd.to_numeric(big_df.get('l_buy'), errors='coerce').fillna(0)
            big_df['l_sell'] = pd.to_numeric(big_df.get('l_sell'), errors='coerce').fillna(0)

            grouped = big_df.groupby('symbol')
            result = pd.DataFrame({
                '股票代码': [k for k, _ in grouped],
                '股票名称': grouped['name'].first().values,
                '上榜次数': grouped.size().values,
                '累积购买额': grouped['l_buy'].sum().values,
                '累积卖出额': grouped['l_sell'].sum().values,
                '净额': (grouped['l_buy'].sum() - grouped['l_sell'].sum()).values,
                '买入席位数': 0,
                '卖出席位数': 0,
            })
            return result
        except Exception as e:
            logging.error(f"Tushare获取个股上榜统计失败: {e}")
            return pd.DataFrame()

    # ==================== 涨停原因 ====================

    def get_limitup_reason(self, trade_date):
        """
        涨停原因（Tushare kpl_list 接口，已移除同花顺爬虫依赖）
        列: 日期, 代码, 名称, 原因, 详因, 最新价, 涨跌幅, 涨跌额, 换手率, 成交量, 成交额, DDE
        """
        # Tushare kpl_list（需较高积分）
        if not self.is_available():
            return pd.DataFrame()
        try:
            trade_dt = trade_date.replace('-', '')
            df = self.pro.kpl_list(trade_date=trade_dt, tag='涨停')
            if df is None or df.empty:
                return pd.DataFrame()
            df['symbol'] = df['ts_code'].apply(self._extract_symbol)

            def _safe_col(col, default=0):
                return pd.to_numeric(df[col], errors='coerce').fillna(default) if col in df.columns else default

            reason = df['theme'] if 'theme' in df.columns else (df['lu_desc'] if 'lu_desc' in df.columns else '')
            result = pd.DataFrame({
                '日期': pd.to_datetime(df['trade_date']).dt.date,
                '代码': df['symbol'],
                '名称': df['name'],
                '原因': reason,
                '详因': df['lu_desc'] if 'lu_desc' in df.columns else '',
                '最新价': 0,
                '涨跌幅': _safe_col('pct_chg'),
                '涨跌额': 0,
                '换手率': _safe_col('turnover_rate'),
                '成交量': 0,
                '成交额': _safe_col('amount'),
                'DDE': 0,
            })
            return result
        except Exception as e:
            logging.error(f"Tushare获取涨停原因失败: {e}")
            return pd.DataFrame()


# 创建全局实例
tushare_data = TushareData()
