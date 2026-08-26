"""六维市场强弱评分引擎（纯标准库）。

数据来源：腾讯自选股 MCP（westock-mcp）返回的 overview / sector 原始 JSON。
本模块负责：
  1. extract_overview() / extract_sector() 把 MCP 原始结构归一化为内部 raw dict
  2. compute() 基于六维加权算出综合评分 + 仓位系数 + 诊断
输出 data.json 供前端（Cloudflare Pages / GitHub Pages / 本地）读取。
"""

import json
import datetime

# 六维权重（与需求一致）
WEIGHTS = {
    "breadth": 0.25,   # 涨跌比广度
    "volume": 0.15,    # 量能健康度
    "sector": 0.20,    # 板块资金集中度
    "fund": 0.20,      # 主力资金流向
    "trend": 0.10,     # 指数趋势 RSRS
    "sentiment": 0.10, # 市场情绪温度
}


def clamp(x, lo=0, hi=100):
    return max(lo, min(hi, x))


def _to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ---------------- 量能健康度：盘中时点归一化基准 ----------------

# 参考基准：近期两市常态日成交额（亿元）。
# 用途：把「盘中累计成交额」换算成相对全天的「量能节奏(pace)」。
# 该值应随市场量能中枢变化更新（建议取近 20 个交易日两市成交额均值）。
REFERENCE_DAILY_AMOUNT_YI = 15000.0

# 盘中累计成交额占比基准曲线：锚点 = (距 9:30 的连续交易分钟, 截至该时点应完成的全天成交额占比)。
# 午休(11:30~13:00)不交易，故 13:00 之后扣除 90 分钟。曲线取自两市量能历史分布的经验估计
#（上午约 54%、下午约 46%，开盘与尾盘相对密集）。
_VOLUME_SHARE_ANCHORS = [
    (0, 0.00), (30, 0.15), (60, 0.28), (90, 0.41), (120, 0.54),
    (150, 0.63), (180, 0.73), (210, 0.85), (240, 1.00),
]


def _trading_minutes_since_open(hhmm):
    """HH:MM → 距 9:30 的连续交易分钟（午休不计入）；非交易时段返回 None。"""
    try:
        h, m = (int(x) for x in hhmm.split(":"))
    except Exception:
        return None
    mins = h * 60 + m
    if mins < 9 * 60 + 30 or mins > 15 * 60:
        return None
    if mins > 11 * 60 + 30:
        mins -= 90
    return mins - (9 * 60 + 30)


def _expected_share(hhmm):
    """截至该时点、按历史常态应完成的全天成交额占比(0~1)。收盘后(非交易时段)按 1.0 计。"""
    t = _trading_minutes_since_open(hhmm)
    if t is None:
        return 1.0
    a = _VOLUME_SHARE_ANCHORS
    if t <= a[0][0]:
        return a[0][1]
    if t >= a[-1][0]:
        return a[-1][1]
    for i in range(1, len(a)):
        t0, s0 = a[i - 1]
        t1, s1 = a[i]
        if t <= t1:
            return s0 + (s1 - s0) * (t - t0) / (t1 - t0)
    return a[-1][1]


def _now_hhmm():
    bj = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
    return bj.strftime("%H:%M")


# ---------- 从 MCP 原始 JSON 抽取 ----------

def extract_overview(overview_json):
    """overview_json: westock data_market_overview(type='all') 的原始返回。"""
    rows = {}
    if isinstance(overview_json, dict) and overview_json.get("ok"):
        for item in overview_json.get("data", []):
            rows[item.get("listCode")] = item.get("row", {})

    updown = rows.get("market_statis_updown", {})
    trade = rows.get("market_statis_daily_trade", {})
    tech = rows.get("market_statis_technical", {})
    val = rows.get("market_statis_valuation", {})

    breadth = {
        "up": updown.get("CNT_RED", 0),
        "down": updown.get("CNT_GREEN", 0),
        "flat": updown.get("CNT_ZERO", 0),
        "total": updown.get("CNT_TOTAL", 0),
        "limit_up": updown.get("CNT_REACH_UPLIMIT", 0),
        "limit_down": updown.get("CNT_REACH_DNLIMIT", 0),
        "high20": updown.get("CNT_HIGH20", 0),
        "low20": updown.get("CNT_LOW20", 0),
    }

    volume = {
        "amount_yi": trade.get("MONEY", 0),
        "avg5_ratio": trade.get("MONEY_5DAVG_RATIO", 100),
        "avg10_ratio": trade.get("MONEY_10DAVG_RATIO", 100),
        "avg20_ratio": trade.get("MONEY_20DAVG_RATIO", 100),
    }

    indices = [
        {"name": "上证指数", "code": "000001.SH", "close": trade.get("CLOSE_PRICE_SZZS"), "chg_pct": trade.get("CHANGE_PCT_SZZS")},
        {"name": "深证成指", "code": "399001.SZ", "close": trade.get("CLOSE_PRICE_SZCZ"), "chg_pct": trade.get("CHANGE_PCT_SZCZ")},
        {"name": "创业板指", "code": "399006.SZ", "close": trade.get("CLOSE_PRICE_CYBZ"), "chg_pct": trade.get("CHANGE_PCT_CYBZ")},
    ]

    technical = {
        "price": trade.get("CLOSE_PRICE_SZZS"),
        "ma5": tech.get("MA_5"),
        "ma10": tech.get("MA_10"),
        "ma20": tech.get("MA_20"),
        "ma60": tech.get("MA_60"),
        "ma250": tech.get("MA_250"),
        "macd": tech.get("MACD"),
        "dif": tech.get("DIF"),
        "dea": tech.get("DEA"),
        "rsi6": tech.get("RSI_6"),
        "rsi12": tech.get("RSI_12"),
        "rsi24": tech.get("RSI_24"),
        "kdj_k": tech.get("KDJ_K"),
        "kdj_d": tech.get("KDJ_D"),
        "kdj_j": tech.get("KDJ_J"),
    }

    valuation = {"pe_ttm_pct_10y": val.get("PE_TTM_PCT_10Y")}

    return {
        "date": updown.get("date") or trade.get("date"),
        "breadth": breadth,
        "volume": volume,
        "indices": indices,
        "technical": technical,
        "valuation": valuation,
    }


def extract_sector(sector_json):
    """sector_json: westock data_sector(mode='ranking') 的原始返回。
    注意：MCP 返回的 zljlr 单位为「万元」，这里统一换算成「亿元」。
    输出两块：
      - sector_top/sector_bottom：按主力净流入排名的板块（fundflow.plate.top/bottom）
      - sector_rank：按涨跌幅排名的板块（rank.plate，含领涨股，供前端「板块涨幅 TOP」）"""
    top, bottom = [], []
    rank = []
    if isinstance(sector_json, dict) and sector_json.get("ok"):
        data = sector_json.get("data", {})
        ff = data.get("fundflow", {})
        plate = ff.get("plate", {})
        for t in plate.get("top", [])[:5]:
            z = _to_float(t.get("zljlr"))
            top.append({"name": t.get("name"), "zdf": _to_float(t.get("zdf")),
                        "zljlr": (z / 10000.0) if z is not None else None})
        for t in plate.get("bottom", [])[:5]:
            z = _to_float(t.get("zljlr"))
            bottom.append({"name": t.get("name"), "zdf": _to_float(t.get("zdf")),
                           "zljlr": (z / 10000.0) if z is not None else None})
        # 板块涨幅排行（rank.plate：bd_name/bd_zdf/bd_lb/bd_hsl/nzg_name/nzg_zdf）
        for t in data.get("rank", {}).get("plate", [])[:10]:
            if not t.get("bd_name"):
                continue
            rank.append({
                "name": t.get("bd_name"),
                "zdf": _to_float(t.get("bd_zdf")),
                "lb": _to_float(t.get("bd_lb")),
                "hsl": _to_float(t.get("bd_hsl")),
                "leader": t.get("nzg_name"),
                "leader_zdf": _to_float(t.get("nzg_zdf")),
            })
    # 默认按「主线清晰（上涨板块 60~80%）」给 0.7；精确值可由调用方覆盖
    return {"sector_top": top, "sector_bottom": bottom, "sector_rank": rank, "sector_rising_ratio": 0.7}


# ---------- 六维评分 ----------

def score_breadth(b):
    """涨跌比广度：抛物线倒扣分（反身性冷却机制）。

    X = 上涨比例(up/total)：得分 = 4·X·(1-X)·100
      - X=0.5（涨跌各半）得满分 100（最健康、最稳）
      - X→0（极弱）或 X→1（极强/高潮）得分都趋近 0
    哲学：中庸之道——只在"上涨占优但未到极致"时给重仓；
          盛极而衰——X>0.85 触高潮预警自动降权；X<0.15 强制空仓。
    返回 (score, detail, overheat, label)。
    """
    up = b.get("up", 0) or 0
    down = b.get("down", 0) or 0
    flat = b.get("flat", 0) or 0
    total = b.get("total", 0) or (up + down + flat)
    if total <= 0:
        return 50.0, {"上涨比例": "—", "x": 0.5, "市场状态": "无数据", "计分模型": "—"}, False, "中性"
    x = up / total
    if x < 0.15:
        score = 0.0
        status = "极弱 · 冰点（强制空仓）"
        model = "反身性冷却(非对称)"
    elif x <= 0.5:
        # 弱侧：随涨股比单调上升——跌得多→分低，平衡(50%)→满分；符合"跌过头归零、平衡给重仓"
        score = (x - 0.15) / (0.5 - 0.15) * 100
        status = "偏弱" if x < 0.30 else "正常"
        model = "反身性冷却(非对称)"
    else:
        # 强侧：倒 U 抛物线冷却——涨过头(>85%)自动降权防范退潮
        score = 4 * x * (1 - x) * 100
        if x > 0.80:
            status = "🔥 极度高潮，警惕退潮"
            model = "反身性冷却(抛物线)"
        elif x > 0.70:
            status = "偏热"
            model = "反身性冷却(抛物线)"
        else:
            status = "正常"
            model = "反身性冷却(抛物线)"
    overheat = x > 0.80
    detail = {
        "上涨比例": "{:.1f}%".format(x * 100),
        "x": round(x, 4),
        "市场状态": status,
        "计分模型": model,
    }
    return clamp(score), detail, overheat, status


def score_volume(v, up_ratio=None, hhmm=None):
    """量能健康度（盘中实时口径）：量能节奏(pace) = 当前时点累计成交额 ÷ 该时点预期累计额。

    修正说明（重点）：旧逻辑用全天阈值(1.2~2.2万亿)直接映射「盘中累计额」，但上午累计额
    通常只有全天的 15%~40%，于是 base 直接变负、分数被压到 0 附近 —— 这就是「盘中量能异常偏低」。
    现改为按「当前时点匹配的累计占比」归一化：
        预期累计额 = REFERENCE_DAILY_AMOUNT_YI × 该时点预期占比(_expected_share)
        pace       = 实际累计额 / 预期累计额
        pace≈1 正常；>1.3 放量健康；<0.6 缩量偏弱
    再叠加量价配合修正：普跌放量(出货)打折、普涨放量(健康)加成。
    返回 (score, detail)，detail 含各分量便于核对。
    """
    amt = v.get("amount_yi")
    hhmm = hhmm or _now_hhmm()
    use_pace = (v.get("source") in ("public_realtime", "tongdaxin_realtime")) or (amt and not v.get("avg10_ratio"))
    if use_pace and amt:
        share = _expected_share(hhmm)
        expected = REFERENCE_DAILY_AMOUNT_YI * share
        pace = amt / expected if expected > 0 else 1.0
        base = clamp(50 + (pace - 1.0) * 70)
        detail = {
            "累计成交额_亿": round(amt, 1),
            "当前时点": hhmm,
            "预期累计占比": "{:.1f}%".format(share * 100),
            "预期累计额_亿": round(expected, 1),
            "量能节奏_pace": round(pace, 2),
            "量价配合": "—",
            "基准日成交额_亿": REFERENCE_DAILY_AMOUNT_YI,
        }
        if up_ratio is not None:
            if up_ratio < 0.35:
                base *= 0.45
                detail["量价配合"] = "普跌放量 ×0.45"
            elif up_ratio < 0.45:
                base *= 0.75
                detail["量价配合"] = "弱市放量 ×0.75"
            elif up_ratio > 0.65:
                base *= 1.15
                detail["量价配合"] = "普涨放量 ×1.15"
        return clamp(base), detail
    # 旧口径（MCP 路径，数据为相对 10 日均值的比值，日频）：直接映射比值
    r = v.get("avg10_ratio", 100) or 100
    base = (r - 60) / (130 - 60) * 100
    return clamp(base), {"量能基准": "10日均值的{:.0f}%".format(r)}


def score_sector(top, rising_ratio):
    if not top:
        return 50.0
    max_gain = max((t.get("zdf") or 0) for t in top)
    rr = rising_ratio if rising_ratio is not None else 0.5
    s = 35 + min(max_gain, 10) * 4 + (rr - 0.5) * 50
    return clamp(s)


def score_fund(top, bottom, up_ratio=None):
    """主力资金流向：有资金流数据（zljlr）用净流入；公开接口无资金流时改用板块涨幅动能替代。

    涨幅替代经校准：系数 6 → 3.5（原系数在弱市会因板块涨幅差虚高）。
    广度修正：市场普跌（up_ratio<0.4）时资金维度封顶 55 分，避免"指数弱、资金假强"的矛盾定性。
    """
    has_flow = any(t.get("zljlr") is not None for t in (top or [])[:3]) or \
               any(t.get("zljlr") is not None for t in (bottom or [])[:3])
    if has_flow:
        net = 0.0
        for t in (top or [])[:3]:
            z = t.get("zljlr")
            if z is not None:
                net += z
        for t in (bottom or [])[:3]:
            z = t.get("zljlr")
            if z is not None:
                net += z
        return clamp(50 + net * 0.3)
    # 涨幅动能替代
    tp = sum((t.get("zdf") or 0) for t in (top or [])[:3]) / max(len([1 for t in (top or [])[:3]]), 1)
    bt = sum((t.get("zdf") or 0) for t in (bottom or [])[:3]) / max(len([1 for t in (bottom or [])[:3]]), 1)
    s = 50 + (tp - bt) * 3.5
    if up_ratio is not None:
        if up_ratio < 0.4:
            s = min(s, 55)
        elif up_ratio < 0.5:
            s = min(s, 65)
    return clamp(s)


def score_trend(t):
    price = t.get("price")
    if price is None:
        return 50.0
    mas = [t.get(k) for k in ("ma5", "ma10", "ma20", "ma60", "ma250") if t.get(k) is not None]
    if not mas:
        return 50.0
    above = sum(1 for m in mas if price >= m)
    ma_score = above / len(mas) * 100
    macd = t.get("macd") or 0
    rsi = t.get("rsi12") or 50
    s = ma_score * 0.7 + (15 if macd > 0 else 0) + (rsi - 50) * 0.3
    return clamp(s)


def score_sentiment(b):
    """市场情绪温度（涨停相对跌停的「热度」 gauge）：单调升温 + 独立过热 flag。

    设计修正（重点 / 修复 14 分异常偏低）：
      旧逻辑对「涨停/(涨停+跌停) 比 X」套用与广度相同的抛物线 4·X·(1-X)·100，
      该曲线在 X→1（涨停绝对占优）时塌陷到 0，于是「79 涨停 / 5 跌停」这种强多头日
      被算成 ~12 分（与广度 76% 上涨、市场明显转暖的实况严重矛盾）；且 lu>=80 的硬阈值
      造成 79→12、80→20 的断崖。
      现改为：情绪温度随涨停相对优势单调升温——
        X = 涨停 /(涨停 + 跌停 + 背景跌停基线)；背景基线≈总量0.8%，避免「5 个真实跌停」
        把比值推到 0.94 的极端（真实跌停偏少时不该比「0 跌停(基线44)」更极端）。
        温度 = 10 + X^0.7 × 85   （X=0 冰点10 / X=0.5 中性55 / X→1 高温95）
      过热（盛极而衰）作为**独立 flag** 处理（百股涨停 lu≥100），不再把分数压到 20，
      避免「温度高」与「预警」自相矛盾；预警改由 overheat + 诊断体现。
      广度修正：普跌环境涨停多为局部热点，温度打折（避免「普跌却情绪热」）。
    返回 (score, detail, overheat, label)。
    """
    lu = b.get("limit_up", 0) or 0
    ld = b.get("limit_down", 0) or 0
    total = b.get("total", 0) or 5000
    if lu == 0 and ld == 0:
        return 50.0, {"涨停": 0, "跌停": 0, "涨跌停比": 0.0, "情绪定性": "无明显涨跌停"}, False, "中性"
    # 背景跌停基线：≈总量0.8%，作为分母垫底，避免极少/零跌停把比值推到极端
    baseline = max(1, round(total * 0.008))
    denom = lu + ld + baseline
    x = lu / denom if denom > 0 else 0.5
    # 情绪温度：随涨停相对优势单调升温（不再用峰值在 0.5 的抛物线）
    temp = 10 + (x ** 0.7) * 85
    # 过热（盛极而衰）独立 flag：百股涨停级视为全局亢奋
    overheat = lu >= 100
    if overheat:
        status = "🔥 情绪极度亢奋（百股涨停），危险！"
    elif lu >= 60:
        status = "🚀 涨停潮，情绪高涨"
    elif x < 0.12:
        status = "💀 情绪极度恐慌，冰点"
    elif x < 0.3:
        status = "偏弱·偏冷"
    else:
        status = "✅ 情绪正常偏暖"
    # ---- 广度修正：结合整体涨跌家数，避免「普跌却情绪热」 ----
    up = b.get("up", 0) or 0
    up_ratio = up / total if total else 0.5
    if up_ratio < 0.35:
        temp = clamp(temp * 0.7)
        status = "⚠️ 结构性偏热，但市场普跌（涨股比{:.0f}%）".format(up_ratio * 100)
    elif up_ratio < 0.45:
        temp = clamp(temp * 0.85)
        status = "⚠️ 涨跌停结构偏热，市场偏弱（涨股比{:.0f}%）".format(up_ratio * 100)
    elif up_ratio > 0.65:
        temp = clamp(min(temp * 1.05, 95))
    detail = {
        "涨停": lu,
        "跌停": ld,
        "涨跌停比(原始)": round(lu / (lu + ld) if ld > 0 else x, 2),
        "情绪热度X": round(x, 2),
        "情绪定性": status,
    }
    return clamp(temp), detail, overheat, status


def band_label(score):
    if score >= 80:
        return "强势"
    if score >= 60:
        return "偏强"
    if score >= 40:
        return "中性"
    if score >= 20:
        return "偏弱"
    return "极弱"


def position_from_score(score):
    """综合评分 → 动态仓位系数（绝对映射表，非线性）。

    综合评分区间   对应仓位   市场定性    核心操作建议（口诀）
    80 ~ 100 分    100%      强势主升    "贪婪持股"
    60 ~ 79 分     80%       偏强操作    "顺势加码"
    40 ~ 59 分     60%       震荡灵活    "高抛低吸"
    20 ~ 39 分     30%       偏弱防守    "只卖不买"
    0 ~ 19 分      0%        极端风险    "强制休息"
    返回 (position_pct, 市场定性, 口诀)。
    """
    if score >= 80:
        return 100, "强势主升", "贪婪持股"
    if score >= 60:
        return 80, "偏强操作", "顺势加码"
    if score >= 40:
        return 60, "震荡灵活", "高抛低吸"
    if score >= 20:
        return 30, "偏弱防守", "只卖不买"
    return 0, "极端风险", "强制休息"


def compute(raw, ts=None, icepoint=None):
    b = raw.get("breadth", {})
    v = raw.get("volume", {})
    tech = raw.get("technical", {})
    top = raw.get("sector_top", [])
    bottom = raw.get("sector_bottom", [])
    rr = raw.get("sector_rising_ratio")
    # 主力资金维度优先使用东方财富「板块主力净流入 TOP」真实净额；否则回退到涨幅榜动能代理
    fund_raw = raw.get("sector_fundflow")
    if fund_raw:
        ftop, fbottom = fund_raw[:3], fund_raw[-3:]
    else:
        ftop, fbottom = top, bottom

    b_score, b_detail, b_overheat, b_label = score_breadth(b)
    # ---- 冰点逆修正（Contrarian Reversal）----
    # 连续 N 天广度冰点（上涨比例<15%）时，逆势上调广度分，防范"跌过头却永不抄底"。
    ip = icepoint or {}
    if ip.get("active") and ip.get("level", 0) >= 2:
        b_score = float(ip.get("corrected_score", b_score))
        b_label = "❄️ 冰点逆修正·抄底窗口"
        b_detail["市场状态"] = ip.get("note", b_detail.get("市场状态"))
        b_detail["计分模型"] = "冰点逆修正(Contrarian Reversal)"
        b_detail["冰点修正"] = ip.get("note")
    elif ip.get("active") and ip.get("level", 0) == 1:
        b_detail["冰点修正"] = ip.get("note")
        b_detail["计分模型"] = "冰点（首日·不抄底）"
    s_score, s_detail, s_overheat, s_label = score_sentiment(b)
    # 广度（上涨家数占比）：供量能/资金维度做"普跌修正"，避免弱市下假高分
    up_ratio = (b.get("up", 0) or 0) / ((b.get("total", 0) or 1) or 1)
    # 当前盘中时点（从 ts 解析；ts 为空则用北京时间）
    hhmm = (ts[11:16] if (ts and len(ts) >= 16) else _now_hhmm())
    v_score, v_detail = score_volume(v, up_ratio, hhmm=hhmm)
    dims = {
        "breadth": round(b_score, 1),
        "volume": round(v_score, 1),
        "sector": round(score_sector(top, rr), 1),
        "fund": round(score_fund(ftop, fbottom, up_ratio), 1),
        "trend": round(score_trend(tech), 1),
        "sentiment": round(s_score, 1),
    }
    # 全局高潮预警：广度过热(涨跌比>80%普涨) 或 情绪过热(涨停≥80且涨跌停比>0.8)。
    # 两处阈值已在各自评分函数内封口，这里直接取或，避免日常误报。
    overheat = bool(b_overheat or s_overheat)
    if overheat:
        market_status = "🔥 高潮预警 · 警惕退潮"
    elif b_detail.get("x", 0.5) > 0.7:
        market_status = "偏热"
    elif b_detail.get("x", 0.5) < 0.2:
        market_status = "冰点"
    else:
        market_status = "正常"
    # 冰点逆修正优先于通用"冰点"标签，体现"物极必反"定性与抄底窗口
    if ip.get("active"):
        market_status = ip.get("note", market_status)

    total = sum(dims[k] * WEIGHTS[k] for k in WEIGHTS)
    total = round(total, 1)

    # 综合评分 → 动态仓位系数（绝对映射表，非线性阶梯）
    position, label, verdict = position_from_score(total)

    diags = []
    if dims["volume"] < 50:
        pace = v_detail.get("量能节奏_pace")
        if pace is not None:
            diags.append("量能节奏为预期的{:.0f}%，盘中缩量，反弹持续性存疑".format(pace * 100))
        else:
            diags.append("量能偏弱，反弹持续性存疑")
    if dims["trend"] < 45:
        diags.append("指数低于中长期均线，中期趋势仍偏弱")
    if top:
        diags.append("主线集中于{}（{:.2f}%），注意板块轮动风险".format(top[0]["name"], top[0]["zdf"] or 0))
    if dims["breadth"] >= 80:
        ur = (b.get("up", 0) / (b.get("total", 1) or 1)) * 100
        diags.append("普涨格局（涨股比{:.0f}%），情绪高涨".format(ur))
    if overheat:
        diags.append("⚠️ 市场情绪进入高潮区（涨跌比/涨跌停比极端），系统已自动降权防范退潮，建议降低仓位")
    if ip.get("active"):
        diags.append(ip.get("message", "市场进入冰点状态，注意风险控制"))
    if not diags:
        diags.append("各项指标均衡，按仓位系数操作即可")

    if ts is None:
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    dimensions_out = {}
    label_overrides = {"breadth": b_label, "sentiment": s_label}
    for k in dims:
        dimensions_out[k] = {"score": dims[k], "label": label_overrides.get(k, band_label(dims[k]))}

    sectors_top = [
        {"name": t.get("name"), "zdf": t.get("zdf"), "zljlr": t.get("zljlr")}
        for t in top[:5]
    ]

    # 数据来源标注：广度来自实时接口（东财或通达信）则如实标注
    bs = raw.get("breadth_source")
    if bs in ("eastmoney_realtime", "tongdaxin_realtime"):
        src_name = "东方财富" if bs == "eastmoney_realtime" else "通达信"
        source = "腾讯自选股 MCP + {}实时广度".format(src_name)
        breadth_realtime = True
    else:
        source = "腾讯自选股 MCP (westock-mcp)"
        breadth_realtime = False

    bd = dict(b)
    bd.update({
        "上涨比例": b_detail.get("上涨比例"),
        "涨跌停比": s_detail.get("涨跌停比"),
        "市场状态": market_status,
        "计分模型": b_detail.get("计分模型"),
        "情绪定性": s_detail.get("情绪定性"),
        "overheat": overheat,
    })

    # 时段切分规则说明（每个维度实际使用的盘中/日频口径），便于核对正确性
    methodology = {
        "breadth": {
            "数据源": "新浪全市场分页(实时)",
            "时段口径": "当前时点涨跌家数快照（截至当前时刻实时状态，非全天累计）",
            "说明": "每次刷新即为当时真实涨跌分布，已是盘中实时值",
        },
        "volume": {
            "数据源": "新浪两市累计成交额(实时累加)",
            "时段口径": "当前时点累计成交额 ÷ 该时点预期累计占比（{} 时点预期占比 {:.1f}%）".format(
                hhmm, _expected_share(hhmm) * 100),
            "说明": "用盘中时点匹配的累计占比归一化，解决上午被全天阈值压低的问题",
        },
        "sector": {
            "数据源": "新浪行业板块(实时)",
            "时段口径": "当前时点行业板块涨幅 + 全行业实时上涨占比",
            "说明": "板块强弱与上涨面均为盘中实时",
        },
        "fund": {
            "数据源": "东方财富板块资金流(主力净流入 f62) 优先；不可用时回退板块涨幅动能代理",
            "时段口径": "当前时点行业板块主力净流入 TOP 净额(亿元)降序",
            "说明": "盘中实时；净流入为正→资金加分，为负→扣分",
        },
        "trend": {
            "数据源": "上证日K(腾讯)",
            "时段口径": "日频（收盘价含今日进行中K线，现价实时）",
            "说明": "趋势为慢变量，盘中随现价微调，不做日内累计换算",
        },
        "sentiment": {
            "数据源": "涨跌停家数(实时)",
            "时段口径": "当前时点涨停/跌停家数快照 + 背景跌停基线归一 + 广度修正",
            "说明": "情绪温度随涨停相对优势单调升温（非峰值在0.5的抛物线）；过热(百股涨停)独立标记，不压分；盘中实时",
        },
    }

    return {
        "updated_at": ts,
        "trading": True,
        "caliber": "intraday_realtime",
        "score": total,
        "position_pct": position,
        "position_label": label,
        "verdict": verdict,
        "dimensions": dimensions_out,
        "indices": raw.get("indices", []),
        "sectors_top": sectors_top,
        "sectors_rank": raw.get("sector_rank", []),
        "diagnostics": diags,
        "breadth_detail": bd,
        "breadth_realtime": breadth_realtime,
        "overheat": overheat,
        "market_status": market_status,
        "icepoint": ip if ip else {"active": False},
        "source": source,
        "volume_detail": v_detail,
        "methodology": methodology,
    }


if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 3:
        with open(sys.argv[1], "r", encoding="utf-8") as f:
            ov = json.load(f)
        with open(sys.argv[2], "r", encoding="utf-8") as f:
            sec = json.load(f)
        raw = extract_overview(ov)
        raw.update(extract_sector(sec))
        out = compute(raw)
        print(json.dumps(out, ensure_ascii=False, indent=2))
