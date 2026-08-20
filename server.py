#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
盘中市场强弱监控系统 · 零依赖后端 (Python 标准库)
========================================================
数据源（服务器端直连，1 次/小时，不会被限流）：
  - 全市场 A 股实时行情：东方财富 push2 clist  (涨跌家数/量能/涨跌停)
  - 大盘指数：          东方财富 push2 ulist  (主) + 新浪 (兜底)
  - 行业板块/资金流向：  东方财富 push2 clist  (主力/超大单净流入)

特性：
  - 仅用 Python 标准库（http.server + urllib + threading），无需 pip install
  - 每 60 分钟自动刷新
  - 失败时保留上次缓存，绝不显示假数据
  - 提供 /api/status、/api/refresh、/api/health，前端可单独部署
  - 任何装有 Python 3 的机器 `python server.py` 即可运行

运行：
    python server.py            # 默认 http://0.0.0.0:5000
    PORT=8080 python server.py  # 自定义端口
"""
import os, json, time, threading, traceback, urllib.request, urllib.error
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler

# -----------------------------------------------------------------------------
# 缓存与配置
# -----------------------------------------------------------------------------
CACHE = {
    'last_update': '等待首次扫描...',
    'total_score': None,
    'position': None,
    'status': '初始化中',
    'scores': {},
    'details': {},
    'indices': [],
    'top_sectors': [],
    'market_breadth': {},
    'is_trading': False,
    'error': None,
    'data_sources': {},
}
CACHE_LOCK = threading.Lock()
SCAN_LOCK = threading.Lock()  # 防止并发扫描

WEIGHTS = {
    'breadth': 0.25,
    'volume': 0.15,
    'sector': 0.20,
    'fund': 0.20,
    'rsrs': 0.10,
    'sentiment': 0.10,
}

REFRESH_INTERVAL = int(os.environ.get('REFRESH_INTERVAL', 60 * 60))  # 默认 60 分钟

HTML_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'index.html')

# -----------------------------------------------------------------------------
# 工具函数
# -----------------------------------------------------------------------------
def now_str():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')

def is_trading_time():
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    hhmm = now.hour * 100 + now.minute
    return (925 <= hhmm <= 1130) or (1300 <= hhmm <= 1500)

def safe_float(x, default=0.0):
    if x is None:
        return default
    try:
        v = float(x)
        if v != v:  # NaN
            return default
        return v
    except (TypeError, ValueError):
        return default

def fmt_yi(yuan):
    yi = safe_float(yuan) / 1e8
    s = '+' if yi >= 0 else ''
    return f'{s}{yi:.1f}亿'

# -----------------------------------------------------------------------------
# 数据获取层（标准库直连东方财富 / 新浪）
# -----------------------------------------------------------------------------
def em_get(url, timeout=25):
    """东方财富 push2 接口统一请求"""
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Referer': 'https://quote.eastmoney.com/',
        'Accept': 'application/json, text/plain, */*',
    })
    resp = urllib.request.urlopen(req, timeout=timeout)
    return json.loads(resp.read().decode('utf-8'))

def fetch_all_quotes():
    """全市场 A 股实时行情（东方财富 push2，含涨跌家数/量能/涨跌停）"""
    url = ('https://push2.eastmoney.com/api/qt/clist/get?'
           'pn=1&pz=10000&po=1&np=1&ut=bd1d9ddbbe40792358a9094d06037557&'
           'fltt=2&invt=2&fid=f3&'
           'fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23&'
           'fields=f2,f3,f4,f5,f6,f12,f14')
    data = em_get(url)
    diff = data.get('data', {}).get('diff', [])
    if not diff:
        raise ValueError('东方财富全市场行情返回为空')
    total = data.get('data', {}).get('total', len(diff))
    quotes = []
    for s in diff:
        quotes.append({
            'code': s.get('f12'),
            'name': s.get('f14'),
            'price': safe_float(s.get('f2')),
            'pct': safe_float(s.get('f3')),      # 已为百分比数值，如 1.5 即 +1.5%
            'amount': safe_float(s.get('f6')),   # 单位：元
        })
    return quotes, total

def fetch_indices():
    """大盘指数：东方财富 ulist 为主，新浪兜底"""
    # —— 主源：东方财富 ulist ——
    try:
        url = ('https://push2.eastmoney.com/api/qt/ulist.np/get?'
               'fltt=2&fields=f2,f3,f4,f6,f12,f14&'
               'secids=1.000001,0.399001,1.000300,0.399006,1.000688')
        data = em_get(url)
        d = data.get('data', {})
        diff = d.get('diff', [])
        if isinstance(diff, dict):
            diff = list(diff.values())
        if diff:
            idx = []
            for i in diff:
                idx.append({
                    'code': i.get('f12'),
                    'name': i.get('f14'),
                    'price': safe_float(i.get('f2')),
                    'pct': safe_float(i.get('f3')),
                    'amount': safe_float(i.get('f6')),
                })
            return idx, '东方财富'
    except Exception as e:
        print(f'  [warn] 东方财富指数失败，尝试新浪: {e}')

    # —— 兜底：新浪 s_ 指数 ——
    try:
        sina = ('https://hq.sinajs.com.cn/list='
                's_sh000001,s_sz399001,s_sh000300,s_sz399006,s_sh000688')
        req = urllib.request.Request(sina, headers={
            'User-Agent': 'Mozilla/5.0',
            'Referer': 'https://finance.sina.com.cn/',
        })
        raw = urllib.request.urlopen(req, timeout=15).read().decode('gbk', 'ignore')
        idx = []
        for line in raw.strip().split(';'):
            line = line.strip()
            if '=' not in line or 'hq_str_' not in line:
                continue
            var, val = line.split('=', 1)
            parts = val.strip().strip('"').split(',')
            if len(parts) < 3:
                continue
            name = parts[0]
            cur = safe_float(parts[1])
            prev = safe_float(parts[2])
            pct = (cur - prev) / prev * 100 if prev else 0.0
            idx.append({'code': var.replace('hq_str_', ''), 'name': name,
                        'price': cur, 'pct': pct, 'amount': 0.0})
        if idx:
            return idx, '新浪'
    except Exception as e:
        print(f'  [warn] 新浪指数也失败: {e}')

    raise ValueError('指数行情全部获取失败')

def fetch_sectors():
    """行业板块 + 主力/超大单净流入（东方财富 push2）"""
    url = ('https://push2.eastmoney.com/api/qt/clist/get?'
           'pn=1&pz=500&po=1&np=1&ut=bd1d9ddbbe40792358a9094d06037557&'
           'fltt=2&invt=2&fid=f62&fs=m:90+t:2&'
           'fields=f12,f14,f2,f3,f4,f6,f62,f66,f72,f104,f105')
    data = em_get(url)
    diff = data.get('data', {}).get('diff', [])
    if not diff:
        raise ValueError('东方财富板块行情返回为空')
    sectors = []
    for s in diff:
        sectors.append({
            'code': s.get('f12'),
            'name': s.get('f14'),
            'price': safe_float(s.get('f2')),
            'pct': safe_float(s.get('f3')),
            'amount': safe_float(s.get('f6')),
            'main': safe_float(s.get('f62')),    # 主力净流入（元）
            'super': safe_float(s.get('f66')),   # 超大单净流入
            'large': safe_float(s.get('f72')),   # 大单净流入
            'up': int(safe_float(s.get('f104'), 0)),
            'down': int(safe_float(s.get('f105'), 0)),
        })
    return sectors, '东方财富'

# -----------------------------------------------------------------------------
# 评分引擎（与 akshare 版一致）
# -----------------------------------------------------------------------------
def calc_breadth(quotes):
    total = len(quotes)
    if total == 0:
        return 50, {'上涨家数': '--', '下跌家数': '--'}
    up = sum(1 for q in quotes if q['pct'] > 0)
    down = sum(1 for q in quotes if q['pct'] < 0)
    flat = total - up - down
    strong = sum(1 for q in quotes if q['pct'] > 3)
    weak = sum(1 for q in quotes if q['pct'] < -3)
    limit_up = sum(1 for q in quotes if q['pct'] >= 9.8)
    limit_down = sum(1 for q in quotes if q['pct'] <= -9.8)
    up_ratio = up / total
    strong_ratio = strong / total
    weak_ratio = weak / total
    score = max(0, min(100, up_ratio * 60 + strong_ratio * 40 - weak_ratio * 30))
    return score, {
        '上涨家数': up, '下跌家数': down, '平盘家数': flat,
        '上涨比例': f'{up_ratio*100:.1f}%',
        '强势股(>3%)': strong, '弱势股(<-3%)': weak,
        '涨停': limit_up, '跌停': limit_down,
    }

def calc_volume(quotes):
    total_turnover = sum(q['amount'] for q in quotes) / 1e8
    if total_turnover > 12000:
        score, lvl = 90, '极度放量'
    elif total_turnover > 10000:
        score, lvl = 80, '放量'
    elif total_turnover > 8000:
        score, lvl = 70, '放量'
    elif total_turnover > 6000:
        score, lvl = 55, '温和'
    elif total_turnover > 4000:
        score, lvl = 45, '正常'
    elif total_turnover > 2500:
        score, lvl = 30, '缩量'
    else:
        score, lvl = 15, '极度缩量'
    return score, {'总成交额': f'{total_turnover:.0f}亿', '量能等级': lvl}

def calc_sector(sectors):
    if not sectors:
        return 50, {'板块状态': '—'}
    inflow = sum(1 for s in sectors if s['main'] > 0)
    outflow = len(sectors) - inflow
    total_flow = sum(s['main'] for s in sectors)
    strong = sum(1 for s in sectors if s['pct'] > 2)
    top5 = sorted(sectors, key=lambda x: x['main'], reverse=True)[:5]
    top5_flow = sum(s['main'] for s in top5)
    top5_ratio = top5_flow / total_flow if total_flow != 0 else 0
    score = max(0, min(100, min(top5_ratio * 50 + 50, 60) + min(strong * 4, 40)))
    return score, {
        '净流入板块': inflow, '净流出板块': outflow,
        'TOP5占比': f'{top5_ratio*100:.1f}%',
        '强势板块(>2%)': strong,
        '主力净流入': fmt_yi(total_flow),
    }

def calc_fund_flow(sectors):
    if not sectors:
        return 50, {'主力净流入': '—', '超大单净流入': '—'}
    main = sum(s['main'] for s in sectors)
    super_ = sum(s['super'] for s in sectors)
    large = sum(s['large'] for s in sectors)
    my = main / 1e8
    sy = super_ / 1e8
    if main > 0 and super_ > 0:
        score = 95 if my > 50 else (90 if my > 20 else 85)
    elif main > 0 and super_ <= 0:
        score = 55
    elif main <= 0 and super_ > 0:
        score = 45
    elif main <= 0 and super_ <= 0:
        score = 10 if my < -50 else (20 if my < -20 else 35)
    else:
        score = 40
    dir_ = '双流入' if (main > 0 and super_ > 0) else ('双流出' if (main < 0 and super_ < 0) else '分歧')
    return score, {
        '主力净流入': fmt_yi(main),
        '超大单净流入': fmt_yi(super_),
        '大单净流入': fmt_yi(large),
        '资金方向': dir_,
    }

def calc_rsrs(indices):
    if not indices:
        return 50, {'沪深300': '—', '上证指数': '—'}
    hs300 = next((i for i in indices if '沪深300' in i['name'] or i['code'].endswith('000300')), {})
    sh = next((i for i in indices if '上证' in i['name'] and 'B股' not in i['name']), {})
    avg = (safe_float(hs300.get('pct'), 0) + safe_float(sh.get('pct'), 0)) / 2
    score = max(0, min(100, 50 + avg * 10))
    return score, {
        '沪深300': f"{safe_float(hs300.get('pct'), 0):+.2f}%",
        '上证指数': f"{safe_float(sh.get('pct'), 0):+.2f}%",
        '趋势方向': '上行' if avg > 0.3 else ('下行' if avg < -0.3 else '横盘'),
    }

def calc_sentiment(breadth_details):
    up = safe_float(breadth_details.get('涨停', 0), 0)
    down = safe_float(breadth_details.get('跌停', 0), 0)
    total = up + down
    ratio = up / total if total > 0 else 0.5
    if ratio > 0.8:
        score = 100
    elif ratio > 0.6:
        score = 80
    elif ratio > 0.5:
        score = 65
    elif ratio > 0.4:
        score = 45
    elif ratio > 0.2:
        score = 25
    else:
        score = 10
    if up > 50:
        score = min(100, score + 10)
    if down > 30:
        score = max(0, score - 10)
    mood = '极度乐观' if up > 50 else ('恐慌' if down > 30 else '正常')
    return score, {'涨停': int(up), '跌停': int(down), '涨跌停比': f'{ratio*100:.0f}%', '情绪': mood}

def compute_position(total):
    if total >= 80:
        return 90, '强势市场，积极进攻'
    elif total >= 60:
        return 70, '偏强市场，正常操作'
    elif total >= 40:
        return 50, '震荡市场，灵活应对'
    elif total >= 20:
        return 30, '偏弱市场，防守为主'
    else:
        return 10, '弱势市场，空仓观望'

# -----------------------------------------------------------------------------
# 刷新主流程
# -----------------------------------------------------------------------------
def refresh_data():
    """执行一次完整的数据刷新与评分计算（带并发保护）"""
    if not SCAN_LOCK.acquire(blocking=False):
        print('  [skip] 已有扫描在进行中')
        with CACHE_LOCK:
            return dict(CACHE)
    try:
        print(f'\n[{now_str()}] 开始扫描市场数据...')
        start = time.time()
        sector_src = '获取失败'
        sector_err = None

        quotes, total_stocks = fetch_all_quotes()
        indices, idx_src = fetch_indices()

        try:
            sectors, sector_src = fetch_sectors()
        except Exception as e:
            sector_err = f'{type(e).__name__}: {e}'
            print(f'  [warn] 板块数据获取失败，使用空数据继续: {sector_err}')
            sectors = []

        s_b, d_b = calc_breadth(quotes)
        s_v, d_v = calc_volume(quotes)
        s_s, d_s = calc_sector(sectors)
        s_f, d_f = calc_fund_flow(sectors)
        s_r, d_r = calc_rsrs(indices)
        s_m, d_m = calc_sentiment(d_b)

        scores = {
            'breadth': round(s_b, 1),
            'volume': round(s_v, 1),
            'sector': round(s_s, 1),
            'fund': round(s_f, 1),
            'rsrs': round(s_r, 1),
            'sentiment': round(s_m, 1),
        }
        details = {
            'breadth': d_b, 'volume': d_v, 'sector': d_s,
            'fund': d_f, 'rsrs': d_r, 'sentiment': d_m,
        }
        total = round(sum(scores[k] * WEIGHTS[k] for k in WEIGHTS), 1)
        pos, status = compute_position(total)

        up_count = sum(1 for q in quotes if q['pct'] > 0)
        down_count = sum(1 for q in quotes if q['pct'] < 0)
        top_sectors = sorted(sectors, key=lambda x: x['main'], reverse=True)[:10]

        result = {
            'last_update': now_str(),
            'total_score': total,
            'position': pos,
            'status': status,
            'scores': scores,
            'details': details,
            'indices': indices,
            'top_sectors': top_sectors,
            'market_breadth': {
                'total': total_stocks,
                'up': up_count,
                'down': down_count,
                'up_ratio': f'{up_count/total_stocks*100:.1f}%' if total_stocks else '--',
            },
            'is_trading': is_trading_time(),
            'error': sector_err,
            'data_sources': {
                '全市场': '东方财富',
                '指数': idx_src,
                '板块资金': sector_src,
            },
            'scan_time': f'{time.time()-start:.1f}s',
        }
        with CACHE_LOCK:
            CACHE.update(result)
        print(f'  完成 评分={total} 仓位={pos}% 状态={status} 耗时={result["scan_time"]} 源=[全市场:东方财富 指数:{idx_src} 板块:{sector_src}]')
        return result
    except Exception as e:
        err = f'{type(e).__name__}: {e}'
        print(f'  [error] 刷新失败: {err}')
        traceback.print_exc()
        with CACHE_LOCK:
            # 若已有有效数据（如离线快照），失败时不覆盖，仅保留原状态
            if CACHE.get('total_score') is None:
                CACHE['error'] = err
                CACHE['last_update'] = now_str() + ' (出错)'
            CACHE['is_trading'] = is_trading_time()
        return dict(CACHE)
    finally:
        SCAN_LOCK.release()

def scheduler_loop():
    """后台定时刷新线程：启动即扫描一次，之后每 REFRESH_INTERVAL 秒"""
    refresh_data()
    while True:
        time.sleep(REFRESH_INTERVAL)
        try:
            refresh_data()
        except Exception as e:
            print(f'  [error] 调度异常: {e}')

# -----------------------------------------------------------------------------
# HTTP 服务
# -----------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    def _send_json(self, obj):
        body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self):
        try:
            with open(HTML_PATH, 'r', encoding='utf-8') as f:
                body = f.read().encode('utf-8')
        except Exception as e:
            body = f'<html><body><h1>index.html 未找到</h1><p>{e}</p></body></html>'.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split('?', 1)[0]
        if path in ('/', '/index.html'):
            self._send_html()
        elif path == '/api/status':
            with CACHE_LOCK:
                self._send_json(dict(CACHE))
        elif path == '/api/refresh':
            # 触发一次扫描（后台线程），等待完成后返回最新结果
            t = threading.Thread(target=refresh_data, daemon=True)
            t.start()
            t.join(timeout=40)
            with CACHE_LOCK:
                self._send_json(dict(CACHE))
        elif path == '/api/health':
            self._send_json({'ok': True, 'time': now_str()})
        else:
            self.send_response(404)
            self.end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.end_headers()

    def log_message(self, fmt, *args):
        pass  # 静默

def load_seed():
    """可选：启动时从 SEED_FILE 载入一份快照作为初始缓存（离线演示/预览用）。
    线上运行（有网络）时无需设置，首次实时扫描会覆盖它。"""
    seed = os.environ.get('SEED_FILE')
    if not seed or not os.path.exists(seed):
        return False
    try:
        with open(seed, 'r', encoding='utf-8') as f:
            data = json.load(f)
        with CACHE_LOCK:
            CACHE.update(data)
        print(f'  已载入离线快照：{seed}（实时扫描成功后将自动覆盖）')
        return True
    except Exception as e:
        print(f'  [warn] 载入快照失败: {e}')
        return False

def main():
    port = int(os.environ.get('PORT', 5000))
    host = os.environ.get('HOST', '0.0.0.0')
    load_seed()
    print('=' * 60)
    print('  盘中市场强弱监控系统 · 零依赖后端')
    print('  数据源：东方财富 push2（1 次/小时，不限流）')
    print(f'  刷新间隔：{REFRESH_INTERVAL // 60} 分钟')
    print('=' * 60)
    t = threading.Thread(target=scheduler_loop, daemon=True)
    t.start()
    server = HTTPServer((host, port), Handler)
    print(f'  服务已启动：http://{host}:{port}')
    print(f'  本机访问：http://127.0.0.1:{port}')
    print(f'  按 Ctrl+C 停止')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n  服务已停止')

if __name__ == '__main__':
    main()
