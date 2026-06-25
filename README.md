# InStock 股票量化投资系统

本仓库是 InStock 股票系统的自用改造版，用于 A 股/ETF 数据采集、技术指标计算、策略选股、回测统计、量能监控和 Web 展示。

## 项目定位

系统主线：

1. 从 Tushare、XTick、 新浪财经、同花顺等数据源采集行情和专题数据。
2. 写入 PostgreSQL，分钟 K 线和量能监控缓存写入 Redis。
3. 计算技术指标、K 线形态、选股策略、回测收益和量能排行。
4. 通过 Tornado Web 服务提供旧版模板页面和 Vue3 SPA 页面。

主要模块：

- `instock/core`：数据抓取、指标、策略、K 线、量能计算等核心逻辑。
- `instock/job`：数据库初始化、每日同步、历史数据、分钟 K 线和量能任务。
- `instock/web`：Tornado 服务、REST API、旧版模板页面。
- `instock/web/frontend`：Vue3 + Vite 前端。
- `docker`：PostgreSQL + Redis + InStock 的容器部署配置。

## 运行依赖

- Python 3.11+
- PostgreSQL 16+
- Redis 7+
- TA-Lib C library
- Node.js 20+（仅前端开发/构建需要）

Python 依赖见 `requirements.txt`。当前数据库层已切到 PostgreSQL，运行时依赖 `psycopg2-binary`，不再使用 MySQL/MariaDB。

## 配置项

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

敏感配置建议放环境变量或 `instock/config/*.txt`，不要提交到 Git。

## Docker 启动

`docker/docker-compose.yml` 已切到 PostgreSQL + Redis。首次运行先构建镜像，`build.sh` 会把项目同步到 `docker/stock` 后执行 Docker build：

```bash
cd docker
sh build.sh
```

启动服务：

```bash
docker compose up -d
```

服务：

- Web：`http://localhost:9988/`
- Vue3 SPA：`http://localhost:9988/app`
- PostgreSQL：服务名 `instockdbservice`，库名 `instockdb`
- Redis：服务名 `instockredis`

首次启动后初始化数据库：

```bash
docker exec -it InStock python /data/InStock/instock/job/init_job.py
```

如需重新构建镜像：

```bash
cd docker
sh build.sh
```

## 本地开发

安装 Python 依赖：

```bash
python3 -m pip install -r requirements.txt
```

启动 PostgreSQL 和 Redis 后，设置环境变量，例如：

```bash
export db_host=localhost
export db_port=5432
export db_user=instock
export db_password=instock
export db_database=instockdb
export REDIS_HOST=localhost
export REDIS_PORT=6379
export TUSHARE_TOKEN=你的Token
```

初始化数据库：

```bash
python3 instock/job/init_job.py
```

启动 Web：

```bash
python3 instock/web/web_service.py
```

访问：

- 旧版首页：`http://localhost:9988/`
- 新版前端：`http://localhost:9988/app`

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

构建产物输出到 `instock/web/static/dist`，Tornado 会通过 `/app` 返回 SPA 入口。

## 常用任务

初始化基础表：

```bash
python3 instock/job/init_job.py
```

执行每日任务：

```bash
python3 instock/job/execute_daily_job.py
```

拉取历史数据：

```bash
python3 instock/job/fetch_historical_data.py
```

初始化量能监控相关表：

```bash
python3 instock/job/init_volume_tables.py
```

启动量能监控 daemon：

```bash
python3 instock/job/volume_monitor_daemon.py
```

补分钟 K 线：

```bash
python3 instock/job/fill_minute_bars.py
```

## 验证

语法检查：

```bash
python3 -m compileall -q instock check_strategy.py
```

前端构建：

```bash
cd instock/web/frontend
npm run build
```

健康检查接口：

```bash
curl http://localhost:9988/api/system_health
```

## 注意事项

- 当前代码和部署配置以 PostgreSQL 为准，不再启动 MariaDB/MySQL。
- Redis 是量能监控、分钟 K 线缓存和部分健康检查的必需服务。
- Tushare/XTick Token 缺失时，相关数据采集任务会失败或返回空数据。
- `instock/config` 下的 token、cookie、交易客户端配置属于敏感信息，提交前请确认 `.gitignore` 生效。
