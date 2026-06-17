#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import pandas as pd
import numpy as np

# 在项目运行时，临时将项目路径添加到环境变量
cpath = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
cpath_current = os.path.dirname(__file__)
if not cpath_current:
    cpath_current = '/Users/x6-mac/WWW/stock/instock/core/crawling'
sys.path.append(cpath)

__author__ = 'myh '
__date__ = '2025/12/31 '

try:
    import tushare as ts
    TUSHARE_AVAILABLE = True
except ImportError:
    TUSHARE_AVAILABLE = False

class TushareFetcher:
    """
    Tushare数据获取器
    """

    def __init__(self):
        self.token = self._get_token()
        self.pro = None
        if TUSHARE_AVAILABLE and self.token:
            self._init_pro()

    def _get_token(self):
        """
        获取Tushare token
        优先级：环境变量 > 文件 > None
        """
        # 1. 从环境变量获取
        token = os.environ.get('TUSHARE_TOKEN')
        if token:
            return token

        # 2. 从配置文件获取
        token_file = os.path.join(cpath, 'config', 'tushare_token.txt')
        if os.path.exists(token_file):
            with open(token_file, 'r') as f:
                token = f.read().strip()
                if token:
                    return token

        return None

    def _init_pro(self):
        """初始化Tushare pro接口"""
        try:
            self.pro = ts.pro_api(self.token)
            return True
        except Exception as e:
            print(f"Tushare初始化失败: {e}")
            return False

    def is_available(self):
        """检查Tushare是否可用"""
        return TUSHARE_AVAILABLE and self.token is not None and self.pro is not None


# 创建全局实例
fetcher = TushareFetcher()


def stock_zh_a_spot_em() -> pd.DataFrame:
    """
    获取A股实时行情数据，列名对齐 TABLE_CN_STOCK_SPOT
    :return: 实时行情DataFrame
    """
    if not fetcher.is_available():
        return pd.DataFrame()

    try:
        import datetime
        import numpy as np
        today = datetime.datetime.now()
        start_of_year = f"{today.year}0101"
        end_of_year = f"{today.year + 1}1231"

        # 获取最近交易日（不超过今天）
        trade_cal = fetcher.pro.trade_cal(exchange='SSE', start_date=start_of_year, end_date=end_of_year)
        trade_dates = trade_cal[trade_cal.is_open == 1]['cal_date'].tolist()
        trade_dates.sort(reverse=True)
        today_str = today.strftime('%Y%m%d')
        past_dates = [d for d in trade_dates if d <= today_str]
        latest_date = past_dates[0] if past_dates else None
        if not latest_date:
            return pd.DataFrame()

        # 获取A股基本信息
        stock_basic = fetcher.pro.stock_basic(
            exchange='', list_status='L',
            fields='ts_code,symbol,name,industry,list_date,market'
        )

        # 获取当日行情 daily列: open,high,low,close,pre_close,change,pct_chg,vol,amount
        daily = fetcher.pro.daily(trade_date=latest_date)

        # 获取当日基础指标 daily_basic列: close(重复),turnover_rate,volume_ratio,pe,pe_ttm,pb,
        #   total_share,float_share,free_share,total_mv,circ_mv
        daily_basic = fetcher.pro.daily_basic(trade_date=latest_date)
        # 去掉 daily_basic 中与 daily 重复的 close 列，避免 close_x/close_y
        daily_basic = daily_basic.drop(columns=['close'], errors='ignore')

        # 合并
        result = pd.merge(stock_basic, daily, on='ts_code', how='inner')
        result = pd.merge(result, daily_basic, on=['ts_code', 'trade_date'], how='left')

        # 对齐 TABLE_CN_STOCK_SPOT 的 41 列
        out = pd.DataFrame()
        out['code']              = result['symbol']
        out['name']              = result['name']
        out['new_price']         = result['close']
        out['change_rate']       = result['pct_chg']
        out['ups_downs']         = result['change']
        out['volume']            = result['vol']
        out['deal_amount']       = result['amount']
        out['amplitude']         = ((result['high'] - result['low']) / result['pre_close'] * 100).round(2)
        out['turnoverrate']      = result.get('turnover_rate', np.nan)
        out['volume_ratio']      = result.get('volume_ratio', np.nan)
        out['open_price']        = result['open']
        out['high_price']        = result['high']
        out['low_price']         = result['low']
        out['pre_close_price']   = result['pre_close']
        out['speed_increase']    = np.nan
        out['speed_increase_5']  = np.nan
        out['speed_increase_60'] = np.nan
        out['speed_increase_all']= np.nan
        out['dtsyl']             = result.get('pe', np.nan)
        out['pe9']               = result.get('pe', np.nan)
        out['pe']                = result.get('pe_ttm', np.nan)
        out['pbnewmrq']          = result.get('pb', np.nan)
        out['basic_eps']         = np.nan
        out['bvps']              = np.nan
        out['per_capital_reserve']   = np.nan
        out['per_unassign_profit']   = np.nan
        out['roe_weight']            = np.nan
        out['sale_gpr']              = np.nan
        out['debt_asset_ratio']      = np.nan
        out['total_operate_income']  = np.nan
        out['toi_yoy_ratio']         = np.nan
        out['parent_netprofit']      = np.nan
        out['netprofit_yoy_ratio']   = np.nan
        out['report_date']           = np.nan
        out['total_shares']      = result.get('total_share', np.nan)
        out['free_shares']       = result.get('free_share', np.nan)
        out['total_market_cap']  = result.get('total_mv', np.nan)
        out['free_cap']          = result.get('circ_mv', np.nan)
        out['industry']          = result['industry']
        out['listing_date']      = result['list_date']

        # 过滤无价格数据
        out = out[out['new_price'].notna() & (out['new_price'] > 0)].copy()
        return out

    except Exception as e:
        print(f"Tushare获取股票行情失败: {e}")
        return pd.DataFrame()


def stock_zh_a_hist(
    symbol: str = "000001",
    period: str = "daily",
    start_date: str = "19700101",
    end_date: str = "20500101",
    adjust: str = "",
) -> pd.DataFrame:
    """
    获取A股历史K线数据，列名与 CN_STOCK_HIST_DATA['columns'] 一致（英文）。
    columns: date, open, close, high, low, volume, amount, amplitude, quote_change, ups_downs, turnover
    """
    if not fetcher.is_available():
        return pd.DataFrame()

    try:
        # 构建完整代码
        ts_code = f"{symbol}.SH" if symbol.startswith('6') else f"{symbol}.SZ"

        # 获取K线数据
        df = fetcher.pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
        if df is None or df.empty:
            return pd.DataFrame()

        # 前复权 / 后复权：用 adj_factor 相对比值调整
        # 正确算法：前复权 = 原始价 * (当日adj_factor / 最新adj_factor)
        # 这样最新一天的价格不变，历史价格按比例缩放
        if adjust in ('qfq', 'hfq'):
            adj_df = fetcher.pro.adj_factor(ts_code=ts_code, start_date=start_date, end_date=end_date)
            if adj_df is not None and not adj_df.empty:
                df = pd.merge(df, adj_df[['trade_date', 'adj_factor']], on='trade_date', how='left')
                df['adj_factor'] = df['adj_factor'].ffill().fillna(1)
                latest_adj = df['adj_factor'].iloc[0]   # Tushare 返回倒序，[0]为最新
                if adjust == 'qfq':
                    ratio = df['adj_factor'] / latest_adj
                else:  # hfq
                    ratio = df['adj_factor'] / df['adj_factor'].iloc[-1]
                for col in ('open', 'high', 'low', 'close', 'pre_close'):
                    if col in df.columns:
                        df[col] = (df[col] * ratio).round(3)

        # 按日期升序排列
        df = df.sort_values('trade_date').reset_index(drop=True)

        # 计算振幅、涨跌额，换手率从 daily_basic 获取（可选，这里先置0）
        df['amplitude'] = ((df['high'] - df['low']) / df['pre_close'] * 100).round(2)
        df['ups_downs'] = (df['close'] - df['pre_close']).round(3)
        df['turnover'] = 0

        # 重命名为英文列名，与 CN_STOCK_HIST_DATA['columns'] 一致
        df = df.rename(columns={
            'trade_date': 'date',
            'open': 'open',
            'close': 'close',
            'high': 'high',
            'low': 'low',
            'vol': 'volume',
            'amount': 'amount',
            'pct_chg': 'quote_change',
        })

        return df[['date', 'open', 'close', 'high', 'low', 'volume', 'amount',
                   'amplitude', 'quote_change', 'ups_downs', 'turnover']]

    except Exception as e:
        print(f"Tushare获取K线数据失败 {symbol}: {e}")
        return pd.DataFrame()


def fetch_etf_list() -> pd.DataFrame:
    """
    获取ETF列表
    """
    if not fetcher.is_available():
        return pd.DataFrame()

    try:
        etf_list = fetcher.pro.fund_basic(market='E', fields='ts_code,symbol,name,fund_type,list_date')
        etf_list = etf_list.rename(columns={
            'ts_code': '代码',
            'name': '名称',
            'symbol': '交易代码',
            'fund_type': '类型',
            'list_date': '上市日期'
        })
        return etf_list
    except Exception as e:
        print(f"Tushare获取ETF列表失败: {e}")
        return pd.DataFrame()


def fetch_stock_list() -> pd.DataFrame:
    """
    获取股票列表
    """
    if not fetcher.is_available():
        return pd.DataFrame()

    try:
        stock_list = fetcher.pro.stock_basic(exchange='', list_status='L',
                                             fields='ts_code,symbol,name,industry,list_date,market,area')
        stock_list = stock_list.rename(columns={
            'ts_code': '代码',
            'symbol': '交易代码',
            'name': '名称',
            'industry': '行业',
            'list_date': '上市日期',
            'market': '市场',
            'area': '地区'
        })
        return stock_list
    except Exception as e:
        print(f"Tushare获取股票列表失败: {e}")
        return pd.DataFrame()
