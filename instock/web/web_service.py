#!/usr/local/bin/python3
# -*- coding: utf-8 -*-

import logging
import os.path
import sys
from abc import ABC

import tornado.escape
import tornado.httpserver
import tornado.ioloop
import tornado.options
from tornado import gen

# 在项目运行时，临时将项目路径添加到环境变量
cpath_current = os.path.dirname(os.path.dirname(__file__))
cpath = os.path.abspath(os.path.join(cpath_current, os.pardir))
sys.path.append(cpath)
log_path = os.path.join(cpath_current, 'log')
if not os.path.exists(log_path):
    os.makedirs(log_path)
logging.basicConfig(format='%(asctime)s %(message)s', filename=os.path.join(log_path, 'stock_web.log'))
logging.getLogger().setLevel(logging.ERROR)
import instock.lib.torndb as torndb
import instock.lib.database as mdb
import instock.lib.version as version
import instock.web.dataTableHandler as dataTableHandler
import instock.web.dataIndicatorsHandler as dataIndicatorsHandler
import instock.web.syncHandler as syncHandler
import instock.web.apiHandler as apiHandler
import instock.web.base as webBase

__author__ = 'myh '
__date__ = '2023/3/10 '


class Application(tornado.web.Application):
    def __init__(self):
        handlers = [
            # ── Vue3 SPA 入口（/app/* 全部返回 index.html）──────────────────
            (r"/app(?:/.*)?", VueSPAHandler),

            # ── 旧版 Tornado 模板路由（保持兼容）──────────────────────────────
            (r"/", HomeHandler),
            (r"/instock/", HomeHandler),
            (r"/instock/api_data", dataTableHandler.GetStockDataHandler),
            (r"/instock/data", dataTableHandler.GetStockHtmlHandler),
            (r"/instock/data/indicators", dataIndicatorsHandler.GetDataIndicatorsHandler),
            (r"/instock/control/attention", dataIndicatorsHandler.SaveCollectHandler),
            (r"/instock/sync", syncHandler.SyncPageHandler),
            (r"/instock/api/sync", syncHandler.SyncApiHandler),
            (r"/instock/api/sync/status", syncHandler.SyncStatusApiHandler),
            (r"/instock/api/sync/log", syncHandler.SyncLogSSEHandler),

            # ── Vue3 REST API ──────────────────────────────────────────────
            (r"/api/meta",        apiHandler.ApiMetaHandler),
            (r"/api/data",        apiHandler.ApiDataHandler),
            (r"/api/trade_date",  apiHandler.ApiTradeDateHandler),
            (r"/api/watchlist",   apiHandler.ApiWatchlistHandler),
            (r"/api/custom_strategy", apiHandler.ApiCustomStrategyHandler),
            (r"/instock/api_data/kline", apiHandler.ApiKlineHandler),
        ]
        settings = dict(  # 配置
            template_path=os.path.join(os.path.dirname(__file__), "templates"),
            static_path=os.path.join(os.path.dirname(__file__), "static"),
            xsrf_cookies=False,  # True,
            # cookie加密
            cookie_secret="027bb1b670eddf0392cdda8709268a17b58b7",
            debug=True,
        )
        super(Application, self).__init__(handlers, **settings)
        # Have one global connection to the blog DB across all handlers
        self.db = torndb.Connection(**mdb.MYSQL_CONN_TORNDB)


# Vue3 SPA Handler：所有 /app/* 请求返回 Vue 构建产物的 index.html
class VueSPAHandler(webBase.BaseHandler, ABC):
    def get(self):
        dist_index = os.path.join(os.path.dirname(__file__), "static", "dist", "index.html")
        if os.path.exists(dist_index):
            with open(dist_index, 'r', encoding='utf-8') as f:
                content = f.read()
            self.set_header('Content-Type', 'text/html; charset=utf-8')
            self.write(content)
        else:
            self.set_status(404)
            self.write("Vue3 前端尚未构建，请先执行 npm run build")


# 首页handler。
class HomeHandler(webBase.BaseHandler, ABC):
    @gen.coroutine
    def get(self):
        self.render("index.html",
                    stockVersion=version.__version__,
                    leftMenu=webBase.GetLeftMenu(self.request.uri))


def main():
    # tornado.options.parse_command_line()
    tornado.options.options.logging = None

    http_server = tornado.httpserver.HTTPServer(Application())
    port = 9988
    http_server.listen(port)

    print(f"服务已启动，web地址 : http://localhost:{port}/")
    logging.error(f"服务已启动，web地址 : http://localhost:{port}/")

    tornado.ioloop.IOLoop.current().start()


if __name__ == "__main__":
    main()
