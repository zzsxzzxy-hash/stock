#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""
A股行情数据（已重构为Tushare API，彻底移除东方财富爬虫）
"""

import pandas as pd
from functools import lru_cache

__author__ = 'myh '
__date__ = '2025/12/31 '


def stock_zh_a_spot_em() -> pd.DataFrame:
    """
    A股实时行情（Tushare API）
    :return: 实时行情
    :rtype: pandas.DataFrame
    """
    try:
        from instock.core.crawling.stock_hist_tushare import stock_zh_a_spot_em as tushare_spot
        return tushare_spot()
    except ImportError:
        print("Tushare模块未安装")
        return pd.DataFrame()
    except Exception as e:
        print(f"Tushare获取实时行情失败: {e}")
        return pd.DataFrame()


@lru_cache()
def code_id_map_em() -> dict:
    """
    股票代码到市场ID的映射（Tushare API，替代东方财富爬虫）
    返回: {symbol: market_id}，上交所=1，深交所=0
    """
    try:
        from instock.core.crawling.tushare_data import tushare_data
        if not tushare_data.is_available():
            return dict()
        stock_basic = tushare_data.pro.stock_basic(
            exchange='', list_status='L',
            fields='ts_code,symbol,exchange'
        )
        if stock_basic is None or stock_basic.empty:
            return dict()
        # exchange: SSE=上交所(1), SZSE=深交所(0), BSE=北交所(0)
        def _market_id(exchange):
            return 1 if exchange == 'SSE' else 0
        stock_basic['market_id'] = stock_basic['exchange'].apply(_market_id)
        return dict(zip(stock_basic['symbol'], stock_basic['market_id']))
    except Exception as e:
        print(f"code_id_map_em获取失败: {e}")
        return dict()


def stock_zh_a_hist(
    symbol: str = "000001",
    period: str = "daily",
    start_date: str = "20200101",
    end_date: str = "20500101",
    adjust: str = "",
) -> pd.DataFrame:
    """
    A股历史K线（Tushare API）
    :param symbol: 股票代码
    :param period: choice of {'daily', 'weekly', 'monthly'}
    :param start_date: 开始日期 YYYYMMDD
    :param end_date: 结束日期 YYYYMMDD
    :param adjust: choice of {'qfq', 'hfq', ''}
    :return: 历史K线
    """
    try:
        from instock.core.crawling.stock_hist_tushare import stock_zh_a_hist as tushare_hist
        return tushare_hist(symbol=symbol, period=period, start_date=start_date,
                            end_date=end_date, adjust=adjust)
    except ImportError:
        print("Tushare模块未安装")
        return pd.DataFrame()
    except Exception as e:
        print(f"Tushare获取数据失败: {e}")
        return pd.DataFrame()


def stock_zh_a_hist_min_em(
    symbol: str = "000001",
    start_date: str = "1979-09-01 09:32:00",
    end_date: str = "2222-01-01 09:32:00",
    period: str = "5",
    adjust: str = "",
) -> pd.DataFrame:
    """
    A股分钟K线（Tushare无免费接口，返回空DataFrame）
    """
    return pd.DataFrame()


def stock_zh_a_hist_pre_min_em(
    symbol: str = "000001",
    start_time: str = "09:00:00",
    end_time: str = "15:50:00",
) -> pd.DataFrame:
    """
    A股盘前/盘中分钟行情（Tushare无免费接口，返回空DataFrame）
    """
    return pd.DataFrame()


if __name__ == "__main__":
    df = stock_zh_a_spot_em()
    print(df)
