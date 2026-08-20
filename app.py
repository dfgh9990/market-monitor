#!/usr/bin/env python3
"""
盘中市场强弱监控系统 - Flask 后端 (akshare 版)
========================================================
数据源：
  - 全市场 A 股实时行情：akshare.stock_zh_a_spot()  (新浪源，稳定)
  - 大盘指数：akshare.stock_zh_index_spot_sina()      (新浪源，稳定)
  - 行业板块/资金流向：akshare.stock_board_industry_spot_em() (东方财富源，云端可用)
  - 兜底：直接 urllib 请求东方财富 push2 API

特性：
  - 每 60 分钟自动刷新
  - 失败时保留上次缓存，绝不显示假数据
  - 全 CORS 支持，前端可单独部署
  - 单进程/多进程均兼容
"""
import os, json, time, threading, traceback, logging
from datetime import datetime
from decimal import Decimal, InvalidOperation

from flask import Flask, jsonify, send_from_directory

# -----------------------------------------------------------------------------
# 初始化
# -----------------------------------------------------------------------------
app = Flask(__name__, static_folder='.', static_url_path='')
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger('market_monitor')

# 模块延迟导入，避免启动时 akshare 打印进度条阻塞
ak = None

def _import_akshare():
    global ak
    if ak is None:
        import akshare as _ak
        ak = _ak
    return ak

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

WEIGHTS = {
    'breadth': 0.25,
    'volume': 0.15,
    'sector': 0.20,
    'fund': 0.20,
    'rsrs': 0.10,
    'sentiment': 0.10,
}
DIM_NAMES = {
    'breadth': '涨跌比广度',
    'volume': '量能健康度',
    'sector': '板块资金集中度',
    'fund': '主力资金流向',
    'rsrs': '指数趋势',
    'sentiment': '市场情绪温度',
}

REFRESH_INTERVAL = int(os.environ.get('REFRESH_INTERVAL', 60 * 60))  # 默认 60 分钟

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
        return float(x)
    except (TypeError, ValueError):
        return default

def fmt_yi(yuan):
    yi = safe_float(yuan) / 1e8
    s = '+' if yi >= 0 else ''
    return f'{s}{yi:.1f}亿'

def fmt_amount(yuan):
    return f'{safe_float(yuan)/1e8:.0f}亿'

# -----------------------------------------------------------------------------
# 数据获取层
# -----------------------------------------------------------------------------
def fetch_all_quotes():
    """全市场 A 股实时行情，优先新浪源"""
    _ak = _import_akshare()
    df = _ak.stock_zh_a_spot()
    if df is None or df.empty:
        raise ValueError('新浪全市场行情返回为空')
    # 重命名统一
    df = df.rename(columns={
        '代码': 'code', '名称': 'name', '最新价': 'price',
        '涨跌幅': 'pct', '成交额': 'amount', '成交量': 'vol'
    })
    df['pct'] = df['pct'].apply(safe_float)
    df['amount'] = df['amount'].apply(safe_float)
    df['price'] = df['price'].apply(safe_float)
    records = df[['code', 'name', 'price', 'pct', 'amount', 'vol']].to_dict('records')
    return records, len(records)

def fetch_index_quotes():
    """大盘指数，新浪源"""
    _ak = _import_akshare()
    df = _ak.stock_zh_index_spot_sina()
    if df is None or df.empty:
        raise ValueError('新浪指数行情返回为空')
    df = df.rename(columns={
        '代码': 'code', '名称': 'name', '最新价': 'price',
        '涨跌幅': 'pct', '成交额': 'amount'
    })
    # 保留主要指数
    keep = {'上证指数', '深证成指', '沪深300', '创业板指', '科创50'}
    df = df[df['name'].isin(keep)]
    df['pct'] = df['pct'].apply(safe_float)
    df['price'] = df['price'].apply(safe_float)
    df['amount'] = df['amount'].apply(safe_float)
    return df[['code', 'name', 'price', 'pct', 'amount']].to_dict('records')

def fetch_sector_em_direct():
    """直接请求东方财富板块 API（当 akshare 东财接口不可用时兜底）"""
    import urllib.request
    url = (
        'https://push2.eastmoney.com/api/qt/clist/get?'
        'pn=1&pz=500&po=1&np=1&ut=bd1d9ddbbe40792358a9094d06037557&'
        'fltt=2&invt=2&fid=f62&fs=m:90+t:2&'
        'fields=f12,f14,f2,f3,f4,f6,f62,f66,f72,f104,f105'
    )
    req = urllib.request.Request(
        url,
        headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://quote.eastmoney.com/',
            'Accept': 'application/json, text/plain, */*',
        },
    )
    resp = urllib.request.urlopen(req, timeout=20)
    data = json.loads(resp.read().decode('utf-8'))
    diff = data.get('data', {}).get('diff', [])
    if not diff:
        raise ValueError('东财板块 API 返回为空')
    out = []
    for s in diff:
        out.append({
            'code': s.get('f12'),
            'name': s.get('f14'),
            'price': safe_float(s.get('f2')),
            'pct': safe_float(s.get('f3')),
            'amount': safe_float(s.get('f6')),
            'main': safe_float(s.get('f62')),
            'super': safe_float(s.get('f66')),
            'large': safe_float(s.get('f72')),
            'up': int(safe_float(s.get('f104'), 0)),
            'down': int(safe_float(s.get('f105'), 0)),
        })
    return out

def fetch_sectors():
    """行业板块数据，优先 akshare 东财接口，失败则直接请求东财"""
    errors = []
    _ak = _import_akshare()
    try:
        df = _ak.stock_board_industry_spot_em()
        if df is not None and not df.empty:
            df = df.rename(columns={
                '板块代码': 'code', '板块名称': 'name',
                '最新价': 'price', '涨跌幅': 'pct', '成交额': 'amount',
                '主力净流入': 'main', '超大单净流入': 'super', '大单净流入': 'large',
                '上涨家数': 'up', '下跌家数': 'down',
            })
            df['pct'] = df['pct'].apply(safe_float)
            df['main'] = df['main'].apply(safe_float)
            df['super'] = df['super'].apply(safe_float)
            df['large'] = df['large'].apply(safe_float)
            df['up'] = df['up'].apply(lambda x: int(safe_float(x, 0)))
            df['down'] = df['down'].apply(lambda x: int(safe_float(x, 0)))
            return df[['code', 'name', 'price', 'pct', 'amount', 'main', 'super', 'large', 'up', 'down']].to_dict('records'), 'akshare-东财'
    except Exception as e:
        errors.append(f'akshare-东财: {e}')

    try:
        return fetch_sector_em_direct(), 'urllib-东财'
    except Exception as e:
        errors.append(f'urllib-东财: {e}')

    raise ValueError('板块数据全部获取失败: ' + '; '.join(errors))

# -----------------------------------------------------------------------------
# 评分引擎
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
    """执行一次完整的数据刷新与评分计算"""
    global CACHE
    logger.info('开始刷新市场数据...')
    start = time.time()
    sector_src = '获取失败'
    sector_err = None
    try:
        quotes, total_stocks = fetch_all_quotes()
        indices = fetch_index_quotes()
    except Exception as e:
        err = f'{type(e).__name__}: {e}'
        logger.error(f'刷新失败: {err}')
        logger.debug(traceback.format_exc())
        with CACHE_LOCK:
            CACHE['error'] = err
            CACHE['last_update'] = now_str() + ' (出错)'
            CACHE['is_trading'] = is_trading_time()
        return dict(CACHE)

    try:
        sectors, sector_src = fetch_sectors()
    except Exception as e:
        sector_err = f'{type(e).__name__}: {e}'
        logger.warning(f'板块数据获取失败，使用空数据继续: {sector_err}')
        sectors = []

    try:
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
            'breadth': d_b,
            'volume': d_v,
            'sector': d_s,
            'fund': d_f,
            'rsrs': d_r,
            'sentiment': d_m,
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
                '全市场': 'akshare-新浪',
                '指数': 'akshare-新浪',
                '板块资金': sector_src,
            },
            'scan_time': f'{time.time()-start:.1f}s',
        }
        with CACHE_LOCK:
            CACHE.update(result)
        logger.info(f'刷新完成 评分={total} 仓位={pos}% 状态={status} 耗时={result["scan_time"]}')
        return result
    except Exception as e:
        err = f'{type(e).__name__}: {e}'
        logger.error(f'刷新失败: {err}')
        logger.debug(traceback.format_exc())
        with CACHE_LOCK:
            CACHE['error'] = err
            CACHE['last_update'] = now_str() + ' (出错)'
            CACHE['is_trading'] = is_trading_time()
        return dict(CACHE)

def scheduler_loop():
    """后台定时刷新线程"""
    while True:
        try:
            refresh_data()
        except Exception as e:
            logger.error(f'调度异常: {e}')
        time.sleep(REFRESH_INTERVAL)

# -----------------------------------------------------------------------------
# HTTP 路由
# -----------------------------------------------------------------------------
@app.after_request
def after_request(resp):
    resp.headers['Access-Control-Allow-Origin'] = '*'
    resp.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    resp.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    return resp

@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

@app.route('/api/status')
def api_status():
    with CACHE_LOCK:
        return jsonify(dict(CACHE))

@app.route('/api/refresh')
def api_refresh():
    result = refresh_data()
    return jsonify(result)

@app.route('/api/health')
def api_health():
    return jsonify({'ok': True, 'time': now_str()})

# -----------------------------------------------------------------------------
# 启动
# -----------------------------------------------------------------------------
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    host = os.environ.get('HOST', '0.0.0.0')
    logger.info('启动盘中市场强弱监控系统 (akshare 版)...')
    # 启动后台刷新线程
    t = threading.Thread(target=scheduler_loop, daemon=True)
    t.start()
    # 立即执行一次，避免首次访问空数据
    refresh_data()
    app.run(host=host, port=port, threaded=True, debug=False)
