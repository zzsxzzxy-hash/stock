import instock.lib.database as mdb

tables = [
    'cn_stock_spot_buy',
    'cn_stock_strategy_enter',
    'cn_stock_strategy_keep_increasing',
    'cn_stock_strategy_parking_apron',
    'cn_stock_strategy_backtrace_ma250',
    'cn_stock_strategy_breakthrough_platform',
    'cn_stock_strategy_low_backtrace_increase',
    'cn_stock_strategy_turtle_trade',
    'cn_stock_strategy_high_tight_flag',
    'cn_stock_strategy_climax_limitdown',
    'cn_stock_strategy_low_atr',
]
print('=== 策略表 ===')
for t in tables:
    try:
        r = mdb.executeSqlFetch('SELECT MAX(date), COUNT(*) FROM `' + t + '`')
        print(t + ': max=' + str(r[0][0]) + ' cnt=' + str(r[0][1]))
    except Exception as e:
        print(t + ': ERROR ' + str(e))

print()
print('=== 依赖表 ===')
for t in ['cn_stock_spot', 'cn_stock_hist_data', 'cn_stock_indicators']:
    try:
        r = mdb.executeSqlFetch('SELECT MAX(date), COUNT(*) FROM `' + t + '`')
        print(t + ': max=' + str(r[0][0]) + ' cnt=' + str(r[0][1]))
    except Exception as e:
        print(t + ': ERROR ' + str(e))
