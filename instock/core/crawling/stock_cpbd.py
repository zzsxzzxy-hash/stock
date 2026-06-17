# -*- coding:utf-8 -*-
# !/usr/bin/env python
"""
操盘必读数据（已重构为Tushare API，彻底移除东方财富爬虫）
Tushare接口: fina_indicator, daily_basic, top_list, block_trade, margin_detail
"""
import logging
import pandas as pd
from instock.core.crawling.tushare_data import tushare_data

__author__ = 'myh '
__date__ = '2025/12/31 '


def stock_cpbd_em(symbol: str = "000001") -> dict:
    """
    个股操盘必读（Tushare API）
    :param symbol: 6位股票代码
    :return: 操盘必读数据字典
    """
    if not tushare_data.is_available():
        return {}

    ts_code = f"{symbol}.SH" if symbol.startswith(('6', '9')) else f"{symbol}.SZ"
    result = {}

    try:
        # 基本面指标（最新一期财报）
        fina = tushare_data.pro.fina_indicator(
            ts_code=ts_code,
            fields='ann_date,end_date,eps,bps,roe,netprofit_margin,grossprofit_margin,debt_to_assets'
        )
        if fina is not None and not fina.empty:
            row = fina.iloc[0]
            result['财务数据'] = row.to_dict()
    except Exception as e:
        logging.warning(f"stock_cpbd_em 获取财务指标失败 {symbol}: {e}")

    try:
        # 每日指标（最新交易日）
        basic = tushare_data.pro.daily_basic(
            ts_code=ts_code,
            fields='trade_date,pe,pe_ttm,pb,ps,turnover_rate,volume_ratio,total_mv,circ_mv'
        )
        if basic is not None and not basic.empty:
            result['行情指标'] = basic.iloc[0].to_dict()
    except Exception as e:
        logging.warning(f"stock_cpbd_em 获取每日指标失败 {symbol}: {e}")

    try:
        # 融资融券（最近一日）
        margin = tushare_data.pro.margin_detail(ts_code=ts_code)
        if margin is not None and not margin.empty:
            result['融资融券'] = margin.iloc[0].to_dict()
    except Exception as e:
        logging.warning(f"stock_cpbd_em 获取融资融券失败 {symbol}: {e}")

    return result


def stock_zjlx_em(symbol: str = "000001") -> pd.DataFrame:
    """
    个股资金流向历史（Tushare moneyflow 接口）
    :param symbol: 6位股票代码
    :return: 资金流向历史 DataFrame
    """
    if not tushare_data.is_available():
        return pd.DataFrame()

    try:
        ts_code = f"{symbol}.SH" if symbol.startswith(('6', '9')) else f"{symbol}.SZ"
        df = tushare_data.pro.moneyflow(ts_code=ts_code)
        if df is None or df.empty:
            return pd.DataFrame()

        df = df.sort_values('trade_date').reset_index(drop=True)

        import numpy as np
        result = pd.DataFrame({
            '日期':        pd.to_datetime(df['trade_date']).dt.date,
            '主力净流入额':  pd.to_numeric(df.get('buy_elg_amount', 0), errors='coerce').fillna(0) +
                           pd.to_numeric(df.get('buy_lg_amount', 0),  errors='coerce').fillna(0) -
                           pd.to_numeric(df.get('sell_elg_amount', 0), errors='coerce').fillna(0) -
                           pd.to_numeric(df.get('sell_lg_amount', 0),  errors='coerce').fillna(0),
            '超大单净流入额': pd.to_numeric(df.get('buy_elg_amount', 0), errors='coerce').fillna(0) -
                             pd.to_numeric(df.get('sell_elg_amount', 0), errors='coerce').fillna(0),
            '大单净流入额':   pd.to_numeric(df.get('buy_lg_amount', 0), errors='coerce').fillna(0) -
                             pd.to_numeric(df.get('sell_lg_amount', 0), errors='coerce').fillna(0),
            '中单净流入额':   pd.to_numeric(df.get('buy_md_amount', 0), errors='coerce').fillna(0) -
                             pd.to_numeric(df.get('sell_md_amount', 0), errors='coerce').fillna(0),
            '小单净流入额':   pd.to_numeric(df.get('buy_sm_amount', 0), errors='coerce').fillna(0) -
                             pd.to_numeric(df.get('sell_sm_amount', 0), errors='coerce').fillna(0),
        })
        return result
    except Exception as e:
        logging.error(f"stock_zjlx_em获取资金流向失败 {symbol}: {e}")
        return pd.DataFrame()


if __name__ == "__main__":
    result = stock_cpbd_em(symbol="000001")
    print(result)

    df = stock_zjlx_em(symbol="000001")
    print(df)
