// 菜单模块配置 — 与后端 singleton_stock_web_module_data 保持一致
export const menuModules = [
  {
    type: '自有策略',
    icon: 'MagicStick',
    items: [
      { name: '爆量股票', table: 'cn_stock_strategy_volume_surge', realtime: false, custom: true },
    ]
  },
  {
    type: '综合选股',
    icon: 'Monitor',
    items: [
      { name: '综合选股', table: 'cn_stock_selection', realtime: false },
    ]
  },
  {
    type: '股票基本数据',
    icon: 'DataBoard',
    items: [
      { name: '每日股票数据',   table: 'cn_stock_spot',                 realtime: true  },
      { name: '涨停原因揭密',   table: 'cn_stock_limitup_reason',       realtime: true  },
      { name: '股票资金流向',   table: 'cn_stock_fund_flow',            realtime: true  },
      { name: '股票分红配送',   table: 'cn_stock_bonus',                realtime: true  },
      { name: '股票龙虎榜',    table: 'cn_stock_lhb',                  realtime: true  },
      { name: '股票大宗交易',   table: 'cn_stock_blocktrade',           realtime: false },
      { name: '行业资金流向',   table: 'cn_stock_fund_flow_industry',   realtime: true  },
      { name: '概念资金流向',   table: 'cn_stock_fund_flow_concept',    realtime: true  },
      { name: '每日ETF数据',   table: 'cn_etf_spot',                   realtime: true  },
    ]
  },
  {
    type: '股票指标数据',
    icon: 'TrendCharts',
    items: [
      { name: '股票指标数据', table: 'cn_stock_indicators',      realtime: false },
      { name: '股票指标买入', table: 'cn_stock_indicators_buy',  realtime: false },
      { name: '股票指标卖出', table: 'cn_stock_indicators_sell', realtime: false },
    ]
  },
  {
    type: '股票K线形态',
    icon: 'PieChart',
    items: [
      { name: 'K线形态识别', table: 'cn_stock_kline_pattern', realtime: false },
    ]
  },
  {
    type: '股票策略数据',
    icon: 'Aim',
    items: [
      { name: '基本面选股',   table: 'cn_stock_spot_buy',                    realtime: false },
      { name: '放量上涨',     table: 'cn_stock_strategy_enter',              realtime: false },
      { name: '均线多头',     table: 'cn_stock_strategy_keep_increasing',    realtime: false },
      { name: '停机坪',       table: 'cn_stock_strategy_parking_apron',      realtime: false },
      { name: '回踩年线',     table: 'cn_stock_strategy_backtrace_ma250',    realtime: false },
      { name: '突破平台',     table: 'cn_stock_strategy_breakthrough_platform', realtime: false },
      { name: '无大幅回撤',   table: 'cn_stock_strategy_low_backtrace_increase', realtime: false },
      { name: '海龟交易法则', table: 'cn_stock_strategy_turtle_trade',       realtime: false },
      { name: '高而窄旗形',   table: 'cn_stock_strategy_high_tight_flag',    realtime: false },
      { name: '放量跌停',     table: 'cn_stock_strategy_climax_limitdown',   realtime: false },
      { name: '低ATR',        table: 'cn_stock_strategy_low_atr',            realtime: false },
    ]
  },
]
