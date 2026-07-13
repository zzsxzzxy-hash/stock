# InStock 股票分析系统 - 当前目录与功能说明

本文按当前代码结构说明系统模块、页面、API、任务和数据流。新版主要操作界面在 Vue3 SPA，旧版 Tornado 模板页面仍保留兼容。

## 顶层结构

```
.
├── AGENTS.md                    # 本仓库协作规则，股票/行情任务前必须确认真实时间
├── README.md                    # 项目运行与功能说明
├── mulu.md                      # 当前目录与功能说明
├── requirements.txt             # Python 依赖
├── check_strategy.py            # 策略检查脚本
├── dominant_sector_review.csv   # 主线/题材复盘数据产物
├── morning_signal_*.csv         # 早盘信号回放产物
├── cron/                        # Docker/服务器定时任务配置
├── docker/                      # PostgreSQL + Redis + InStock 编排
├── his-stock/                   # 历史数据相关目录
├── img/                         # 项目截图
├── instock/                     # 核心代码
├── outputs/                     # 分析、报表和导出产物
├── scripts/                     # 辅助脚本
└── supervisor/                  # Supervisor 配置
```

## instock 目录

```
instock/
├── bin/                         # 启动脚本
├── config/                      # token、cookie、代理、交易客户端等配置
├── core/                        # 行情、指标、策略、分钟线和量能核心逻辑
├── job/                         # 初始化、同步、补线、回放、校准等任务
├── lib/                         # 数据库、交易时间、加密、通用运行模板
├── trade/                       # 自动交易引擎和策略模板
└── web/                         # Tornado Web 服务、REST API、Vue3 前端和旧模板
```

## config

```
instock/config/
├── eastmoney_cookie.txt         # 东方财富 Cookie
├── proxy.txt                    # 代理列表
├── trade_client.json            # 券商客户端配置
└── xtick_token.txt              # XTick Token
```

说明：

- Tushare/XTick 等 token 可用环境变量，也可写入配置文件。
- `trade_client.json`、cookie、token 属于敏感配置，不应提交到 Git。

## core

```
instock/core/
├── crawling/                    # 数据抓取封装
├── indicator/                   # 技术指标计算
├── kline/                       # K 线、筹码和图表相关逻辑
├── pattern/                     # K 线形态识别
├── strategy/                    # 传统选股策略
├── backtest/                    # 策略回测统计
├── factor_config.py             # 量能/信号因子配置读写
├── minute_bar_collector.py      # 分钟 K 线采集、Redis 缓存和补全支持
├── volume_pre_calc.py           # 量能监控预计算
├── volume_rank_engine.py        # 量能排行计算引擎
├── stockfetch.py                # 数据获取与缓存调度
├── tablestructure.py            # 数据库表结构定义
├── singleton_stock_web_module_data.py # Web 表格菜单/列配置注册
└── web_module_data.py           # Web 表格模块数据结构
```

### crawling

主要封装 A 股/ETF/题材相关数据源：

- `tushare_data.py`：Tushare API 封装。
- `stock_hist_tushare.py`：日/周/月 K 线与复权处理。
- `stock_lhb_em.py`、`stock_lhb_sina.py`：龙虎榜。
- `stock_fund_em.py`：资金流向。
- `stock_dzjy_em.py`：大宗交易。
- `stock_fhps_em.py`：分红送配。
- `stock_limitup_reason.py`：同花顺涨停原因。
- `stock_chip_race.py`：通达信竞价抢筹。
- `fund_etf_em.py`：ETF 基础与行情数据。

### strategy

传统策略模块包括：

- 放量上涨
- 均线多头/持续上涨
- 停机坪
- 回踩年线
- 突破平台
- 无大幅回撤
- 海龟交易法则
- 高而窄旗形
- 放量跌停
- 低 ATR

当前超短主线接力逻辑主要在 `instock/web/volumeHandler.py` 和相关 job 中实现，不在传统 `core/strategy` 目录内。

## job

```
instock/job/
├── init_job.py                  # 初始化数据库表结构
├── execute_daily_job.py         # 每日任务总入口
├── daily_market_sync.py         # 日线/基础行情同步
├── batch_fetch_hist.py          # 历史行情批量抓取
├── batch_fetch_fund_flow.py     # 资金流批量抓取
├── batch_fetch_limitup_reason.py # 涨停原因批量抓取
├── fill_minute_bars.py          # 分钟 K 线补全
├── repair_minute_bars_by_daily.py # 按日线修复分钟线完整性
├── volume_monitor_daemon.py     # 盘中量能监控 daemon
├── init_volume_tables.py        # 量能相关表初始化
├── custom_strategy_daily_job.py # 自有策略日任务
├── rebuild_trade_theme.py       # 重建股票题材/主线映射
├── sync_sector_map.py           # 同步板块/题材映射
├── morning_signal_replay.py     # 早盘信号离线回放
├── calibrate_mainline_core.py   # 主线核心参数校准
├── backfill_operation_signal.py  # 当前版本回放并补齐操作系统判断
├── analyze_trade_selection_pattern.py # 交易选择模式分析
├── indicators_data_daily_job.py # 指标计算任务
├── klinepattern_data_daily_job.py # K 线形态任务
├── strategy_data_daily_job.py   # 传统策略任务
├── backtest_data_daily_job.py   # 策略回测任务
└── his_stock_importer.py        # 历史数据导入
```

常用任务链：

```
初始化表结构       init_job.py
同步日线/基础行情  daily_market_sync.py
补全分钟线         fill_minute_bars.py
修复分钟线         repair_minute_bars_by_daily.py
启动盘中监控       volume_monitor_daemon.py
回放早盘信号       morning_signal_replay.py
校准主线参数       calibrate_mainline_core.py
补齐操作系统判断   backfill_operation_signal.py
```

## lib

```
instock/lib/
├── database.py                  # PostgreSQL/psycopg2 + SQLAlchemy 连接
├── torndb.py                    # Tornado 使用的轻量 psycopg2 包装
├── trade_time.py                # 交易日和交易时段判断
├── trade_hours.py               # 分钟线采集窗口和预期分钟计算
├── run_template.py              # 作业运行模板
├── singleton_type.py            # 单例元类
├── crypto_aes.py                # AES 加密/解密
└── version.py                   # 版本号
```

## web

```
instock/web/
├── web_service.py               # Tornado 服务入口和路由，端口 9988
├── apiHandler.py                # 通用数据、K线、关注、操作记录、自有策略 API
├── volumeHandler.py             # 量能监控、龙头强势、主线核心、因子配置 API
├── systemHandler.py             # 系统健康、任务触发、分钟线补全状态 API
├── dataTableHandler.py          # 旧版表格页处理
├── dataIndicatorsHandler.py     # 旧版 K 线指标页处理
├── syncHandler.py               # 数据同步页面和 SSE 日志
├── base.py                      # BaseHandler 与旧版菜单
├── frontend/                    # Vue3 + Vite 前端
├── templates/                   # 旧版 Tornado HTML 模板
└── static/                      # 静态资源和 Vue 构建产物
```

### Tornado 路由

| 路由 | Handler | 说明 |
| --- | --- | --- |
| `/app/*` | `VueSPAHandler` | Vue3 SPA 入口 |
| `/`、`/instock/` | `HomeHandler` | 旧版首页 |
| `/instock/data` | `GetStockHtmlHandler` | 旧版表格页 |
| `/instock/data/indicators` | `GetDataIndicatorsHandler` | 旧版指标图表页 |
| `/instock/sync` | `SyncPageHandler` | 旧版同步页 |
| `/instock/api/sync*` | `syncHandler` | 同步任务和 SSE 日志 |
| `/api/meta` | `ApiMetaHandler` | 表格列定义 |
| `/api/data` | `ApiDataHandler` | 通用表格数据 |
| `/api/trade_date` | `ApiTradeDateHandler` | 最近交易日 |
| `/api/watchlist` | `ApiWatchlistHandler` | 关注列表 |
| `/api/operation_log` | `ApiOperationLogHandler` | 每日操作记录 |
| `/api/custom_strategy` | `ApiCustomStrategyHandler` | 自有策略表 |
| `/instock/api_data/kline` | `ApiKlineHandler` | 日/周/月 K 线 |
| `/api/minute_kline` | `ApiMinuteKlineHandler` | 1 分钟 K 线 |
| `/api/volume_rank` | `ApiVolumeRankHandler` | 量能异动排行 |
| `/api/volume_detail` | `ApiVolumeDetailHandler` | 个股量能详情 |
| `/api/leader_strength` | `ApiLeaderStrengthHandler` | 龙头强势 |
| `/api/mainline_core` | `ApiMainlineCoreHandler` | 主线核心观察/超短接力 |
| `/api/stock_signal_detail` | `ApiStockSignalDetailHandler` | 单股信号详情 |
| `/api/sector_*` | `volumeHandler` | 板块映射管理 |
| `/api/trade_theme*` | `volumeHandler` | 题材/主线映射 |
| `/api/redis_query`、`/api/redis_dates` | `volumeHandler` | Redis 查询 |
| `/api/system_health` | `ApiSystemHealthHandler` | 系统健康检查 |
| `/api/system_action` | `ApiSystemActionHandler` | 触发系统任务 |
| `/api/system_action_status` | `ApiSystemActionStatusHandler` | 任务状态 |
| `/api/factor_config` | `ApiFactorConfigHandler` | 因子配置 |
| `/api/minute_bars` | `ApiMinuteBarsHandler` | 分钟线序列 |
| `/api/score_single` | `ApiScoreSingleHandler` | 单股票因子计算 |

## Vue3 前端

```
instock/web/frontend/
├── package.json                 # Vue3/Vite/Element Plus/ECharts 依赖
├── vite.config.js               # Vite 配置，开发代理到 9988
└── src/
    ├── App.vue                  # 主布局、侧边栏和页面标题
    ├── main.js                  # Vue 入口
    ├── router/index.js          # SPA 路由
    ├── config/menus.js          # 表格类动态菜单
    ├── api/index.js             # 前端 API 封装
    ├── components/
    │   ├── MinuteBarChart.vue
    │   ├── RedisQuery.vue
    │   ├── SectorManager.vue
    │   ├── StockSignalDetail.vue
    │   ├── TradeThemeManager.vue
    │   └── VolumeRankRow.vue
    └── views/
        ├── HomeView.vue
        ├── SyncView.vue
        ├── WatchlistView.vue
        ├── OperationStrategyView.vue
        ├── OperationJournalView.vue
        ├── VolumeMonitorView.vue
        ├── LeaderStrengthView.vue
        ├── FactorConfigView.vue
        ├── ScoreSingleView.vue
        ├── SystemHealthView.vue
        ├── TableView.vue
        ├── CustomStrategyView.vue
        └── IndicatorsView.vue
```

### SPA 路由

| 路径 | 视图 | 功能 |
| --- | --- | --- |
| `/` | `HomeView` | 首页和系统任务入口 |
| `/sync` | `SyncView` | 数据同步管理 |
| `/watchlist` | `WatchlistView` | 我的关注 |
| `/operation_strategy` | `OperationStrategyView` | 超短主线接力 |
| `/operation_journal` | `OperationJournalView` | 每日操作记录 |
| `/volume_monitor` | `VolumeMonitorView` | 量能异动监控 |
| `/leader_strength` | `LeaderStrengthView` | 龙头强势/主线核心观察 |
| `/factor_config` | `FactorConfigView` | 因子配置 |
| `/score_single` | `ScoreSingleView` | 单股票计算 |
| `/system_health` | `SystemHealthView` | 系统运行监控 |
| `/table/:table` | `TableView` | 通用数据表 |
| `/custom/:table` | `CustomStrategyView` | 自有策略表 |
| `/indicators` | `IndicatorsView` | K 线指标图表 |

### 当前侧边栏分组

```
首页
数据同步管理
我的关注
操作策略
├── 超短主线接力
└── 每日操作记录
自有策略
├── 量能异动监控
├── 龙头强势
├── 爆量股票
├── 因子配置
└── 单股票计算
综合选股
股票基本数据
股票指标数据
股票K线形态
股票策略数据
```

## 操作策略模块

### 超短主线接力

入口：`/app/operation_strategy`

核心 API：`/api/mainline_core`

功能：

- 按日期和分时快照查看主线候选。
- 默认市场池适配创业板、科创板、京市 A 股。
- 支持市场筛选、买入状态筛选、刷新和表头排序。
- 展示主线状态、第一主线、候选主线数、核心标的数。
- 展示市场环境闸门：`接力可做`、`谨慎试错`、`弱市退潮`。
- 展示全 A 上涨率、交易池上涨率、交易池中位涨跌幅、跌超 2% 数量。
- 个股列包含主线、核心分、模式、买入状态、涨幅、回落、实时涨跌幅、量比、风险和记录操作。
- 点击记录可跳转到每日操作记录，并携带代码、名称、价格、主线等上下文。

主要规则：

- `弱市退潮`：默认关闭可买池。
- `谨慎试错`：只看无风险前排核心。
- `09:45` 后进入更严格的风控观察口径。
- `等回踩`、`只观察`、`偏追高` 不等同于可以买。

### 每日操作记录

入口：`/app/operation_journal`

核心 API：`/api/operation_log`

数据库表：`cn_trade_operation_log`

字段：

- `trade_date`：交易日期
- `trade_time`：交易时间
- `code` / `name`：股票代码和名称
- `action`：`buy`、`sell`、`hold`、`switch`
- `price` / `quantity`：价格和数量
- `mainline`：所属主线
- `strategy`：策略来源
- `reason`：操作理由
- `result`：结果
- `follow_plan`：次日计划
- `system_judgment`：系统判断文本
- `signal_strategy` / `signal_snapshot_time`：系统策略和判断快照时间
- `signal_core_score` / `signal_mode`：核心分和模式
- `signal_buy_status`：买入状态
- `signal_amount_ratio` / `signal_risk`：量比和风险

## 量能与主线模块

主要逻辑位于 `instock/web/volumeHandler.py`、`instock/core/volume_rank_engine.py`、`instock/core/minute_bar_collector.py`。

功能：

- 盘中分钟线采集和 Redis 缓存。
- `cn_stock_minute_bar` 分钟线查询和补全。
- 量能异动排行。
- 龙头强势候选。
- 主线核心候选、观察池、风险标签、市场环境闸门。
- 交易主题/板块映射管理。
- 单股票信号详情和因子评分。

关键缓存/表：

- Redis：分钟线缓存、量能排行缓存、主线候选短缓存。
- PostgreSQL：`cn_stock_minute_bar`、`cn_stock_hist_data`、策略结果表、题材映射表。

## 系统健康模块

主要逻辑位于 `instock/web/systemHandler.py`。

入口：

- `/app/system_health`
- `/api/system_health`
- `/api/system_action`
- `/api/system_action_status`

检查内容：

- Web 服务状态
- PostgreSQL 连接
- Redis 连接
- 量能监控 daemon
- 日线数据日期
- 分钟线 Redis 缓存
- 分钟线 PostgreSQL 落库
- 今日分钟线完整性
- 同步任务状态

可触发动作：

- 启动量能监控 daemon
- 补全分钟 K 线
- 补全今日分钟 K 线
- 刷新量能排行
- 同步板块映射
- 同步日线/基础数据
- 准备爆量策略

## 数据表口径

常用核心表：

| 表 | 说明 |
| --- | --- |
| `cn_stock_hist_data` | 股票日线历史数据 |
| `cn_stock_minute_bar` | 股票 1 分钟 K 线 |
| `cn_stock_spot` | 每日股票数据 |
| `cn_etf_spot` | 每日 ETF 数据 |
| `cn_stock_selection` | 综合选股 |
| `cn_stock_indicators` | 技术指标 |
| `cn_stock_indicators_buy` | 指标买入信号 |
| `cn_stock_indicators_sell` | 指标卖出信号 |
| `cn_stock_kline_pattern` | K 线形态 |
| `cn_stock_strategy_*` | 传统策略和自有策略结果 |
| `cn_stock_strategy_volume_surge` | 爆量股票策略 |
| `cn_stock_trade_theme` | 股票题材/主线映射 |
| `cn_trade_operation_log` | 每日操作记录 |

## 数据流

```
外部数据源
  ├─ Tushare / XTick / 新浪 / 同花顺 / 通达信
  ↓
采集与同步任务
  ├─ daily_market_sync.py
  ├─ batch_fetch_hist.py
  ├─ fill_minute_bars.py
  └─ volume_monitor_daemon.py
  ↓
存储层
  ├─ PostgreSQL：日线、分钟线、指标、策略、操作记录
  └─ Redis：盘中分钟缓存、量能排行、短期候选缓存
  ↓
分析层
  ├─ 指标计算
  ├─ K线形态
  ├─ 传统策略
  ├─ 量能异动
  ├─ 龙头强势
  └─ 超短主线接力
  ↓
展示层
  ├─ Vue3 SPA：/app
  └─ 旧版 Tornado 模板：/
```

## 开发与验证

启动后端：

```bash
python3 instock/web/web_service.py
```

启动前端开发服务：

```bash
cd instock/web/frontend
npm run dev
```

构建前端：

```bash
cd instock/web/frontend
npm run build
```

后端语法检查：

```bash
python3 -m compileall -q instock check_strategy.py
```

健康检查：

```bash
curl http://localhost:9988/api/system_health
```

## 工作规则

- 股票、行情、交易日、回测、信号、数据库查询、数据同步、报表生成相关任务开始前，必须先确认当前真实时间。
- 当前项目数据库以 PostgreSQL 为准，不再使用 MySQL/MariaDB。
- Redis 是盘中分钟线、量能监控和主线候选的重要依赖。
- 文档中的页面和 API 以 `instock/web/web_service.py`、`instock/web/frontend/src/router/index.js` 和 `instock/web/frontend/src/App.vue` 为准。
