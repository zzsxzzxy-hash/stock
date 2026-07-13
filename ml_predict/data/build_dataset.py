#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
构建训练数据集

从数据库拉取6-7月数据，提取特征，标注是否盈利
只包含：科创板、北交所、创业板
"""

import os
import sys
from datetime import datetime
from pathlib import Path

# 添加父目录到路径，以便导入 instock.lib.database
ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd
import numpy as np
import instock.lib.database as mdb


def get_market(code: str) -> str:
    """判断股票所属市场"""
    if code.startswith('688'):
        return '科创板'
    elif code.startswith('300') or code.startswith('301'):
        return '创业板'
    elif code.startswith('8') or code.startswith('4') or code.startswith('920'):
        return '北交所'
    elif code.startswith('60'):
        return '沪A'
    elif code.startswith('00'):
        return '深A'
    else:
        return '其他'


def fetch_raw_data(start_date='2026-06-01', end_date='2026-07-01'):
    """
    拉取原始数据
    返回：DataFrame with columns [date, code, ...]
    """
    print(f'拉取 {start_date} 至 {end_date} 的原始数据...')

    # 1. 早盘分钟线（09:35-09:55，用于计算早盘细节特征）
    print('  [1/8] 拉取早盘分钟线（09:35-09:55）...')
    sql_morning = f'''
        SELECT date::text as date, code, time, open, high, low, close, volume, amount
        FROM instockdb.cn_stock_minute_bar
        WHERE date >= '{start_date}' AND date < '{end_date}'
          AND time >= '09:35' AND time <= '09:55'
        ORDER BY date, code, time
    '''
    morning_rows = mdb.executeSqlFetch(sql_morning) or []
    df_morning_detail = pd.DataFrame(morning_rows, columns=[
        'date', 'code', 'time', 'open', 'high', 'low', 'close', 'volume', 'amount'
    ])
    print(f'      {len(df_morning_detail)} 条早盘分钟线')

    # 2. 全天行情
    print('  [2/8] 拉取全天行情...')
    sql_spot = f'''
        SELECT date, code, name, turnoverrate, volume_ratio,
               new_price, change_rate, amplitude, deal_amount
        FROM instockdb.cn_stock_spot
        WHERE date >= '{start_date}' AND date < '{end_date}'
    '''
    spot_rows = mdb.executeSqlFetch(sql_spot) or []
    df_spot = pd.DataFrame(spot_rows, columns=[
        'date', 'code', 'name', 'turnoverrate', 'daily_volume_ratio',
        'close_price', 'change_rate', 'amplitude', 'deal_amount'
    ])
    print(f'      {len(df_spot)} 条全天行情')

    # 3. 次日分钟线（09:35-10:00，用于计算次日收益）
    print('  [3/8] 拉取次日分钟线...')
    sql_next = f'''
        SELECT date::text as date, code, time, high, low, close
        FROM instockdb.cn_stock_minute_bar
        WHERE date >= '{start_date}' AND date < '{end_date}'
          AND time >= '09:35' AND time <= '10:00'
        ORDER BY date, code, time
    '''
    next_rows = mdb.executeSqlFetch(sql_next) or []
    df_next = pd.DataFrame(next_rows, columns=['date', 'code', 'time', 'high', 'low', 'close'])
    print(f'      {len(df_next)} 条次日分钟线')

    # 4. 主线映射（核心特征）
    print('  [4/8] 拉取主线映射...')
    sql_theme = '''
        SELECT code, trade_theme, confidence
        FROM instockdb.cn_stock_trade_theme
    '''
    theme_rows = mdb.executeSqlFetch(sql_theme) or []
    df_theme = pd.DataFrame(theme_rows, columns=['code', 'trade_theme', 'theme_confidence'])
    print(f'      {len(df_theme)} 只股票有主线')

    # 5. 历史日线（用于计算前N日涨跌幅）
    print('  [5/8] 拉取历史日线...')
    sql_hist = f'''
        SELECT date::text as date, code, close, volume, amount, turnover as turnover_hist, quote_change
        FROM instockdb.cn_stock_hist_data
        WHERE date >= '2026-05-01' AND date < '{end_date}'
        ORDER BY date, code
    '''
    hist_rows = mdb.executeSqlFetch(sql_hist) or []
    df_hist = pd.DataFrame(hist_rows, columns=[
        'date', 'code', 'hist_close', 'hist_volume', 'hist_amount', 'hist_turnover', 'hist_quote_change'
    ])
    print(f'      {len(df_hist)} 条历史日线')

    # 6. 市场情绪（每日涨跌停、成交额）
    print('  [6/8] 拉取市场情绪数据...')
    sql_market = f'''
        SELECT date,
               COUNT(*) as total_stocks,
               SUM(CASE WHEN CAST(change_rate AS FLOAT) >= 19.5 THEN 1 ELSE 0 END) as limit_up_count,
               SUM(CASE WHEN CAST(change_rate AS FLOAT) <= -19.5 THEN 1 ELSE 0 END) as limit_down_count,
               SUM(CASE WHEN CAST(change_rate AS FLOAT) > 0 THEN 1 ELSE 0 END) as red_count,
               SUM(CAST(deal_amount AS FLOAT)) as total_amount
        FROM instockdb.cn_stock_spot
        WHERE date >= '{start_date}' AND date < '{end_date}'
        GROUP BY date
    '''
    market_rows = mdb.executeSqlFetch(sql_market) or []
    df_market = pd.DataFrame(market_rows, columns=[
        'date', 'total_stocks', 'limit_up_count', 'limit_down_count', 'red_count', 'total_amount'
    ])
    print(f'      {len(df_market)} 个交易日的市场情绪')

    # 7. 交易日列表
    print('  [7/8] 获取交易日映射...')
    sql_dates = f'''
        SELECT DISTINCT date::text as date
        FROM instockdb.cn_stock_minute_bar
        WHERE date >= '2026-05-01' AND date < '{end_date}'
        ORDER BY date
    '''
    date_rows = mdb.executeSqlFetch(sql_dates) or []
    dates = [str(r[0]) for r in date_rows]
    date_map = {dates[i]: dates[i+1] for i in range(len(dates)-1) if i+1 < len(dates)}
    print(f'      {len(date_map)} 个交易日映射')

    # 8. 前一日收盘价（用于计算早盘涨幅）
    print('  [8/8] 拉取前日收盘价...')
    sql_prev_close = f'''
        SELECT date::text as date, code, close as prev_close
        FROM instockdb.cn_stock_hist_data
        WHERE date >= '2026-05-01' AND date < '{end_date}'
    '''
    prev_close_rows = mdb.executeSqlFetch(sql_prev_close) or []
    df_prev_close = pd.DataFrame(prev_close_rows, columns=['date', 'code', 'prev_close'])
    print(f'      {len(df_prev_close)} 条前日收盘价')

    return df_morning_detail, df_spot, df_next, df_theme, df_hist, df_market, df_prev_close, dates, date_map


def calculate_early_morning_features(df_main, df_morning_detail, df_prev_close, dates):
    """
    计算早盘细节特征（09:35-09:55）
    特征：涨幅、回撤、斜率、加速等
    优化：使用向量化计算，避免逐行遍历
    """
    print('计算早盘细节特征（09:35-09:55）...')

    # 构建前日收盘价映射
    date_to_prev = {dates[i]: dates[i-1] for i in range(1, len(dates))}

    # 为早盘分钟线添加前日收盘价
    df_morning_detail['prev_date'] = df_morning_detail['date'].map(date_to_prev)

    # 合并前日收盘价
    df_morning_detail = df_morning_detail.merge(
        df_prev_close.rename(columns={'date': 'prev_date'}),
        on=['prev_date', 'code'],
        how='left'
    )

    # 计算涨幅
    df_morning_detail['close_num'] = pd.to_numeric(df_morning_detail['close'], errors='coerce')
    df_morning_detail['prev_close_num'] = pd.to_numeric(df_morning_detail['prev_close'], errors='coerce')
    df_morning_detail['change_rate'] = (df_morning_detail['close_num'] / df_morning_detail['prev_close_num'] - 1) * 100

    print('  聚合早盘特征...')

    # 按股票聚合特征
    agg_features = df_morning_detail.groupby(['date', 'code']).agg({
        'change_rate': ['max', 'min', 'last', 'first', 'std'],
        'close_num': 'last',
    }).reset_index()

    agg_features.columns = ['date', 'code', 'max_change_0935_0955', 'min_change_0935_0955',
                             'final_change_0955', 'open_change_0935', 'slope_std', 'snapshot_close']

    # 回撤特征
    agg_features['pullback_ratio'] = np.where(
        agg_features['max_change_0935_0955'] > 0,
        (agg_features['max_change_0935_0955'] - agg_features['final_change_0955']) / agg_features['max_change_0935_0955'],
        np.nan
    )
    agg_features['pullback_abs'] = agg_features['max_change_0935_0955'] - agg_features['final_change_0955']

    # 红盘分钟数占比
    red_count = df_morning_detail[df_morning_detail['change_rate'] > 0].groupby(['date', 'code']).size().reset_index(name='red_count')
    total_count = df_morning_detail.groupby(['date', 'code']).size().reset_index(name='total_count')

    agg_features = agg_features.merge(red_count, on=['date', 'code'], how='left')
    agg_features = agg_features.merge(total_count, on=['date', 'code'], how='left')
    agg_features['red_count'].fillna(0, inplace=True)
    agg_features['red_minutes_ratio'] = agg_features['red_count'] / agg_features['total_count']

    # 斜率特征（简化版：用标准差代表波动）
    # 完整的斜率计算太复杂，后续可以优化
    agg_features['max_slope'] = agg_features['slope_std'] * 2  # 近似
    agg_features['avg_slope'] = (agg_features['final_change_0955'] - agg_features['open_change_0935']) / 20  # 平均每分钟
    agg_features['acceleration_count'] = 0  # 简化，后续可以优化
    agg_features['snapshot_time'] = '09:55'

    # 合并到主数据集
    df_main = df_main.merge(
        agg_features[['date', 'code', 'max_change_0935_0955', 'min_change_0935_0955', 'final_change_0955',
                       'open_change_0935', 'pullback_ratio', 'pullback_abs', 'max_slope', 'avg_slope',
                       'slope_std', 'red_minutes_ratio', 'acceleration_count', 'snapshot_close', 'snapshot_time']],
        on=['date', 'code'],
        how='left'
    )

    print(f'  早盘特征计算完成')
    return df_main


def calculate_snapshot_volume_ratio(df_main, df_morning_detail):
    """
    计算快照量比：当前累计成交额 / 昨日同期累计成交额（基于早盘分钟线）
    """
    print('计算快照量比...')

    # 从早盘分钟线计算累计成交额
    sql_cum = '''
        SELECT date::text as date, code, SUM(CAST(amount AS FLOAT)) as cum_amount
        FROM (
            SELECT date, code, time, amount
            FROM instockdb.cn_stock_minute_bar
            WHERE date >= '2026-05-01' AND date < '2026-08-01'
              AND time >= '09:30' AND time <= '09:55'
        ) t
        GROUP BY date, code
    '''

    import instock.lib.database as mdb
    rows = mdb.executeSqlFetch(sql_cum) or []
    df_cum = pd.DataFrame(rows, columns=['date', 'code', 'cum_amount'])
    df_cum['cum_amount'] = pd.to_numeric(df_cum['cum_amount'], errors='coerce')

    print(f'  已聚合 {len(df_cum)} 条累计成交额')

    # 获取交易日列表
    sql_dates = '''
        SELECT DISTINCT date::text as date
        FROM instockdb.cn_stock_minute_bar
        WHERE date >= '2026-05-01' AND date < '2026-08-01'
        ORDER BY date
    '''
    date_rows = mdb.executeSqlFetch(sql_dates) or []
    dates = [str(r[0]) for r in date_rows]
    date_to_prev = {dates[i]: dates[i-1] for i in range(1, len(dates))}

    # 为 df_main 添加昨日日期
    df_main['prev_date'] = df_main['date'].map(date_to_prev)

    # 合并当日累计
    df_main = df_main.merge(
        df_cum[['date', 'code', 'cum_amount']],
        on=['date', 'code'],
        how='left'
    ).rename(columns={'cum_amount': 'today_cum'})

    # 合并昨日累计
    df_main = df_main.merge(
        df_cum[['date', 'code', 'cum_amount']],
        left_on=['prev_date', 'code'],
        right_on=['date', 'code'],
        how='left',
        suffixes=('', '_prev')
    ).rename(columns={'cum_amount': 'prev_cum'})

    # 向量化计算量比
    df_main['snapshot_volume_ratio'] = df_main['today_cum'] / df_main['prev_cum']

    # 清理辅助列
    df_main.drop(columns=['prev_date', 'today_cum', 'prev_cum', 'date_prev', 'code_prev'], inplace=True, errors='ignore')

    print(f'  计算完成，{len(df_main)} 条')

    return df_main


def calculate_sector_and_market_features(df_main, df_spot_all, df_market):
    """
    计算板块联动和市场情绪特征
    """
    print('计算板块联动和市场情绪特征...')

    # 1. 市场情绪特征（合并到主数据集）
    df_market = df_market.copy()
    df_market['red_ratio'] = df_market['red_count'] / df_market['total_stocks']
    df_market['limit_up_ratio'] = df_market['limit_up_count'] / df_market['total_stocks']
    df_market['limit_down_ratio'] = df_market['limit_down_count'] / df_market['total_stocks']
    df_market['total_amount_billions'] = pd.to_numeric(df_market['total_amount'], errors='coerce') / 1e9

    df_main = df_main.merge(
        df_market[['date', 'red_ratio', 'limit_up_ratio', 'limit_down_ratio', 'total_amount_billions']],
        on='date',
        how='left'
    )

    # 2. 板块联动特征
    print('  计算板块联动特征...')

    # 为每个主线计算当日平均涨幅
    theme_performance = df_spot_all.merge(
        df_main[['code', 'trade_theme']].drop_duplicates(),
        on='code',
        how='inner'
    )

    theme_performance['change_rate_num'] = pd.to_numeric(theme_performance['change_rate'], errors='coerce')
    theme_performance['volume_ratio_num'] = pd.to_numeric(theme_performance['daily_volume_ratio'], errors='coerce')

    theme_daily_perf = theme_performance.groupby(['date', 'trade_theme']).agg({
        'change_rate_num': ['mean', 'median', 'count'],
        'volume_ratio_num': 'mean'
    }).reset_index()

    theme_daily_perf.columns = ['date', 'trade_theme', 'theme_avg_change', 'theme_median_change',
                                  'theme_stock_count', 'theme_avg_volume_ratio']

    # 合并到主数据集
    df_main = df_main.merge(
        theme_daily_perf,
        on=['date', 'trade_theme'],
        how='left'
    )

    # 填充无主线的股票
    df_main['theme_avg_change'].fillna(0, inplace=True)
    df_main['theme_median_change'].fillna(0, inplace=True)
    df_main['theme_stock_count'].fillna(0, inplace=True)
    df_main['theme_avg_volume_ratio'].fillna(0, inplace=True)

    print(f'  板块联动和市场情绪特征计算完成')
    return df_main

    # 合并昨日累计
    df_morning = df_morning.merge(
        df_cum[['date', 'code', 'cum_amount']],
        left_on=['prev_date', 'code'],
        right_on=['date', 'code'],
        how='left',
        suffixes=('', '_prev')
    ).rename(columns={'cum_amount': 'prev_cum'})

    # 向量化计算量比
    df_morning['snapshot_volume_ratio'] = df_morning['today_cum'] / df_morning['prev_cum']

    # 清理辅助列
    df_morning.drop(columns=['prev_date', 'today_cum', 'prev_cum', 'date_prev', 'code_prev'], inplace=True, errors='ignore')

    print(f'  计算完成，{len(df_morning)} 条')

    return df_morning


def calculate_sector_and_market_features(df_main, df_spot_all, df_market):
    """
    计算板块联动和市场情绪特征
    """
    print('计算板块联动和市场情绪特征...')

    # 1. 市场情绪特征（合并到主数据集）
    df_market = df_market.copy()
    df_market['red_ratio'] = df_market['red_count'] / df_market['total_stocks']
    df_market['limit_up_ratio'] = df_market['limit_up_count'] / df_market['total_stocks']
    df_market['limit_down_ratio'] = df_market['limit_down_count'] / df_market['total_stocks']
    df_market['total_amount_billions'] = pd.to_numeric(df_market['total_amount'], errors='coerce') / 1e9

    df_main = df_main.merge(
        df_market[['date', 'red_ratio', 'limit_up_ratio', 'limit_down_ratio', 'total_amount_billions']],
        on='date',
        how='left'
    )

    # 2. 板块联动特征
    print('  计算板块联动特征...')

    # 为每个主线计算当日平均涨幅
    theme_performance = df_spot_all.merge(
        df_main[['code', 'trade_theme']].drop_duplicates(),
        on='code',
        how='inner'
    )

    theme_performance['change_rate_num'] = pd.to_numeric(theme_performance['change_rate'], errors='coerce')
    theme_performance['volume_ratio_num'] = pd.to_numeric(theme_performance['daily_volume_ratio'], errors='coerce')

    theme_daily_perf = theme_performance.groupby(['date', 'trade_theme']).agg({
        'change_rate_num': ['mean', 'median', 'count'],
        'volume_ratio_num': 'mean'
    }).reset_index()

    theme_daily_perf.columns = ['date', 'trade_theme', 'theme_avg_change', 'theme_median_change',
                                  'theme_stock_count', 'theme_avg_volume_ratio']

    # 合并到主数据集
    df_main = df_main.merge(
        theme_daily_perf,
        on=['date', 'trade_theme'],
        how='left'
    )

    # 填充无主线的股票
    df_main['theme_avg_change'].fillna(0, inplace=True)
    df_main['theme_median_change'].fillna(0, inplace=True)
    df_main['theme_stock_count'].fillna(0, inplace=True)
    df_main['theme_avg_volume_ratio'].fillna(0, inplace=True)

    print(f'  板块联动和市场情绪特征计算完成')
    return df_main


def calculate_next_day_return(df_main, df_next, date_map):
    """
    计算当日收益和次日收益（优化版：预先建立索引）

    交易逻辑：
    1. T日 09:45-10:00 买入（快照价格）
    2. T日 15:00 收盘 → 当日收益（核心指标）
    3. T+1日 09:35-10:00 卖出 → 次日收益

    卖出规则：
    - 如果09:35后变绿 → 卖在09:45前最低点
    - 如果保持红盘 → 卖在09:35-09:50最高点-2%
    """
    print('计算当日收益和次日收益...')

    # 为 df_main 添加次日列
    df_main['next_date'] = df_main['date'].map(date_map)

    # 预先过滤次日分钟线到需要的时间段
    df_next_0935 = df_next[df_next['time'] == '09:35'].copy()
    df_next_before_0945 = df_next[df_next['time'] <= '09:45'].copy()
    df_next_0935_0950 = df_next[(df_next['time'] >= '09:35') & (df_next['time'] <= '09:50')].copy()

    # 构建索引字典
    print('  构建次日数据索引...')

    # 09:35 的收盘价和最低价
    next_0935_dict = {}
    for _, row in df_next_0935.iterrows():
        key = (row['date'], row['code'])
        next_0935_dict[key] = {'close': float(row['close']), 'low': float(row['low'])}

    # 09:45 前的最低价
    next_before_0945_low = df_next_before_0945.groupby(['date', 'code'])['low'].min().to_dict()

    # 09:35-09:50 的最高价
    next_0935_0950_high = df_next_0935_0950.groupby(['date', 'code'])['high'].max().to_dict()

    print('  开始计算收益...')

    intraday_returns = []        # 当日收益
    overnight_returns = []       # 隔夜收益
    total_returns = []           # 总收益
    sell_prices = []
    sell_reasons = []

    for idx, row in df_main.iterrows():
        date = row['date']
        next_date = row['next_date']
        code = row['code']
        buy_price = float(row['snapshot_close'])

        # 当日收盘价（从 close_price 列获取）
        close_price = float(row['close_price']) if pd.notna(row['close_price']) else np.nan

        # 计算当日收益（买入价 → 收盘价）
        if pd.notna(close_price) and buy_price > 0:
            intraday_ret = (close_price / buy_price - 1) * 100
        else:
            intraday_ret = np.nan

        # 计算次日收益（收盘价 → 次日卖出价）
        if pd.isna(next_date) or buy_price <= 0 or pd.isna(close_price):
            overnight_ret = np.nan
            total_ret = np.nan
            sell_price = np.nan
            reason = '无次日数据'
        else:
            key = (next_date, code)

            # 获取 09:35 数据
            bar_0935 = next_0935_dict.get(key)
            if not bar_0935:
                overnight_ret = np.nan
                total_ret = np.nan
                sell_price = np.nan
                reason = '无次日分钟线'
            else:
                # 判断次日卖出价格
                if bar_0935['close'] < close_price:
                    # 变绿：卖在09:45前最低点
                    sell_price = float(next_before_0945_low.get(key, bar_0935['low']))
                    reason = '变绿卖最低'
                else:
                    # 红盘：卖在09:35-09:50最高点-2%
                    highest = next_0935_0950_high.get(key)
                    if highest:
                        sell_price = float(highest) * 0.98
                        reason = '红盘卖高点-2%'
                    else:
                        sell_price = bar_0935['close']
                        reason = '兜底'

                # 隔夜收益（收盘价 → 次日卖出价）
                if sell_price > 0:
                    overnight_ret = (sell_price / close_price - 1) * 100
                    # 总收益（买入价 → 次日卖出价）
                    total_ret = (sell_price / buy_price - 1) * 100
                else:
                    overnight_ret = np.nan
                    total_ret = np.nan

        intraday_returns.append(intraday_ret)
        overnight_returns.append(overnight_ret)
        total_returns.append(total_ret)
        sell_prices.append(sell_price)
        sell_reasons.append(reason)

        if (idx + 1) % 10000 == 0:
            print(f'  已处理 {idx+1}/{len(df_main)}...')

    df_main['intraday_return'] = intraday_returns      # 当日收益（核心）
    df_main['overnight_return'] = overnight_returns    # 隔夜收益
    df_main['total_return'] = total_returns            # 总收益
    df_main['next_day_sell_price'] = sell_prices
    df_main['sell_reason'] = sell_reasons

    # 清理辅助列
    df_main.drop(columns=['next_date'], inplace=True, errors='ignore')

    print(f'  计算完成，{len(intraday_returns)} 条')

    return df_main


def calculate_historical_features(df_main, df_hist, df_spot_all, dates):
    """
    计算历史特征：前N日涨跌幅、量比等（优化版：预先构建索引）
    增加：前1日量比和涨幅（用于缩量上涨特征）
    """
    print('计算历史特征...')

    # 构建历史数据透视表
    hist_pivot = df_hist.pivot_table(
        index=['date', 'code'],
        values=['hist_close', 'hist_quote_change', 'hist_turnover'],
        aggfunc='first'
    ).reset_index()

    # 构建 spot 数据透视表（用于获取前日量比）
    spot_pivot = df_spot_all.pivot_table(
        index=['date', 'code'],
        values=['daily_volume_ratio', 'change_rate'],
        aggfunc='first'
    ).reset_index()

    # 日期到索引的映射
    date_to_idx = {d: i for i, d in enumerate(dates)}

    # 为每个日期准备前 N 日的映射
    date_shifts = {}
    for i, date in enumerate(dates):
        date_shifts[date] = {
            'prev_1': dates[i-1] if i-1 >= 0 else None,
            'prev_3': [dates[i-j] for j in range(1, 4) if i-j >= 0],
            'prev_5': [dates[i-j] for j in range(1, 6) if i-j >= 0],
            'prev_10': [dates[i-j] for j in range(1, 11) if i-j >= 0],
        }

    # 为每个 code 构建历史数据字典
    print('  构建历史数据索引...')
    hist_by_code = {}
    for _, row in hist_pivot.iterrows():
        code = row['code']
        date = row['date']
        if code not in hist_by_code:
            hist_by_code[code] = {}
        hist_by_code[code][date] = {
            'quote_change': float(row['hist_quote_change']) if pd.notna(row['hist_quote_change']) else np.nan,
            'turnover': float(row['hist_turnover']) if pd.notna(row['hist_turnover']) else np.nan,
        }

    # 为每个 code 构建 spot 数据字典（量比和涨幅）
    spot_by_code = {}
    for _, row in spot_pivot.iterrows():
        code = row['code']
        date = row['date']
        if code not in spot_by_code:
            spot_by_code[code] = {}
        spot_by_code[code][date] = {
            'volume_ratio': float(row['daily_volume_ratio']) if pd.notna(row['daily_volume_ratio']) else np.nan,
            'change_rate': float(row['change_rate']) if pd.notna(row['change_rate']) else np.nan,
        }

    print('  计算前N日特征...')

    prev_1d_volume_ratio = []   # 前1日量比
    prev_1d_change_rate = []    # 前1日涨幅
    prev_3d_returns = []
    prev_5d_returns = []
    prev_10d_returns = []
    prev_3d_turnover = []
    prev_5d_turnover = []

    for idx, row in df_main.iterrows():
        date = row['date']
        code = row['code']

        if date not in date_shifts or code not in hist_by_code:
            prev_1d_volume_ratio.append(np.nan)
            prev_1d_change_rate.append(np.nan)
            prev_3d_returns.append(np.nan)
            prev_5d_returns.append(np.nan)
            prev_10d_returns.append(np.nan)
            prev_3d_turnover.append(np.nan)
            prev_5d_turnover.append(np.nan)
            continue

        code_hist = hist_by_code[code]
        code_spot = spot_by_code.get(code, {})

        # 前1日量比和涨幅
        prev_1_date = date_shifts[date]['prev_1']
        if prev_1_date and prev_1_date in code_spot:
            prev_1d_volume_ratio.append(code_spot[prev_1_date].get('volume_ratio', np.nan))
            prev_1d_change_rate.append(code_spot[prev_1_date].get('change_rate', np.nan))
        else:
            prev_1d_volume_ratio.append(np.nan)
            prev_1d_change_rate.append(np.nan)

        # 前3日涨跌幅累计
        ret_3d = [code_hist.get(d, {}).get('quote_change', np.nan) for d in date_shifts[date]['prev_3']]
        prev_3d_returns.append(np.nansum(ret_3d) if ret_3d else np.nan)

        # 前5日涨跌幅累计
        ret_5d = [code_hist.get(d, {}).get('quote_change', np.nan) for d in date_shifts[date]['prev_5']]
        prev_5d_returns.append(np.nansum(ret_5d) if ret_5d else np.nan)

        # 前10日涨跌幅累计
        ret_10d = [code_hist.get(d, {}).get('quote_change', np.nan) for d in date_shifts[date]['prev_10']]
        prev_10d_returns.append(np.nansum(ret_10d) if ret_10d else np.nan)

        # 前3日平均换手率
        turnover_3d = [code_hist.get(d, {}).get('turnover', np.nan) for d in date_shifts[date]['prev_3']]
        prev_3d_turnover.append(np.nanmean(turnover_3d) if turnover_3d else np.nan)

        # 前5日平均换手率
        turnover_5d = [code_hist.get(d, {}).get('turnover', np.nan) for d in date_shifts[date]['prev_5']]
        prev_5d_turnover.append(np.nanmean(turnover_5d) if turnover_5d else np.nan)

        if (idx + 1) % 10000 == 0:
            print(f'  已处理 {idx+1}/{len(df_main)}...')

    df_main['prev_1d_volume_ratio'] = prev_1d_volume_ratio
    df_main['prev_1d_change_rate'] = prev_1d_change_rate
    df_main['prev_3d_return'] = prev_3d_returns
    df_main['prev_5d_return'] = prev_5d_returns
    df_main['prev_10d_return'] = prev_10d_returns
    df_main['prev_3d_turnover'] = prev_3d_turnover
    df_main['prev_5d_turnover'] = prev_5d_turnover

    print(f'  历史特征计算完成')
    return df_main


def build_features(df_main):
    """
    构建特征（强化主线映射的作用）
    """
    print('构建特征...')

    # 转换数值列为 float（数据库返回的是 Decimal）
    numeric_cols = ['snapshot_open', 'snapshot_high', 'snapshot_low', 'snapshot_close',
                    'snapshot_volume', 'snapshot_amount', 'turnoverrate', 'daily_volume_ratio',
                    'change_rate', 'amplitude', 'deal_amount', 'close_price']
    for col in numeric_cols:
        if col in df_main.columns:
            df_main[col] = pd.to_numeric(df_main[col], errors='coerce')

    # 基础特征
    df_main['market'] = df_main['code'].apply(get_market)

    # 处理可能的空值
    df_main['turnoverrate'] = pd.to_numeric(df_main['turnoverrate'], errors='coerce')
    df_main['daily_volume_ratio'] = pd.to_numeric(df_main['daily_volume_ratio'], errors='coerce')
    df_main['change_rate'] = pd.to_numeric(df_main['change_rate'], errors='coerce')
    df_main['amplitude'] = pd.to_numeric(df_main['amplitude'], errors='coerce')
    df_main['deal_amount'] = pd.to_numeric(df_main['deal_amount'], errors='coerce')

    # 快照与全天对比
    df_main['snapshot_vs_daily_volume'] = df_main['snapshot_volume_ratio'] / (df_main['daily_volume_ratio'] + 1e-6)

    # 缩量上涨特征（用户发现的关键特征）
    df_main['volume_change_ratio'] = df_main['daily_volume_ratio'] / (df_main['prev_1d_volume_ratio'] + 1e-6)  # 当日量比 / 前日量比
    df_main['price_change_delta'] = df_main['change_rate'] - df_main['prev_1d_change_rate']  # 当日涨幅 - 前日涨幅

    # 缩量上涨信号（二值特征）
    df_main['shrink_volume_rise'] = (
        (df_main['daily_volume_ratio'] < df_main['prev_1d_volume_ratio']) &  # 缩量
        (df_main['change_rate'] > df_main['prev_1d_change_rate'])           # 涨幅更大
    ).astype(int)

    # 主线映射特征（核心）
    df_main['has_theme'] = df_main['trade_theme'].notna().astype(int)
    df_main['theme_confidence'] = pd.to_numeric(df_main['theme_confidence'], errors='coerce').fillna(0)

    # 将主线转换为分类编码（用于后续的类别特征）
    df_main['trade_theme_encoded'] = df_main['trade_theme'].fillna('无主线')

    # 标签：多重标签
    df_main['label_intraday'] = (df_main['intraday_return'] > 0).astype(int)      # 当日是否盈利（核心）
    df_main['label_overnight'] = (df_main['overnight_return'] > 0).astype(int)    # 隔夜是否盈利
    df_main['label_total'] = (df_main['total_return'] > 0).astype(int)            # 总体是否盈利

    # 主标签：当日盈利
    df_main['label'] = df_main['label_intraday']

    print(f'  特征构建完成，共 {len(df_main.columns)} 个特征')

    return df_main


def main():
    print('='*80)
    print('构建训练数据集')
    print('='*80)
    print()

    start_time = datetime.now()

    # 1. 拉取原始数据
    df_morning_detail, df_spot, df_next, df_theme, df_hist, df_market, df_prev_close, dates, date_map = fetch_raw_data()

    # 2. 先计算早盘特征（需要完整的分钟线数据）
    print()
    print('准备主数据集...')
    # 从早盘分钟线中提取每个股票的最后一条记录作为快照
    df_morning_snapshot = df_morning_detail.sort_values(['date', 'code', 'time']).groupby(['date', 'code']).last().reset_index()
    df_main = df_morning_snapshot[['date', 'code']].copy()
    print(f'  {len(df_main)} 个股票快照')

    # 合并全天行情和主线
    df_main = df_main.merge(df_spot, on=['date', 'code'], how='inner')
    df_main = df_main.merge(df_theme, on='code', how='left')
    print(f'  合并后 {len(df_main)} 条')

    # 3. 只保留科创板、北交所、创业板，并过滤 ST 股票
    df_main['market'] = df_main['code'].apply(get_market)
    df_main = df_main[df_main['market'].isin(['科创板', '北交所', '创业板'])]
    print(f'  过滤市场后 {len(df_main)} 条')

    # 过滤 ST 股票（名称中包含 ST、*ST、S*ST 等）
    st_pattern = r'ST|退'
    before_st_filter = len(df_main)
    df_main = df_main[~df_main['name'].str.contains(st_pattern, case=False, na=False)]
    print(f'  过滤 ST 股票后 {len(df_main)} 条（移除 {before_st_filter - len(df_main)} 条）')

    # 4. 计算早盘细节特征（09:35-09:55）
    print()
    df_main = calculate_early_morning_features(df_main, df_morning_detail, df_prev_close, dates)

    # 5. 计算快照量比（基于早盘分钟线）
    print()
    df_main = calculate_snapshot_volume_ratio(df_main, df_morning_detail)

    # 6. 计算板块联动和市场情绪特征
    print()
    df_main = calculate_sector_and_market_features(df_main, df_spot, df_market)

    # 7. 计算当日和次日收益
    print()
    df_main = calculate_next_day_return(df_main, df_next, date_map)

    # 8. 计算历史特征（传入完整的 df_spot 用于获取前日量比）
    print()
    df_main = calculate_historical_features(df_main, df_hist, df_spot, dates)

    # 9. 构建特征
    print()
    df_main = build_features(df_main)

    # 8. 数据验证
    print()
    print('数据验证...')
    print(f'  重复记录: {df_main.duplicated(subset=["date", "code"]).sum()}')
    print(f'  缺失标签: {df_main["label"].isna().sum()}')
    print(f'  特征缺失率:')
    key_features = ['snapshot_volume_ratio', 'turnoverrate', 'daily_volume_ratio',
                    'prev_3d_return', 'has_theme', 'theme_confidence']
    for feat in key_features:
        if feat in df_main.columns:
            missing_rate = df_main[feat].isna().sum() / len(df_main) * 100
            print(f'    {feat}: {missing_rate:.1f}%')

    # 9. 保存数据集
    print()
    print('保存数据集...')
    output_dir = Path(__file__).parent / 'dataset_cache'
    output_dir.mkdir(exist_ok=True)

    output_file = output_dir / 'training_data_202606_202607.parquet'
    df_main.to_parquet(output_file, index=False)

    print(f'  保存到: {output_file}')
    print(f'  数据集大小: {len(df_main)} 条')
    print(f'  特征数量: {len(df_main.columns)} 个')
    print()

    # 10. 数据统计
    print('数据统计:')
    print(f'  总样本数: {len(df_main)}')
    print(f'  有效标签数: {df_main["label"].notna().sum()}')
    print()
    print(f'  当日盈利样本数: {(df_main["label_intraday"] == 1).sum()}')
    print(f'  当日亏损样本数: {(df_main["label_intraday"] == 0).sum()}')
    valid_intraday = df_main["label_intraday"].notna().sum()
    if valid_intraday > 0:
        print(f'  当日胜率: {(df_main["label_intraday"] == 1).sum() / valid_intraday * 100:.1f}%')
    print()
    print(f'  总收益盈利样本数: {(df_main["label_total"] == 1).sum()}')
    print(f'  总收益亏损样本数: {(df_main["label_total"] == 0).sum()}')
    valid_total = df_main["label_total"].notna().sum()
    if valid_total > 0:
        print(f'  总收益胜率: {(df_main["label_total"] == 1).sum() / valid_total * 100:.1f}%')
    print()
    print(f'  平均当日收益: {df_main["intraday_return"].mean():.2f}%')
    print(f'  平均隔夜收益: {df_main["overnight_return"].mean():.2f}%')
    print(f'  平均总收益: {df_main["total_return"].mean():.2f}%')
    print()
    print(f'  按市场统计（当日胜率）:')
    for market in ['科创板', '创业板', '北交所']:
        market_data = df_main[df_main['market'] == market]
        valid = market_data['label_intraday'].notna().sum()
        if valid > 0:
            win_rate = (market_data['label_intraday'] == 1).sum() / valid * 100
            avg_return = market_data['intraday_return'].mean()
            print(f'    {market}: {len(market_data)} 条, 当日胜率 {win_rate:.1f}%, 平均收益 {avg_return:.2f}%')

    print()
    print(f'  按主线统计（当日胜率 Top 10）:')
    theme_stats = df_main[df_main['trade_theme'].notna()].groupby('trade_theme').agg({
        'label_intraday': ['count', 'sum'],
        'intraday_return': 'mean'
    }).reset_index()
    theme_stats.columns = ['trade_theme', 'count', 'wins', 'avg_return']
    theme_stats['win_rate'] = theme_stats['wins'] / theme_stats['count'] * 100
    theme_stats = theme_stats.sort_values('win_rate', ascending=False).head(10)
    for _, row in theme_stats.iterrows():
        print(f'    {row["trade_theme"]}: {int(row["count"])} 条, 胜率 {row["win_rate"]:.1f}%, 平均收益 {row["avg_return"]:.2f}%')

    print()
    print(f'  缩量上涨特征验证:')
    shrink_rise = df_main[df_main['shrink_volume_rise'] == 1]
    normal = df_main[df_main['shrink_volume_rise'] == 0]
    if len(shrink_rise) > 0:
        shrink_win_rate = (shrink_rise['label_intraday'] == 1).sum() / len(shrink_rise) * 100
        shrink_avg_return = shrink_rise['intraday_return'].mean()
        print(f'    有缩量上涨特征: {len(shrink_rise)} 条, 胜率 {shrink_win_rate:.1f}%, 平均收益 {shrink_avg_return:.2f}%')
    if len(normal) > 0:
        normal_win_rate = (normal['label_intraday'] == 1).sum() / len(normal) * 100
        normal_avg_return = normal['intraday_return'].mean()
        print(f'    无缩量上涨特征: {len(normal)} 条, 胜率 {normal_win_rate:.1f}%, 平均收益 {normal_avg_return:.2f}%')
    if len(shrink_rise) > 0 and len(normal) > 0:
        diff = shrink_win_rate - normal_win_rate
        print(f'    胜率差异: {diff:+.1f}% （{"显著" if abs(diff) > 5 else "不显著"}）')

    print()
    elapsed = (datetime.now() - start_time).total_seconds()
    print(f'总耗时: {elapsed:.1f}s')
    print('='*80)


if __name__ == '__main__':
    main()
