# InStock 股票量化投资系统 - 项目目录结构说明

```
instock/
├── bin/                          # 启动脚本
│   ├── run_web.sh                # 启动Web服务（端口9988）
│   ├── run_cron.sh               # 启动cron定时任务服务（前台运行）
│   ├── run_job.sh                # 手动执行定时作业脚本（支持批量/区间作业）
│   ├── run_job.bat               # Windows下手动执行定时作业
│   ├── run_trade.bat             # Windows下启动交易服务
│   ├── run_web.bat               # Windows下启动Web服务
│   └── restart_web.sh            # 重启Web服务（杀进程后重启）
│
├── config/                       # 配置文件
│   ├── tushare_token.txt         # Tushare API Token（主要数据源认证）
│   ├── eastmoney_cookie.txt      # 东方财富网Cookie（备用/兼容）
│   ├── proxy.txt                 # 代理服务器列表（每行一个）
│   ├── trade_client.json         # 券商客户端配置（账号/路径等）
│   └── .gitignore                # Git忽略敏感配置文件
│
├── core/                         # 核心业务逻辑
│   ├── crawling/                 # 数据抓取模块（已全面重构为Tushare API）
│   │   ├── tushare_data.py       # Tushare数据封装类（核心模块，封装所有Tushare API接口）
│   │   │                         #   - 股票列表/实时行情/K线数据
│   │   │                         #   - 龙虎榜详情/机构买卖统计（top_list）
│   │   │                         #   - 个股资金流向/板块资金流向（moneyflow）
│   │   │                         #   - 大宗交易明细/每日统计（block_trade）
│   │   │                         #   - 分红送配（dividend）
│   │   │                         #   - ETF实时/历史行情（fund_basic/fund_daily）
│   │   │                         #   - 操盘必读（daily_basic/fina_indicator）
│   │   │                         #   - 综合选股（stock_basic/daily_basic，206列完整输出）
│   │   ├── stock_hist_tushare.py # Tushare K线数据获取器（日线/周线/月线/复权处理/实时行情）
│   │   ├── stock_hist_em.py      # A股K线数据（已代理到Tushare，保留原函数签名）
│   │   ├── stock_lhb_em.py       # 龙虎榜详情+机构买卖统计（已重构为Tushare top_list）
│   │   ├── stock_lhb_sina.py     # 新浪财经-龙虎榜每日详情（独立数据源，未改动）
│   │   ├── stock_fund_em.py      # 资金流向排名（已重构为Tushare moneyflow）
│   │   ├── stock_dzjy_em.py      # 大宗交易明细+统计（已重构为Tushare block_trade）
│   │   ├── stock_fhps_em.py      # 分红送配数据（已重构为Tushare dividend）
│   │   ├── stock_cpbd.py         # 操盘必读（已重构为Tushare daily_basic/fina_indicator）
│   │   ├── stock_selection.py    # 综合选股器（已重构为Tushare stock_basic/daily_basic）
│   │   ├── stock_chip_race.py    # 通达信竞价抢筹（早盘抢筹/尾盘抢筹，独立数据源）
│   │   ├── stock_limitup_reason.py # 同花顺涨停原因（按日期查询涨停板块及原因，独立数据源）
│   │   ├── fund_etf_em.py        # ETF实时+历史行情（已重构为Tushare fund_basic/fund_daily）
│   │   ├── trade_date_hist.py    # 新浪财经-交易日历（解析JS获取交易日列表）
│   │   └── __init__.py
│   │
│   ├── indicator/                # 技术指标计算模块
│   │   └── calculate_indicator.py # 30+技术指标计算（MACD/KDJ/BOLL/RSI/ATR/CCI/DMI/CR/VR/EMV/OBV/MFI/SAR/Supertrend等）
│   │
│   ├── kline/                    # K线可视化模块
│   │   ├── visualization.py      # Bokeh K线图绘制（K线+均线+成交量+技术指标+形态标注）
│   │   ├── cyq.py                # 筹码分布算法（CYQ计算器，基于成交量分布模型）
│   │   ├── cyq.js                # 筹码分布前端计算（JavaScript版CYQ算法）
│   │   ├── indicator_web_dic.py  # Web端技术指标配置字典（指标名称/描述/字段映射）
│   │   └── __init__.py
│   │
│   ├── pattern/                  # K线形态识别模块
│   │   └── pattern_recognitions.py # 60+K线形态识别（基于TA-Lib，十字星/锤子线/吞没/三兵等）
│   │
│   ├── strategy/                 # 选股策略模块（10种策略）
│   │   ├── enter.py              # 放量上涨策略（量比>=2且涨幅>=2%）
│   │   ├── turtle_trade.py       # 海龟交易法则（收盘价>=60日最高收盘价）
│   │   ├── breakthrough_platform.py # 平台突破策略（横盘后放量突破60日均线）
│   │   ├── climax_limitdown.py   # 高潮跌停策略（跌停后放量反转）
│   │   ├── high_tight_flag.py    # 高紧旗形策略（高位缩量整理，需龙虎榜机构）
│   │   ├── keep_increasing.py    # 持续上涨策略（MA30多头排列，30日涨幅>20%）
│   │   ├── low_atr.py            # 低ATR成长策略（低波动率+长期上涨趋势）
│   │   ├── low_backtrace_increase.py # 低回撤上涨策略（60日内无大幅回撤）
│   │   ├── backtrace_ma250.py    # 回踩年线策略（突破MA250后回踩缩量）
│   │   └── parking_apron.py      # 停机坪策略（涨停后高位缩量横盘整理）
│   │
│   ├── backtest/                 # 回测模块
│   │   └── rate_stats.py         # 策略回测统计（计算1/3/5/10/20/60日收益率）
│   │
│   ├── stockfetch.py             # 数据获取与缓存核心调度（统一调用crawling模块，管理缓存与入库格式）
│   ├── tablestructure.py         # 数据库表结构定义（26张表的字段/类型/中文映射）
│   ├── eastmoney_fetcher.py      # 东方财富请求封装（Cookie管理/会话复用/重试机制/代理支持，兼容保留）
│   ├── singleton_proxy.py        # 代理单例（读取proxy.txt，随机选择代理）
│   ├── singleton_stock.py        # 股票数据单例（当日行情/历史K线缓存，多线程并发获取）
│   ├── singleton_stock_web_module_data.py # Web模块数据单例（左侧菜单/表格配置/数据源注册）
│   ├── singleton_trade_date.py   # 交易日历单例（缓存交易日列表）
│   └── web_module_data.py        # Web模块数据结构定义（表格模式/图标/列名/排序等配置）
│
├── job/                          # 定时任务脚本
│   ├── execute_daily_job.py      # 主作业入口（串联所有子任务，支持单日/批量/区间参数）
│   ├── init_job.py               # 数据库初始化（创建数据库+所有表结构）
│   ├── basic_data_daily_job.py   # 基础数据-盘中实时作业（股票/ETF实时行情入库）
│   ├── basic_data_other_daily_job.py # 基础数据-非实时作业（龙虎榜/资金流/分红/大宗交易/抢筹/涨停原因/操盘必读）
│   ├── basic_data_after_close_daily_job.py # 基础数据-收盘后作业（大宗交易/ETF行情入库）
│   ├── selection_data_daily_job.py # 综合选股作业（选股器数据入库）
│   ├── indicators_data_daily_job.py # 技术指标作业（30+指标计算并入库）
│   ├── klinepattern_data_daily_job.py # K线形态作业（60+形态识别并入库）
│   ├── strategy_data_daily_job.py # 策略选股作业（10种策略并行计算并入库）
│   ├── backtest_data_daily_job.py # 回测作业（策略指标买卖信号回测统计）
│   ├── fetch_historical_data.py  # 历史数据批量抓取脚本（Tushare API，按日期+股票批量入库）
│   ├── test_fetch.py             # 数据抓取测试脚本（测试Tushare连接与数据入库）
│   └── __init__.py
│
├── lib/                          # 基础库
│   ├── database.py               # 数据库连接与操作（PostgreSQL/psycopg2+SQLAlchemy，增删改查/批量入库/表检查）
│   ├── torndb.py                 # Tornado数据库封装（轻量级psycopg2包装，供Web层使用）
│   ├── trade_time.py             # 交易日判断（是否交易日/上一交易日/交易时段判断）
│   ├── run_template.py           # 作业运行模板（解析命令行日期参数，支持单日/批量/区间执行）
│   ├── singleton_type.py         # 单例模式元类（线程安全，基于RLock双重检查）
│   ├── crypto_aes.py             # AES加密/解密工具（用于敏感配置加密存储）
│   └── version.py                # 版本号定义（当前版本4.0.0）
│
├── trade/                        # 自动交易引擎
│   ├── trade_service.py          # 交易服务入口（加载券商配置，启动主引擎+策略）
│   ├── robot/                    # 事件驱动引擎
│   │   ├── engine/
│   │   │   ├── main_engine.py    # 主引擎（管理事件/时钟/行情引擎，策略热加载，easytrader下单）
│   │   │   ├── event_engine.py   # 事件驱动引擎（事件队列+处理线程+监听器注册）
│   │   │   └── clock_engine.py   # 时钟引擎（定时触发策略，支持交易时段/间隔/每日定时）
│   │   └── infrastructure/
│   │       ├── strategy_template.py # 策略模板基类（定义init/on_tick/on_clock/on_trade接口）
│   │       ├── strategy_wrapper.py  # 策略进程包装（独立进程运行策略，通过队列通信）
│   │       └── default_handler.py   # 默认日志处理器（支持stdout/file两种输出方式）
│   ├── strategies/               # 交易策略实现
│   │   ├── stratey1.py           # 示例策略1（自定义交易逻辑模板）
│   │   └── stagging.py           # 打新策略（新股申购策略模板）
│   └── usage.md                  # 交易引擎使用说明
│
└── web/                          # Web前端
    ├── web_service.py            # Tornado Web服务（路由定义/数据库连接/端口9988）
    ├── dataTableHandler.py       # 数据表格请求处理（查询/分页/排序/JSON序列化）
    ├── dataIndicatorsHandler.py  # 技术指标K线图处理（获取K线数据+Bokeh图表渲染+关注操作）
    ├── base.py                   # 基础Handler（数据库连接检查/左侧菜单渲染）
    ├── templates/                # HTML模板
    │   ├── index.html            # 首页入口
    │   ├── stock_web.html        # 数据表格页（SpreadJS表格+日期选择+导出Excel）
    │   ├── stock_indicators.html # 技术指标页（Bokeh K线图+指标切换+形态标注）
    │   ├── common/               # 公共模板片段
    │   │   ├── header.html       # 页面头部
    │   │   ├── footer.html       # 页面底部
    │   │   ├── left_menu.html    # 左侧导航菜单
    │   │   └── meta.html         # HTML meta标签
    │   └── layout/               # 布局模板
    │       ├── default.html      # 默认布局
    │       ├── main.html         # 主页布局
    │       ├── indicators.html   # 指标页布局
    │       └── indicators-main.html # 指标主页布局
    └── static/                   # 静态资源
        ├── css/                  # 样式表（Bootstrap/Ace Admin/FontAwesome/SpreadJS/DatePicker）
        ├── js/                   # JavaScript（jQuery/Bootstrap/Bokeh/SpreadJS/Ace/FileSaver）
        ├── fonts/                # 字体文件（FontAwesome图标字体）
        └── img/                  # 图片（favicon图标）

cron/                             # 定时任务配置（Docker容器内使用）
├── cron.hourly/
│   └── run_hourly                # 每小时执行（盘中9:30-15:00每30分钟，拉取实时行情）
├── cron.workdayly/
│   └── run_workdayly             # 每工作日执行（17:30收盘后，执行全量日作业）
└── cron.monthly/
    └── run_monthly               # 每月执行（周三/周六10:30，清除历史缓存数据）

docker/                           # Docker部署配置
├── Dockerfile                    # 镜像构建（Python3.11+TA-Lib+PostgreSQL/Redis依赖+cron配置）
├── docker-compose.yml            # 容器编排（PostgreSQL + Redis + InStock服务）
├── build.sh                      # 构建脚本（rsync代码+docker build+push镜像）
├── .dockerignore                 # Docker构建忽略文件
└── .gitignore                    # Git忽略文件

supervisor/                       # Supervisor进程管理
└── supervisord.conf              # 进程管理配置（管理Web+cron服务）

img/                              # 项目截图
├── 00.jpg ~ 13.jpg               # 功能截图
└── a1.jpg, a3.jpg                # 策略截图

根目录文件：
├── requirements.txt              # Python依赖清单
├── LICENSE                       # 开源协议
├── README.md                     # 项目说明
└── mulu.md                       # 本文件（项目目录结构说明）
```

## 核心数据流

```
数据源层                    处理层                     存储层                 展示层
─────────                  ──────                     ──────                 ──────
Tushare API ───┐
  (主要数据源)  │           stockfetch.py ──→ PostgreSQL ──→ web_service.py
新浪财经 ──────┤           indicator/     ──→ PostgreSQL ──→ dataTableHandler.py
同花顺 ────────┤   ──→     pattern/       ──→ PostgreSQL ──→ dataIndicatorsHandler.py
通达信 ────────┘           strategy/      ──→ PostgreSQL ──→ Bokeh K线图
                           backtest/      ──→ PostgreSQL
```

> **数据源说明**：项目已从东方财富网爬虫全面重构为 Tushare API 数据源。`tushare_data.py` 作为核心封装层，
> 提供龙虎榜、资金流向、大宗交易、分红送配、ETF行情、操盘必读、综合选股等全部数据接口。
> 新浪财经（龙虎榜/交易日历）、同花顺（涨停原因）、通达信（竞价抢筹）作为独立数据源保留。

## 定时任务执行顺序

```
execute_daily_job.py（每日17:30触发）
  │
  ├─ 1. init_job              → 初始化数据库
  ├─ 2. basic_data_daily      → 股票/ETF实时行情入库（Tushare）
  ├─ 3. selection_data        → 综合选股入库（Tushare）
  ├─ 4. basic_data_other      → 龙虎榜/资金流/分红/大宗交易等（Tushare，并发）
  ├─ 5. indicators_data       → 技术指标计算入库（并发）
  ├─ 6. klinepattern_data     → K线形态识别入库（并发）
  ├─ 7. strategy_data         → 策略选股入库（并发）
  ├─ 8. backtest_data         → 策略回测统计（并发）
  └─ 9. after_close           → 收盘后数据入库（Tushare）
```

## Tushare API 接口映射

| 业务模块 | 爬虫文件 | Tushare接口 | 说明 |
|---------|---------|------------|------|
| K线数据 | stock_hist_tushare.py | `daily`/`weekly`/`monthly` | 日线/周线/月线+复权 |
| 实时行情 | stock_hist_tushare.py | `daily_basic` | 最新价/涨跌幅/换手率等 |
| 龙虎榜 | stock_lhb_em.py | `top_list` | 逐日查询，按日期区间聚合 |
| 资金流向 | stock_fund_em.py | `moneyflow` | 个股资金流+行业聚合板块资金流 |
| 大宗交易 | stock_dzjy_em.py | `block_trade` | 明细+按日/按股聚合统计 |
| 分红送配 | stock_fhps_em.py | `dividend` | 按报告期查询分红方案 |
| ETF行情 | fund_etf_em.py | `fund_basic`/`fund_daily` | ETF列表+历史行情 |
| 操盘必读 | stock_cpbd.py | `daily_basic`/`fina_indicator` | 基本面+财务指标 |
| 综合选股 | stock_selection.py | `stock_basic`/`daily_basic` | 206列完整输出兼容表结构 |
