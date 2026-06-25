#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
系统健康监控 API
GET  /api/system_health   → 各项服务/数据健康状态
POST /api/system_action   → 执行重启/同步操作
"""
import json
import logging
import os
import subprocess
import sys
import datetime
import psutil

import tornado.web

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

log = logging.getLogger(__name__)

# ─── 工具函数 ─────────────────────────────────────────────────────────────────

def _now_beijing():
    try:
        from zoneinfo import ZoneInfo
        return datetime.datetime.now(ZoneInfo('Asia/Shanghai'))
    except Exception:
        return datetime.datetime.now()


def _today() -> str:
    return _now_beijing().strftime('%Y-%m-%d')


def _hhmm() -> str:
    return _now_beijing().strftime('%H:%M')


def _is_trade_day() -> bool:
    """工作日判断（简单版：排除周末）"""
    return _now_beijing().weekday() < 5


# ─── 各项检查 ──────────────────────────────────────────────────────────────────

def _check_process(keywords: list) -> dict:
    """检查匹配关键词的进程是否在运行"""
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            cmdline = ' '.join(proc.info['cmdline'] or [])
            if all(k in cmdline for k in keywords):
                return {'ok': True, 'pid': proc.info['pid'], 'detail': cmdline[:80]}
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return {'ok': False, 'pid': None, 'detail': '进程未找到'}


def _check_web_service() -> dict:
    """检查 web_service.py 是否在监听 9988"""
    res = _check_process(['web_service'])
    if res['ok']:
        # 额外确认端口
        import socket
        try:
            s = socket.create_connection(('127.0.0.1', 9988), timeout=1)
            s.close()
            res['detail'] = f"pid={res['pid']} port=9988 OK"
        except Exception:
            res['ok'] = False
            res['detail'] = f"进程存在(pid={res['pid']})但端口9988无响应"
    return res


def _check_volume_daemon() -> dict:
    return _check_process(['volume_monitor_daemon'])


def _check_redis() -> dict:
    try:
        import redis as redis_lib
        r = redis_lib.Redis(
            host=os.environ.get('redis_host', 'localhost'),
            port=int(os.environ.get('redis_port', 6379)),
            socket_connect_timeout=1,
        )
        info = r.info('server')
        ver = info.get('redis_version', '?')
        return {'ok': True, 'detail': f"Redis {ver} 运行中"}
    except Exception as e:
        return {'ok': False, 'detail': str(e)}


def _check_postgres() -> dict:
    try:
        import instock.lib.database as mdb
        result = mdb.executeSqlFetch('SELECT version()')
        if result:
            ver = result[0][0].split(',')[0]  # e.g. "PostgreSQL 15.x ..."
            return {'ok': True, 'detail': ver}
        return {'ok': False, 'detail': '查询无返回'}
    except Exception as e:
        return {'ok': False, 'detail': str(e)}


def _check_pre_calc(today: str) -> dict:
    """检查预计算缓存是否存在"""
    try:
        from instock.core.minute_bar_collector import get_redis
        r = get_redis()
        key = f'pre_calc:{today}:ma120'
        exists = r.exists(key)
        ttl = r.ttl(key)
        if exists:
            raw = r.get(key)
            count = len(json.loads(raw)) if raw else 0
            return {'ok': True, 'detail': f"{count} 只股票，TTL={ttl}s"}
        else:
            # 未到09:25 或 尚未执行
            hhmm = _hhmm()
            if hhmm < '09:25':
                return {'ok': True, 'detail': f"未到09:25，尚未预计算（当前{hhmm}）", 'warn': True}
            return {'ok': False, 'detail': f"预计算缓存不存在（当前{hhmm}，应已执行）"}
    except Exception as e:
        return {'ok': False, 'detail': str(e)}


def _check_minute_bars(today: str) -> dict:
    """检查今日分钟K线采集进度"""
    try:
        from instock.core.minute_bar_collector import get_redis, get_all_codes_for_date
        from instock.lib.trade_hours import current_expected_minutes, is_collect_window
        import datetime as _dt
        r = get_redis()
        codes = get_all_codes_for_date(today)
        code_count = len(codes)

        if code_count == 0:
            hhmm = _hhmm()
            now_t = _dt.datetime.strptime(hhmm, '%H:%M').time()
            if not is_collect_window(now_t):
                return {'ok': True, 'detail': f"非采集窗口（{hhmm}），暂无数据", 'warn': True}
            return {'ok': False, 'detail': f"采集窗口内无数据（{hhmm}），采集可能已停止"}

        # 抽查第一只股票的bar数量
        sample = list(codes)[0]
        raw = r.get(f'minute_bar:{today}:{sample}')
        bars = json.loads(raw) if raw else []
        bar_count = len(bars)
        last_time = bars[-1]['time'] if bars else 'N/A'

        expected = current_expected_minutes()
        expected_count = len(expected)
        progress = f"{bar_count}/{expected_count}" if expected_count else f"{bar_count}"

        ok = code_count >= 100  # 至少100只才算正常
        return {
            'ok': ok,
            'detail': f"{code_count} 只股票已采集，样本({sample})最新={last_time}，进度{progress}",
        }
    except Exception as e:
        return {'ok': False, 'detail': str(e)}


def _check_volume_rank(today: str) -> dict:
    """检查量能排行缓存是否在正常刷新"""
    try:
        from instock.core.minute_bar_collector import get_redis
        import datetime as _dt
        r = get_redis()

        # 扫描所有 volume_rank:today:* 键
        pattern = f'volume_rank:{today}:*'
        cursor = 0
        keys = []
        while True:
            cursor, batch = r.scan(cursor, match=pattern, count=200)
            keys.extend(batch)
            if cursor == 0:
                break

        hhmm = _hhmm()
        now_t = _dt.datetime.strptime(hhmm, '%H:%M').time()
        trade_start = _dt.time(9, 31)
        trade_end   = _dt.time(15, 0)
        in_trade = trade_start <= now_t <= trade_end

        if not keys:
            if not in_trade:
                return {'ok': True, 'detail': f"非交易时段（{hhmm}），无排行缓存", 'warn': True}
            return {'ok': False, 'detail': f"交易时段内无排行缓存（{hhmm}）"}

        # 取最小TTL（代表最近一次刷新）
        min_ttl = min(r.ttl(k) for k in keys)
        count_key = len(keys)
        ok = (min_ttl > 0 and min_ttl <= 60) or not in_trade
        return {
            'ok': ok,
            'detail': f"{count_key} 个缓存键，最小TTL={min_ttl}s（正常≤35s）",
        }
    except Exception as e:
        return {'ok': False, 'detail': str(e)}


def _check_stock_spot(today: str) -> dict:
    """检查今日股票行情数据是否已写入PG"""
    try:
        import instock.lib.database as mdb
        count = mdb.executeSqlCount(
            "SELECT COUNT(*) FROM cn_stock_spot WHERE date = %s",
            (today,)
        )
        hhmm = _hhmm()
        ok = count >= 100
        warn = count == 0 and hhmm < '09:30'
        return {
            'ok': ok or warn,
            'warn': warn and count == 0,
            'detail': f"{count} 条记录（今日 {today}）",
        }
    except Exception as e:
        return {'ok': False, 'detail': str(e)}


def _check_hist_data(today: str) -> dict:
    """检查今日日K线数据（cn_stock_hist_data）是否已同步"""
    try:
        import instock.lib.database as mdb
        count = mdb.executeSqlCount(
            'SELECT COUNT(*) FROM cn_stock_hist_data WHERE date = %s',
            (today,)
        )
        hhmm = _hhmm()
        # 收盘后（15:30+）才要求有数据；盘中及盘前给警告
        after_close = hhmm >= '15:30'
        ok   = count >= 100
        warn = not ok and not after_close
        return {
            'ok':   ok or warn,
            'warn': warn,
            'detail': f"{count} 条记录（今日 {today}）{'，收盘后需同步' if after_close and not ok else ''}",
        }
    except Exception as e:
        return {'ok': False, 'detail': str(e)}


def _check_volume_surge(today: str) -> dict:
    """检查今日爆量股票策略是否已计算"""
    try:
        import instock.lib.database as mdb
        count = mdb.executeSqlCount(
            'SELECT COUNT(*) FROM cn_stock_strategy_volume_surge WHERE date = %s',
            (today,)
        )
        hhmm = _hhmm()
        after_close = hhmm >= '15:30'
        ok   = count >= 1
        warn = not ok and not after_close
        return {
            'ok':   ok or warn,
            'warn': warn,
            'detail': f"{count} 只爆量股票（今日 {today}）{'，收盘后需同步' if after_close and not ok else ''}",
        }
    except Exception as e:
        return {'ok': False, 'detail': str(e)}


def _check_minute_bar_pg(today: str) -> dict:
    """检查PG中今日分钟K线记录数"""
    try:
        import instock.lib.database as mdb
        count = mdb.executeSqlCount(
            "SELECT COUNT(*) FROM cn_stock_minute_bar WHERE date = %s",
            (today,)
        )
        return {
            'ok': True,
            'detail': f"{count:,} 条（今日），采集进度参考",
        }
    except Exception as e:
        return {'ok': False, 'detail': str(e)}


def _check_daemon_log() -> dict:
    """检查 daemon 日志最后更新时间"""
    log_file = '/tmp/volume_monitor.log'
    try:
        if not os.path.exists(log_file):
            return {'ok': False, 'detail': '日志文件不存在（daemon未运行过）'}
        mtime = os.path.getmtime(log_file)
        dt = datetime.datetime.fromtimestamp(mtime)
        age_sec = (datetime.datetime.now() - dt).total_seconds()
        # 读最后3行
        with open(log_file, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.readlines()
        last_lines = ''.join(lines[-3:]).strip().replace('\n', ' | ')

        # 只在交易时段（09:15~15:05）内要求活跃
        hhmm = _hhmm()
        in_active = '09:15' <= hhmm <= '15:05'
        ok = (age_sec < 120) if in_active else True
        warn = in_active and 120 <= age_sec < 300

        detail = f"最后更新 {dt.strftime('%H:%M:%S')} ({int(age_sec)}秒前)"
        if not in_active:
            detail += "（非交易时段，正常）"
        detail += f"：{last_lines[:80]}"
        return {'ok': ok, 'warn': warn, 'detail': detail}
    except Exception as e:
        return {'ok': False, 'detail': str(e)}


def _check_sectors() -> dict:
    """检查板块映射缓存"""
    try:
        from instock.core.minute_bar_collector import get_redis
        r = get_redis()
        exists = r.exists('pre_calc:sectors')
        if exists:
            raw = r.get('pre_calc:sectors')
            count = len(json.loads(raw)) if raw else 0
            return {'ok': True, 'detail': f"{count} 只股票有板块映射"}
        return {'ok': False, 'detail': '板块映射缓存不存在（需手动触发预计算）'}
    except Exception as e:
        return {'ok': False, 'detail': str(e)}


# ─── 构建完整健康报告 ──────────────────────────────────────────────────────────

def build_health_report() -> dict:
    today = _today()
    hhmm  = _hhmm()
    trade_day = _is_trade_day()

    checks = []

    # ── 1. 基础服务 ──────────────────────────────────────────────────────────
    checks.append({
        'category': '基础服务',
        'name':     'Web 服务 (port 9988)',
        'required': True,
        **_check_web_service(),
    })
    checks.append({
        'category': '基础服务',
        'name':     'Redis',
        'required': True,
        **_check_redis(),
    })
    checks.append({
        'category': '基础服务',
        'name':     'PostgreSQL',
        'required': True,
        **_check_postgres(),
    })

    # ── 2. 量能监控服务 ───────────────────────────────────────────────────────
    daemon_res = _check_volume_daemon()
    checks.append({
        'category': '量能监控',
        'name':     '量能采集 Daemon',
        'required': True,
        'action':   'restart_daemon',
        **daemon_res,
    })
    checks.append({
        'category': '量能监控',
        'name':     '预计算缓存 (MA120/位置)',
        'required': trade_day,
        'action':   'run_pre_calc',
        **_check_pre_calc(today),
    })
    checks.append({
        'category': '量能监控',
        'name':     '分钟K线采集 (Redis)',
        'required': trade_day,
        'action':   'fill_minute_bars',
        **_check_minute_bars(today),
    })
    checks.append({
        'category': '量能监控',
        'name':     '量能排行缓存 (35s刷新)',
        'required': trade_day,
        'action':   'refresh_rank',
        **_check_volume_rank(today),
    })
    checks.append({
        'category': '量能监控',
        'name':     '板块映射缓存',
        'required': True,
        'action':   'reload_sectors',
        **_check_sectors(),
    })
    checks.append({
        'category': '量能监控',
        'name':     'Daemon 日志活跃度',
        'required': False,
        **_check_daemon_log(),
    })

    # ── 3. 每日数据 ───────────────────────────────────────────────────────────
    checks.append({
        'category': '每日数据',
        'name':     '今日股票行情 (cn_stock_spot)',
        'required': trade_day,
        'action':   'sync_stock_spot',
        **_check_stock_spot(today),
    })
    checks.append({
        'category': '每日数据',
        'name':     '每日股票基本数据 (cn_stock_hist_data)',
        'required': trade_day,
        'action':   'sync_hist_data',
        **_check_hist_data(today),
    })
    checks.append({
        'category': '每日数据',
        'name':     '爆量股票数据 (volume_surge)',
        'required': trade_day,
        'action':   'sync_volume_surge',
        **_check_volume_surge(today),
    })
    checks.append({
        'category': '每日数据',
        'name':     '分钟K线持久化 (PG)',
        'required': False,
        **_check_minute_bar_pg(today),
    })

    # ── 汇总 ──────────────────────────────────────────────────────────────────
    required_checks = [c for c in checks if c.get('required')]
    all_ok = all(c['ok'] for c in required_checks)
    warn_count = sum(1 for c in checks if c.get('warn') and not c['ok'])
    error_count = sum(1 for c in required_checks if not c['ok'] and not c.get('warn'))

    return {
        'ok':          all_ok,
        'error_count': error_count,
        'warn_count':  warn_count,
        'today':       today,
        'hhmm':        hhmm,
        'is_trade_day': trade_day,
        'checks':      checks,
    }


# ─── Handler ──────────────────────────────────────────────────────────────────

class ApiSystemHealthHandler(tornado.web.RequestHandler):
    def set_default_headers(self):
        self.set_header('Access-Control-Allow-Origin', '*')
        self.set_header('Content-Type', 'application/json; charset=utf-8')

    def get(self):
        try:
            report = build_health_report()
            self.write(json.dumps(report, ensure_ascii=False))
        except Exception as e:
            log.error(f"system_health error: {e}", exc_info=True)
            self.set_status(500)
            self.write(json.dumps({'ok': False, 'error': str(e)}, ensure_ascii=False))


class ApiSystemActionHandler(tornado.web.RequestHandler):
    def set_default_headers(self):
        self.set_header('Access-Control-Allow-Origin', '*')
        self.set_header('Content-Type', 'application/json; charset=utf-8')
        self.set_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
        self.set_header('Access-Control-Allow-Headers', 'Content-Type')

    def options(self):
        self.set_status(204)
        self.finish()

    def post(self):
        try:
            body = json.loads(self.request.body or '{}')
            action = body.get('action', '')
            result = _execute_action(action)
            self.write(json.dumps(result, ensure_ascii=False))
        except Exception as e:
            log.error(f"system_action error: {e}", exc_info=True)
            self.set_status(500)
            self.write(json.dumps({'ok': False, 'msg': str(e)}, ensure_ascii=False))


def _execute_action(action: str) -> dict:
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))

    if action == 'restart_daemon':
        # 先 kill 旧进程
        for proc in psutil.process_iter(['pid', 'cmdline']):
            try:
                cmd = ' '.join(proc.info['cmdline'] or [])
                if 'volume_monitor_daemon' in cmd:
                    proc.kill()
            except Exception:
                pass
        # 启动新进程
        log_file = '/tmp/volume_monitor.log'
        with open(log_file, 'a') as f:
            p = subprocess.Popen(
                [sys.executable, '-m', 'instock.job.volume_monitor_daemon'],
                cwd=project_root,
                stdout=f, stderr=f,
                start_new_session=True,
            )
        return {'ok': True, 'msg': f"Daemon 已重启，PID={p.pid}"}

    elif action == 'run_pre_calc':
        today = _today()
        try:
            from instock.core.volume_pre_calc import run_pre_calc
            import threading
            threading.Thread(target=run_pre_calc, args=(today,), daemon=True).start()
            return {'ok': True, 'msg': f"预计算已触发（{today}），稍后刷新查看结果"}
        except Exception as e:
            return {'ok': False, 'msg': str(e)}

    elif action == 'fill_minute_bars':
        try:
            import threading
            def _fill():
                from instock.job.fill_minute_bars import fill_both
                fill_both()
            threading.Thread(target=_fill, daemon=True).start()
            return {'ok': True, 'msg': '分钟K线补全已触发，稍后刷新查看结果'}
        except Exception as e:
            return {'ok': False, 'msg': str(e)}

    elif action == 'refresh_rank':
        today = _today()
        hhmm  = _hhmm()
        try:
            import threading
            def _refresh():
                from instock.core.volume_rank_engine import refresh_rank_cache
                for flt in ('all', 'low', 'break'):
                    refresh_rank_cache(today, hhmm, flt)
            threading.Thread(target=_refresh, daemon=True).start()
            return {'ok': True, 'msg': '排行缓存已触发刷新'}
        except Exception as e:
            return {'ok': False, 'msg': str(e)}

    elif action == 'reload_sectors':
        try:
            import threading
            def _sync_sectors():
                from instock.job.sync_sector_map import run as sector_run
                sector_run()
            threading.Thread(target=_sync_sectors, daemon=True).start()
            return {'ok': True, 'msg': '板块映射同步已触发（约需1-2分钟），完成后自动刷新Redis缓存'}
        except Exception as e:
            return {'ok': False, 'msg': str(e)}

    elif action == 'sync_stock_spot':
        try:
            import threading
            def _sync():
                from instock.job.basic_data_daily_job import main as spot_main
                spot_main()
            threading.Thread(target=_sync, daemon=True).start()
            return {'ok': True, 'msg': '股票行情同步已触发（后台运行，约需1-2分钟）'}
        except Exception as e:
            return {'ok': False, 'msg': str(e)}

    elif action == 'sync_hist_data':
        today = _today()
        try:
            import threading
            def _sync_hist():
                from instock.job.batch_fetch_hist import run as hist_run
                hist_run(today, today)
            threading.Thread(target=_sync_hist, daemon=True).start()
            return {'ok': True, 'msg': f'每日股票基本数据同步已触发（{today}），约需1-2分钟'}
        except Exception as e:
            return {'ok': False, 'msg': str(e)}

    elif action == 'sync_volume_surge':
        today = _today()
        try:
            import threading
            def _sync_surge():
                import datetime as _dt
                from instock.job.custom_strategy_daily_job import prepare_volume_surge
                prepare_volume_surge(_dt.date.fromisoformat(today))
            threading.Thread(target=_sync_surge, daemon=True).start()
            return {'ok': True, 'msg': f'爆量股票策略计算已触发（{today}），稍后刷新查看结果'}
        except Exception as e:
            return {'ok': False, 'msg': str(e)}

    else:
        return {'ok': False, 'msg': f"未知操作: {action}"}
