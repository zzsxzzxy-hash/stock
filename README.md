# InStock 股票分析系统

本仓库是 InStock 的本地实盘辅助分析版本，面向 A 股超短、主线接力、量能异动、每日操作复盘和基础行情管理。系统以 PostgreSQL 存储日线/分钟线/策略结果，以 Redis 承载盘中分钟缓存和量能排行，通过 Tornado 提供 API 与旧版模板页面，通过 Vue3 SPA 提供主要操作界面。

## 当前定位

系统现在主要服务四类工作：

1. 行情与基础数据：同步股票/ETF日线、资金流、龙虎榜、大宗交易、涨停原因、分红配送等数据。
2. 分钟线与量能监控：采集/补全 `cn_stock_minute_bar`，计算盘中量能异动、分时强度、龙头强势和单股票因子。
3. 超短主线接力：按日期和分时快照筛选主线、候选个股、买入状态、风险标签和市场环境闸门。
4. 操作复盘：记录买入/卖出/持有/换股，标记错误操作，对照系统当时的候选逻辑和市场环境。

## 主要功能页面

新版前端入口：`http://localhost:9988/app`

| 页面 | 路径 | 说明 |
| --- | --- | --- |
| 首页 | `/app/` | 系统概览、健康状态、常用任务入口 |
| 数据同步管理 | `/app/sync` | 手动触发同步任务并查看日志 |
| 我的关注 | `/app/watchlist` | 关注股票，跳转 K 线和指标页 |
| 超短主线接力 | `/app/operation_strategy` | 主线候选、市场环境闸门、买入状态筛选、实时涨跌幅、表头排序 |
| 每日操作记录 | `/app/operation_journal` | 记录买卖/持有/换股、系统判断、复盘理由和次日计划 |
| 量能异动监控 | `/app/volume_monitor` | 盘中量能排行、分时量比和信号详情 |
| 龙头强势 | `/app/leader_strength` | 龙头强势与主线核心观察两种口径 |
| 因子配置 | `/app/factor_config` | 调整量能/主线因子阈值 |
| 单股票计算 | `/app/score_single` | 单只股票因子得分与信号明细 |
| 系统运行监控 | `/app/system_health` | PostgreSQL、Redis、分钟线、daemon 和同步任务健康检查 |
| 表格数据 | `/app/table/:table` | 基础数据、指标、形态、策略表通用展示 |
| 自有策略 | `/app/custom/:table` | 自有策略表展示，例如爆量股票 |
| K线指标图表 | `/app/indicators` | 日 K、指标、1 分钟 K 线和新浪分时图 |

旧版模板入口仍保留：

- `http://localhost:9988/`
- `http://localhost:9988/instock/data`
- `http://localhost:9988/instock/data/indicators`

## 技术栈

- Python 3.11+
- Tornado 6+
- PostgreSQL 16+
- Redis 7+
- TA-Lib
- Vue 3 + Vite
- Element Plus
- ECharts / lightweight-charts

Python 依赖见 `requirements.txt`。数据库层已迁移到 PostgreSQL，运行时依赖 `psycopg2-binary` 和 `SQLAlchemy`。

## 配置

常用环境变量：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `db_host` | `localhost` | PostgreSQL 主机 |
| `db_port` | `5432` | PostgreSQL 端口 |
| `db_user` | `x6-mac` | PostgreSQL 用户 |
| `db_password` | 空 | PostgreSQL 密码 |
| `db_database` | `instockdb` | PostgreSQL 数据库 |
| `REDIS_HOST` / `redis_host` | `localhost` | Redis 主机 |
| `REDIS_PORT` / `redis_port` | `6379` | Redis 端口 |
| `REDIS_DB` | `0` | Redis DB |
| `TUSHARE_TOKEN` | 空 | Tushare Token，也可写入 `instock/config/tushare_token.txt` |
| `XTICK_TOKEN` | 空 | XTick Token，也可写入 `instock/config/xtick_token.txt` |

敏感配置建议放在环境变量或 `instock/config/*.txt` 中，不要提交到 Git。

## 本地运行

安装依赖：

```bash
python3 -m pip install -r requirements.txt
```

初始化数据库：

```bash
python3 instock/job/init_job.py
```

启动 Web 服务：

```bash
python3 instock/web/web_service.py
```

访问：

- Web 服务：`http://localhost:9988/`
- Vue3 前端：`http://localhost:9988/app`

## 前端开发

```bash
cd instock/web/frontend
npm install
npm run dev
```

开发服务默认端口为 `5173`，会把 `/api` 和 `/instock` 代理到 `http://localhost:9988`。

构建生产前端：

```bash
cd instock/web/frontend
npm run build
```

构建产物输出到 `instock/web/static/dist`，Tornado 通过 `/app/*` 返回 SPA 入口。

## 常用任务

初始化基础表：

```bash
python3 instock/job/init_job.py
```

执行每日任务：

```bash
python3 instock/job/execute_daily_job.py
```

同步日线/基础行情：

```bash
python3 instock/job/daily_market_sync.py
```

补全分钟 K 线：

```bash
python3 instock/job/fill_minute_bars.py
```

修复分钟线与日线不一致：

```bash
python3 instock/job/repair_minute_bars_by_daily.py
```

启动量能监控 daemon：

```bash
python3 instock/job/volume_monitor_daemon.py
```

重建题材/主线映射：

```bash
python3 instock/job/rebuild_trade_theme.py
python3 instock/job/sync_sector_map.py
```

回放早盘信号：

```bash
python3 instock/job/morning_signal_replay.py
```

校准主线核心参数：

```bash
python3 instock/job/calibrate_mainline_core.py
```

## 关键 API

| API | 说明 |
| --- | --- |
| `/api/meta` | 表格列定义 |
| `/api/data` | 通用表格数据 |
| `/api/trade_date` | 最近交易日 |
| `/api/watchlist` | 关注列表增删查 |
| `/api/operation_log` | 每日操作记录增删改查 |
| `/api/custom_strategy` | 自有策略表 |
| `/instock/api_data/kline` | 日/周/月 K 线 |
| `/api/minute_kline` | 1 分钟 K 线 |
| `/api/minute_bars` | 分钟线序列 |
| `/api/volume_rank` | 量能异动排行 |
| `/api/volume_detail` | 个股量能详情 |
| `/api/leader_strength` | 龙头强势候选 |
| `/api/mainline_core` | 超短主线核心候选和市场环境 |
| `/api/stock_signal_detail` | 单股信号详情 |
| `/api/factor_config` | 因子配置 |
| `/api/score_single` | 单股票得分 |
| `/api/system_health` | 系统健康检查 |
| `/api/system_action` | 触发系统任务 |
| `/api/system_action_status` | 查询任务状态 |

## 操作策略口径

“超短主线接力”页面默认更贴近当前操盘需求：

- 默认市场池：创业板、科创板、京市 A 股。
- 买入状态：`可追强`、`重点观察`、`等回踩`、`只观察`、`偏追高`。
- 市场环境闸门：`接力可做`、`谨慎试错`、`弱市退潮`。
- `弱市退潮` 会关闭可买池；`谨慎试错` 只允许前排核心小仓试错。
- `09:45` 后的急拉信号会进入更严格的风控口径。
- 表格支持实时涨跌幅、风险标签、量比、主线和各列排序。

“每日操作记录”用于沉淀真实交易动作，字段包括日期、时间、代码、动作、价格、数量、主线、结果、盈亏、系统判断、操作理由和次日计划。盈亏由接口按股票和交易时间做 FIFO 买卖配对，并同时显示在配对的买入行和卖出行。系统判断由当前主线逻辑自动识别，包含核心分、模式、买入状态、量比和风险；结构化字段同时保存在 `cn_trade_operation_log`，便于按策略状态统计表现。

## Docker

Docker 配置位于 `docker/`，当前编排包含 PostgreSQL、Redis 和 InStock 服务。

构建镜像：

```bash
cd docker
sh build.sh
```

启动：

```bash
docker compose up -d
```

首次启动后初始化数据库：

```bash
docker exec -it InStock python /data/InStock/instock/job/init_job.py
```

## 验证

后端语法检查：

```bash
python3 -m compileall -q instock check_strategy.py
```

前端构建：

```bash
cd instock/web/frontend
npm run build
```

健康检查：

```bash
curl http://localhost:9988/api/system_health
```

## 注意事项

- 执行股票、行情、交易日、回测、信号、数据库查询、数据同步、报表生成相关任务前，先用系统命令确认真实时间。
- Redis 是盘中分钟线缓存、量能排行和部分主线候选计算的重要依赖。
- `cn_stock_minute_bar` 是超短主线、分时回放、实时涨跌幅和操作复盘的关键表。
- `cn_trade_operation_log` 是每日操作记录表，支持买入、卖出、持有复审和换股。
- Tushare/XTick Token 缺失时，相关采集任务会失败或返回空数据。
- `instock/config` 下的 token、cookie、交易客户端配置属于敏感信息，提交前确认 `.gitignore` 生效。
