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
import threading
import psutil

import tornado.web

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

log = logging.getLogger(__name__)
_minute_fill_running = False
_minute_fill_lock = threading.Lock()
_minute_fill_status = {
    'action': 'fill_today_minute_bars',
    'running': False,
    'ok': None,
    'stage': 'idle',
    'progress': 0,
    'message': '尚未执行',
    'error': '',
    'started_at': '',
    'finished_at': '',
    'updated_at': '',
    'seq': 0,
    'logs': [],
}
_MINUTE_FILL_MAX_LOGS = 200

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


def _status_time() -> str:
    return _now_beijing().strftime('%H:%M:%S')


def _minute_fill_snapshot() -> dict:
    with _minute_fill_lock:
        snap = dict(_minute_fill_status)
        snap['logs'] = list(_minute_fill_status.get('logs') or [])
    return snap


def _minute_fill_append(message: str, level: str = 'info',
                        stage: str | None = None,
                        progress: int | float | None = None,
                        stats: dict | None = None):
    now = _status_time()
    with _minute_fill_lock:
        _minute_fill_status['seq'] = int(_minute_fill_status.get('seq') or 0) + 1
        if stage:
            _minute_fill_status['stage'] = stage
        if progress is not None:
            _minute_fill_status['progress'] = max(0, min(100, int(progress)))
        if stats is not None:
            _minute_fill_status['stats'] = stats
        _minute_fill_status['message'] = message
        _minute_fill_status['updated_at'] = now
        logs = _minute_fill_status.setdefault('logs', [])
        logs.append({
            'seq': _minute_fill_status['seq'],
            'time': now,
            'level': level,
            'stage': _minute_fill_status.get('stage') or '',
            'progress': _minute_fill_status.get('progress') or 0,
            'message': message,
            'stats': stats or {},
        })
        if len(logs) > _MINUTE_FILL_MAX_LOGS:
            del logs[:-_MINUTE_FILL_MAX_LOGS]


def _minute_fill_start():
    now = _status_time()
    with _minute_fill_lock:
        _minute_fill_status.update({
            'running': True,
            'ok': None,
            'stage': 'start',
            'progress': 0,
            'message': '今日分钟K线补全任务启动',
            'error': '',
            'started_at': now,
            'finished_at': '',
            'updated_at': now,
            'seq': 0,
            'logs': [],
            'stats': {},
        })
    _minute_fill_append('今日分钟K线补全任务启动', stage='start', progress=0)


def _minute_fill_finish(ok: bool, message: str, error: str = '', stage: str | None = None):
    now = _status_time()
    final_stage = stage or ('done' if ok else 'error')
    with _minute_fill_lock:
        _minute_fill_status.update({
            'running': False,
            'ok': ok,
            'stage': final_stage,
            'progress': 100,
            'message': message,
            'error': error,
            'finished_at': now,
            'updated_at': now,
        })
    _minute_fill_append(
        message,
        level='info' if ok else ('warning' if final_stage == 'incomplete' else 'error'),
        stage=final_stage,
        progress=100,
    )


def _minute_fill_progress(event: dict):
    message = str(event.get('message') or '')
    if not message:
        return
    _minute_fill_append(
        message,
        level=str(event.get('level') or 'info'),
        stage=event.get('stage'),
        progress=event.get('progress'),
        stats=event.get('stats') if isinstance(event.get('stats'), dict) else None,
    )


def _is_trade_day() -> bool:
    """工作日判断（简单版：排除周末）"""
    return _now_beijing().weekday() < 5


def _expected_hist_date(today: str) -> tuple[str, str]:
    """返回 cn_stock_hist_data 当前应该具备的日期及描述。"""
    hhmm = _hhmm()
    if hhmm >= '15:30':
        return today, '今日'

    import instock.lib.database as mdb
    rows = mdb.executeSqlFetch(
        'SELECT MAX(date) FROM cn_stock_minute_bar WHERE date < %s',
        (today,)
    )
    expected_date = str(rows[0][0]) if rows and rows[0][0] else today
    return expected_date, '前一交易日'


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
        latest_rows = mdb.executeSqlFetch('SELECT MAX(date) FROM cn_stock_spot')
        latest = str(latest_rows[0][0]) if latest_rows and latest_rows[0][0] else '无'
        hhmm = _hhmm()
        ok = count >= 100
        warn = count == 0 and hhmm < '09:30'
        return {
            'ok': ok or warn,
            'warn': warn and count == 0,
            'detail': f"今日 {today}：{count} 条；行情最新 {latest}",
        }
    except Exception as e:
        return {'ok': False, 'detail': str(e)}


def _check_hist_data(today: str) -> dict:
    """检查日K线数据是否满足盘中计算需要。"""
    try:
        import instock.lib.database as mdb
        expected_date, expected_desc = _expected_hist_date(today)
        count = mdb.executeSqlCount(
            'SELECT COUNT(*) FROM cn_stock_hist_data WHERE date = %s',
            (expected_date,)
        )
        latest_rows = mdb.executeSqlFetch('SELECT MAX(date) FROM cn_stock_hist_data')
        latest = str(latest_rows[0][0]) if latest_rows and latest_rows[0][0] else '无'
        ok = count >= 100
        return {
            'ok': ok,
            'warn': False,
            'detail': (
                f"{expected_desc} {expected_date}：{count} 条；"
                f"日线最新 {latest}"
                f"{'，需同步后再做盘中计算' if not ok else ''}"
            ),
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


def _minute_index(value: str) -> int | None:
    try:
        hh, mm = str(value or '')[:5].split(':')
        return int(hh) * 60 + int(mm)
    except Exception:
        return None


def _stable_expected_minute(expected: list[str], lag_minutes: int = 2) -> str:
    if not expected:
        return ''
    if len(expected) <= lag_minutes:
        return expected[-1]
    return expected[-1 - lag_minutes]


def _lag_text(latest: str, expected: str) -> str:
    latest_idx = _minute_index(latest)
    expected_idx = _minute_index(expected)
    if latest_idx is None or expected_idx is None:
        return '-'
    lag = max(0, expected_idx - latest_idx)
    return f'{lag}分钟' if lag else '0分钟'


def _redis_minute_stats(today: str, expected: list[str]) -> dict:
    from instock.core.minute_bar_collector import get_redis

    r = get_redis()
    pattern = f'minute_bar:{today}:*'
    cursor = 0
    keys = []
    while True:
        cursor, batch = r.scan(cursor, match=pattern, count=1000)
        keys.extend(batch)
        if cursor == 0:
            break

    code_count = len(keys)
    time_counts: dict[str, int] = {}
    latest_by_code: dict[str, int] = {}
    total_bars = 0
    batch_size = 500
    for i in range(0, len(keys), batch_size):
        batch = keys[i:i + batch_size]
        for key, raw in zip(batch, r.mget(batch)):
            if not raw:
                continue
            try:
                bars = json.loads(raw)
            except Exception:
                bars = []
            if not bars:
                continue
            code = str(key).split(':')[-1]
            latest = ''
            for bar in bars:
                t = str(bar.get('time') or '')[:5]
                if not t:
                    continue
                time_counts[t] = time_counts.get(t, 0) + 1
                total_bars += 1
                if latest == '' or t > latest:
                    latest = t
            if latest:
                latest_by_code[latest] = latest_by_code.get(latest, 0) + 1

    latest_time = max(latest_by_code.keys()) if latest_by_code else ''
    latest_count = latest_by_code.get(latest_time, 0) if latest_time else 0
    max_count = max(time_counts.values()) if time_counts else 0
    min_expected_count = min((time_counts.get(t, 0) for t in expected), default=0)
    return {
        'key_count': code_count,
        'bar_count': total_bars,
        'latest_time': latest_time,
        'latest_count': latest_count,
        'latest_distribution': [
            {'time': t, 'count': c}
            for t, c in sorted(latest_by_code.items(), reverse=True)[:5]
        ],
        'max_count': max_count,
        'min_expected_count': min_expected_count,
        'time_counts': time_counts,
    }


def _check_minute_integrity(today: str, include_running: bool = True) -> dict:
    """按分钟检查今日 PG/Redis 分钟K线是否存在明显缺口。"""
    try:
        fill_status = _minute_fill_snapshot() if include_running else {}
        if include_running and fill_status.get('running'):
            progress = int(fill_status.get('progress') or 0)
            return {
                'ok': True,
                'warn': True,
                'running': True,
                'progress': progress,
                'detail': f"补全运行中 {progress}%：{fill_status.get('message') or '-'}",
                'minute_diag': {
                    'status_text': '补全任务运行中',
                    'fill_task': fill_status,
                },
            }

        import instock.lib.database as mdb
        from instock.lib.trade_hours import expected_minutes_until

        hhmm = _hhmm()
        if not _is_trade_day():
            return {'ok': True, 'warn': True, 'detail': f"非交易日（{today}），跳过完整性检查"}
        if hhmm < '09:32':
            return {'ok': True, 'warn': True, 'detail': f"未到有效采集时间（当前{hhmm}）"}

        until = hhmm
        if '11:30' < until < '13:00':
            until = '11:30'
        elif until > '15:00':
            until = '15:00'
        if until < '09:31':
            return {'ok': True, 'warn': True, 'detail': f"当前{hhmm}，分钟线尚不足以检查"}

        # XTick 1分钟K常以 09:31 作为第一根，午/收盘边界分钟覆盖也不稳定；
        # 完整性检查只看稳定连续交易分钟，避免把接口口径差异当成缺口。
        expected = [
            m for m in expected_minutes_until(until)
            if m >= '09:31' and m not in ('11:30', '15:00')
        ]
        if not expected:
            return {'ok': True, 'warn': True, 'detail': f"当前{hhmm}，无应检查分钟"}
        stable_until = _stable_expected_minute(expected)

        rows = mdb.executeSqlFetch(
            '''SELECT time, COUNT(DISTINCT code) AS cnt
               FROM cn_stock_minute_bar
               WHERE date=%s AND time BETWEEN %s AND %s
               GROUP BY time
               ORDER BY time''',
            (today, expected[0], expected[-1])
        )
        counts = {
            (r[0].strftime('%H:%M') if hasattr(r[0], 'strftime') else str(r[0])[:5]): int(r[1])
            for r in rows or []
        }
        max_count = max(counts.values()) if counts else 0
        min_count = min(counts.get(t, 0) for t in expected)
        threshold = max(100, int(max_count * 0.95))
        bad = [(t, counts.get(t, 0)) for t in expected if counts.get(t, 0) < threshold]
        stable_expected = [t for t in expected if t <= stable_until]
        stable_bad = [(t, counts.get(t, 0)) for t in stable_expected if counts.get(t, 0) < threshold]
        latest_time = max(counts.keys()) if counts else ''
        row_count = mdb.executeSqlCount(
            'SELECT COUNT(*) FROM cn_stock_minute_bar WHERE date=%s',
            (today,)
        )
        redis_stats = _redis_minute_stats(today, expected)
        redis_threshold = max(100, int((redis_stats.get('max_count') or 0) * 0.95))
        redis_stable_bad = [
            (t, (redis_stats.get('time_counts') or {}).get(t, 0))
            for t in stable_expected
            if (redis_stats.get('time_counts') or {}).get(t, 0) < redis_threshold
        ]

        pg_latest_lag = _lag_text(latest_time, stable_until)
        redis_latest = redis_stats.get('latest_time') or ''
        redis_latest_lag = _lag_text(redis_latest, stable_until)
        status_text = '完整'
        reason = ''
        if not counts and not redis_stats.get('key_count'):
            status_text = '未开始'
            reason = 'PG 和 Redis 今日分钟线均为空，通常是采集/补全任务未启动。'
        elif stable_bad or redis_stable_bad:
            status_text = '不完整'
            reason = '稳定延迟窗口内仍存在分钟缺口，需要补全或等待任务完成。'
        elif bad or redis_latest < stable_until:
            status_text = '采集中/有延迟'
            reason = '最新分钟落后当前时间，但稳定延迟窗口内暂未发现明显缺口。'

        diag = {
            'today': today,
            'now': hhmm,
            'check_until': until,
            'stable_until': stable_until,
            'latency_note': '盘中允许最近约2分钟延迟；超过稳定分钟仍缺口才算不完整。',
            'status_text': status_text,
            'reason': reason,
            'pg': {
                'row_count': row_count,
                'minute_count': len(counts),
                'latest_time': latest_time,
                'latest_count': counts.get(latest_time, 0) if latest_time else 0,
                'latest_lag': pg_latest_lag,
                'baseline_count': max_count,
                'threshold_count': threshold,
                'min_expected_count': min_count,
                'bad_count': len(bad),
                'stable_bad_count': len(stable_bad),
                'bad_examples': [{'time': t, 'count': c} for t, c in bad[:10]],
            },
            'redis': {
                'key_count': redis_stats.get('key_count') or 0,
                'bar_count': redis_stats.get('bar_count') or 0,
                'latest_time': redis_latest,
                'latest_count': redis_stats.get('latest_count') or 0,
                'latest_lag': redis_latest_lag,
                'baseline_count': redis_stats.get('max_count') or 0,
                'threshold_count': redis_threshold,
                'min_expected_count': redis_stats.get('min_expected_count') or 0,
                'stable_bad_count': len(redis_stable_bad),
                'latest_distribution': redis_stats.get('latest_distribution') or [],
                'bad_examples': [{'time': t, 'count': c} for t, c in redis_stable_bad[:10]],
            },
            'fill_task': fill_status,
        }

        def _examples(items):
            return ', '.join(f"{t}:{cnt}" for t, cnt in items[:6])

        if status_text == '未开始':
            return {
                'ok': False,
                'detail': f"未开始：PG 0条，Redis 0只；应检查 {expected[0]}~{stable_until}",
                'minute_diag': diag,
            }

        if stable_bad or redis_stable_bad:
            pg_examples = _examples(stable_bad)
            redis_examples = _examples(redis_stable_bad)
            return {
                'ok': False,
                'detail': (
                    f"不完整：应到{stable_until}；"
                    f"PG最新{latest_time or '-'}({counts.get(latest_time, 0) if latest_time else 0}只, 延迟{pg_latest_lag})，"
                    f"Redis最新{redis_latest or '-'}({redis_stats.get('latest_count') or 0}只, 延迟{redis_latest_lag})；"
                    f"PG缺口{pg_examples or '无'}；Redis缺口{redis_examples or '无'}"
                ),
                'minute_diag': diag,
            }

        return {
            'ok': True,
            'warn': status_text != '完整',
            'detail': (
                f"{status_text}：应到{stable_until}；"
                f"PG最新{latest_time or '-'}({counts.get(latest_time, 0) if latest_time else 0}只, 延迟{pg_latest_lag}, 共{row_count:,}条)；"
                f"Redis最新{redis_latest or '-'}({redis_stats.get('latest_count') or 0}只, 延迟{redis_latest_lag}, {redis_stats.get('key_count') or 0}只)"
            ),
            'minute_diag': diag,
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
        'name':     '分钟K线完整性 (PG/Redis补全)',
        'required': trade_day,
        'action':   'fill_today_minute_bars',
        **_check_minute_integrity(today),
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


class ApiSystemActionStatusHandler(tornado.web.RequestHandler):
    def set_default_headers(self):
        self.set_header('Access-Control-Allow-Origin', '*')
        self.set_header('Content-Type', 'application/json; charset=utf-8')
        self.set_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.set_header('Access-Control-Allow-Headers', 'Content-Type')
        self.set_header('Cache-Control', 'no-store')

    def options(self):
        self.set_status(204)
        self.finish()

    def get(self):
        action = self.get_argument('action', 'fill_today_minute_bars')
        if action != 'fill_today_minute_bars':
            self.write(json.dumps({
                'ok': False,
                'msg': f'暂不支持查询该任务状态: {action}',
            }, ensure_ascii=False))
            return
        self.write(json.dumps({
            'ok': True,
            'task': _minute_fill_snapshot(),
        }, ensure_ascii=False))


def _execute_action(action: str) -> dict:
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    global _minute_fill_running

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

    elif action == 'fill_today_minute_bars':
        if _minute_fill_running:
            return {
                'ok': True,
                'msg': '今日分钟K线补全正在运行',
                'task': _minute_fill_snapshot(),
            }
        try:
            import threading
            _minute_fill_running = True
            _minute_fill_start()
            def _fill_today():
                global _minute_fill_running
                try:
                    from instock.job.fill_minute_bars import fill_today
                    fill_today(progress_callback=_minute_fill_progress)
                    _minute_fill_running = False
                    check = _check_minute_integrity(_today(), include_running=False)
                    if check.get('ok') and not check.get('warn'):
                        _minute_fill_finish(
                            True,
                            f"今日分钟K线补全完成，复检通过：{check.get('detail') or ''}",
                        )
                    else:
                        _minute_fill_finish(
                            False,
                            f"补全任务已跑完，但复检仍不完整：{check.get('detail') or ''}",
                            stage='incomplete',
                        )
                except Exception as e:
                    log.error(f"fill_today_minute_bars error: {e}", exc_info=True)
                    _minute_fill_finish(False, f'今日分钟K线补全失败：{e}', str(e))
                finally:
                    _minute_fill_running = False
            threading.Thread(target=_fill_today, daemon=True).start()
            return {
                'ok': True,
                'msg': '今日分钟K线补全已触发，将同时写入 PostgreSQL 和 Redis',
                'task': _minute_fill_snapshot(),
            }
        except Exception as e:
            _minute_fill_running = False
            _minute_fill_finish(False, f'今日分钟K线补全启动失败：{e}', str(e))
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
        today = _today()
        try:
            import threading
            def _sync():
                from instock.job.daily_market_sync import sync_stock_spot
                sync_stock_spot(today, today)
            threading.Thread(target=_sync, daemon=True).start()
            return {'ok': True, 'msg': f'股票行情同步已触发（{today}，后台运行，约需1-2分钟）'}
        except Exception as e:
            return {'ok': False, 'msg': str(e)}

    elif action == 'sync_hist_data':
        today = _today()
        target_date, target_desc = _expected_hist_date(today)
        try:
            import threading
            def _sync_hist():
                from instock.job.batch_fetch_hist import run as hist_run
                hist_run(target_date, target_date)
            threading.Thread(target=_sync_hist, daemon=True).start()
            return {'ok': True, 'msg': f'每日股票基本数据同步已触发（{target_desc} {target_date}），约需1-2分钟'}
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
