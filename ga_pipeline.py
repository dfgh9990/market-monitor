"""ga_pipeline.py — GitHub Actions 版「盘中市场强弱监控」取数+评分（纯标准库，零 MCP 依赖）。

数据源（公开 HTTP 接口，云端/任意网络可达，已实测）：
  1. 新浪 Market_Center.getHQNodeData 分页 → 全市场 5548 只：涨跌家数/涨停跌停/两市成交额/涨停列表
  2. 腾讯 qt.gtimg.cn                          → 三大指数实时行情
  3. 腾讯 web.ifzq.gtimg.cn 日K                 → 指数均线/MACD/RSI（趋势维度）；连板天数判断
  4. 新浪 newSinaHy（GBK）                      → 行业板块涨幅 TOP（板块资金集中度维度）
  5. 东方财富 push2delay.eastmoney.com          → 行业板块主力净流入 TOP（f62 主力净流入，按金额降序；
                                               板块资金流向维度的真实数据来源，失败则优雅降级为涨幅动能代理）

用法（在仓库根目录，GitHub Actions 中由 workflow 调用）：
  python ga_pipeline.py
  读取同目录 data.json（旧快照，可能不存在）→ 生成新 data.json + 更新 temperature_history.json / icepoint_state.json
"""
import json
import os
import sys
import re
import time
import datetime
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from score_lib import compute, compute_range_warning

UA = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://vip.stock.finance.sina.com.cn/mkt/",
}
SINA_HQ = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
TENCENT_QT = "https://qt.gtimg.cn/q="
TENCENT_KLINE = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param="
# 东方财富板块资金流（行业板块主力净流入）。push2delay 镜像实测可用；push2 作兜底。
EM_HOSTS = ["push2delay.eastmoney.com", "push2.eastmoney.com"]

# ---------------- HTTP 工具 ----------------

def fetch_bytes(url, headers=None, timeout=20):
    req = urllib.request.Request(url, headers=headers or UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def fetch_json(url, timeout=20):
    raw = fetch_bytes(url, timeout=timeout)
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        # 新浪 Market_Center 返回 charset=gbk
        return json.loads(raw.decode("gbk", "ignore"))


def fetch_gbk(url, timeout=20):
    raw = fetch_bytes(url, timeout=timeout)
    return raw.decode("gbk", "ignore")


def _f(v, d=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


# ---------------- 1. 新浪全市场分页：涨跌家数 ----------------

def limit_threshold(symbol, name):
    """按代码/名称推断涨停阈值（%）：北交所30 / 创业板科创板20 / ST 5 / 主板10。"""
    if symbol.startswith("bj"):
        return 29.9
    if symbol.startswith(("sz30", "sh688")):
        return 19.9
    if "ST" in (name or "").upper():
        return 4.9
    return 9.9


def fetch_market_breadth():
    """分页抓全 A 股，统计 上涨/下跌/平盘/涨停/跌停/两市成交额，并保留涨停列表（供连板判断）。"""
    up = down = flat = limit_up = limit_down = 0
    amount = 0.0
    limit_stocks = []
    page = 1
    while page <= 80:
        url = "{}Market_Center.getHQNodeData?page={}&num=100&sort=changepercent&asc=0&node=hs_a".format(SINA_HQ, page)
        data = fetch_json(url)
        if not isinstance(data, list) or not data:
            break
        for s in data:
            cp = _f(s.get("changepercent"), 0.0)
            amt = _f(s.get("amount"), 0.0)
            symbol = str(s.get("symbol", ""))
            name = str(s.get("name", ""))
            amount += amt
            if cp > 0:
                up += 1
            elif cp < 0:
                down += 1
            else:
                flat += 1
            thr = limit_threshold(symbol, name)
            if cp >= thr:
                limit_up += 1
                limit_stocks.append({"code": symbol, "name": name, "chg": round(cp, 2), "price": _f(s.get("trade"))})
            elif cp <= -thr:
                limit_down += 1
        if len(data) < 100:
            break
        page += 1
        time.sleep(0.15)  # 轻微限速，避免触发风控
    total = up + down + flat
    return {
        "up": up, "down": down, "flat": flat, "total": total,
        "limit_up": limit_up, "limit_down": limit_down,
        "amount_yi": round(amount / 1e8, 1),
        "limit_stocks": limit_stocks,
        "pages": page,
    }


# ---------------- 2. 腾讯指数实时行情 ----------------

def fetch_indices():
    """腾讯 qt.gtimg.cn 三大指数（上证/深证成指/创业板指）。字段 ~ 分割：1名 3现价 4昨收 31涨跌 32涨跌% 37成交额万 38换手 39PE。"""
    url = TENCENT_QT + "sh000001,sz399001,sz399006"
    body = fetch_gbk(url)
    out = []
    for line in body.strip().split(";"):
        if "=" not in line:
            continue
        payload = line.split('="', 1)[-1].strip().strip('"')
        p = payload.split("~")
        if len(p) < 40:
            continue
        code = p[2]
        if code not in ("000001", "399001", "399006"):
            continue
        now = _f(p[3]); prev = _f(p[4]); chg = _f(p[32])
        out.append({
            "code": code,
            "name": p[1],
            "close": now,
            "prev_close": prev,
            "chg_pct": chg,
            "amount_yi": round(_f(p[37], 0) / 10000.0, 1),  # 万元 → 亿
            "hsl": _f(p[38]),
            "pe": _f(p[39]),
        })
    return out


# ---------------- 3. 技术指标（K 线计算） ----------------

def fetch_kline(symbol, n=260):
    """腾讯日K（前复权）。symbol 如 sh000001 / sz002412。返回 [收盘价...] 最新在最后。"""
    url = TENCENT_KLINE + "{},day,,,{},qfq".format(symbol, n)
    data = fetch_json(url)
    node = data.get("data", {}).get(symbol, {})
    k = node.get("qfqday") or node.get("day") or []
    return [(float(x[0].replace("-", "")), float(x[2])) for x in k]  # (date_int, close)


def fetch_kline_full(symbol, n=200):
    """腾讯日K（前复权）全量字段。返回 [dict(date,open,close,high,low,volume)...]，最新在最后。
    用于区间上下沿界定与当日触及判定（区别于仅返回收盘的 fetch_kline）。"""
    url = TENCENT_KLINE + "{},day,,,{},qfq".format(symbol, n)
    data = fetch_json(url)
    node = data.get("data", {}).get(symbol, {})
    k = node.get("qfqday") or node.get("day") or []
    out = []
    for x in k:
        if len(x) < 6:
            continue
        out.append({
            "date": str(x[0]).replace("-", ""),
            "open": _f(x[1]),
            "close": _f(x[2]),
            "high": _f(x[3]),
            "low": _f(x[4]),
            "volume": _f(x[5], 0.0),
        })
    return out


def ema(values, period):
    k = 2.0 / (period + 1)
    e = values[0]
    for v in values[1:]:
        e = v * k + e * (1 - k)
    return e


def calc_indicators(closes):
    """从收盘序列计算 MA/MACD/RSI。"""
    if len(closes) < 60:
        return {}
    def ma(n):
        return sum(closes[-n:]) / n if len(closes) >= n else None
    dif = ema(closes, 12) - ema(closes, 26)
    # DEA = EMA9(DIF 序列)
    difs = []
    e12 = e26 = None
    for v in closes:
        e12 = v if e12 is None else v * 2 / 13 + e12 * 11 / 13
        e26 = v if e26 is None else v * 2 / 27 + e26 * 25 / 27
        difs.append(e12 - e26)
    dea = None
    for d in difs:
        dea = d if dea is None else d * 2 / 10 + dea * 8 / 10
    # RSI12（简单平滑）
    gains = losses = 0.0
    for i in range(len(closes) - 12, len(closes)):
        diff = closes[i] - closes[i - 1]
        if diff > 0:
            gains += diff
        else:
            losses -= diff
    rsi12 = 100 - 100 / (1 + gains / max(losses, 1e-9)) if (gains + losses) > 0 else 50
    return {
        "ma5": ma(5), "ma10": ma(10), "ma20": ma(20), "ma60": ma(60), "ma250": ma(250),
        "macd": (dif - dea) * 2 if dea is not None else None,
        "dif": dif, "dea": dea, "rsi12": rsi12,
    }


def fetch_technical():
    """上证指数 K 线 → price + 技术指标（指数趋势维度）。"""
    kl = fetch_kline("sh000001", 260)
    closes = [c for _, c in kl]
    if not closes:
        return {}
    tech = calc_indicators(closes)
    tech["price"] = closes[-1]
    tech["price_source"] = "public_realtime"
    return tech


# ---------------- 4. 新浪行业板块（涨幅替代资金流） ----------------

def fetch_sectors():
    """新浪 newSinaHy（GBK）→ 49 行业板块涨幅。字段: 0代码 1名称 2股票数 3均价 4涨跌额 5涨跌幅 8领涨股代码 9领涨股涨幅 12领涨股名。"""
    body = fetch_gbk("https://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php")
    m = re.search(r"\{.*\}", body, re.S)
    if not m:
        return [], []
    try:
        data = json.loads(m.group(0))
    except Exception:
        return [], []
    rows = []
    for k, v in data.items():
        if isinstance(v, str):
            v = v.split(",")  # 新浪返回逗号分隔字符串
        if not isinstance(v, list) or len(v) < 13:
            continue
        zdf = _f(v[5])
        rows.append({
            "name": str(v[1]), "zdf": zdf,
            "leader": str(v[12]), "leader_zdf": _f(v[9]),
            "stock_count": _f(v[2]), "amount": _f(v[6]),
        })
    rows.sort(key=lambda r: r["zdf"] or -999, reverse=True)
    # 全行业实时上涨占比（盘中实时口径，替代原硬编码 0.7）：49 个行业中涨幅为正的占比
    rising = [r for r in rows if (r["zdf"] or 0) > 0]
    rising_ratio = round(len(rising) / len(rows), 3) if rows else 0.7
    return rows[:10], rows[-5:], rising_ratio  # top10（涨幅最高）、bottom5（领跌）、全行业实时上涨占比


# ---------------- 4b. 东方财富行业板块主力净流入 TOP ----------------

def fetch_sector_fundflow(topn=20):
    """东方财富行业板块资金流：按主力净流入(f62)降序返回 TOP。

    返回 [{code,name,zdf,net_inflow_yi,net_inflow_pct}]（net_inflow_yi 单位：亿元；净流入为正/流出为负）。
    多镜像兜底：push2delay 实测可用，push2 兜底；全部失败返回 []（调用方据此降级）。
    字段：f12=代码 f14=名称 f3=涨跌幅 f62=主力净流入(元) f184=主力净流入占比(%)。
    """
    q = ("pn=1&pz={}&po=1&np=1&fltt=2&invt=2&fid=f62"
         "&fs=m:90+t:2+f:!50&fields=f12,f14,f3,f62,f184").format(topn)
    last_err = None
    for host in EM_HOSTS:
        url = "https://{}/api/qt/clist/get?{}".format(host, q)
        try:
            data = fetch_json(url, timeout=15)
        except Exception as e:
            last_err = e
            continue
        arr = (data.get("data") or {}).get("diff") or []
        if not arr:
            last_err = "empty diff"
            continue
        out = []
        for a in arr:
            net = _f(a.get("f62"))  # 元
            out.append({
                "code": a.get("f12"),
                "name": a.get("f14"),
                "zdf": _f(a.get("f3")),
                "zljlr": round(net / 1e8, 2) if net is not None else None,  # 元 → 亿
                "net_inflow_pct": _f(a.get("f184")),
            })
        out.sort(key=lambda r: (r["zljlr"] if r["zljlr"] is not None else -1e9), reverse=True)
        return out
    print("  ⚠️ 板块主力净流入获取失败（{}），主力资金维度将沿用涨幅动能代理".format(repr(last_err)[:80]))
    return []


# ---------------- 5. 连板天数（腾讯日K判断） ----------------

def build_limitup(limit_stocks, max_check=None):
    """连板高度计算：对【全部】涨停股查日K，从最新往回数连续涨停天数，按连板天数降序返回。

    修复旧逻辑的两处缺陷：
      1) 旧版只扫描前 30 只涨停股 → 主板高连板股（当日涨幅仅≈10%）会排在北交所(≈30%)/
         创业板科创板(≈20%)之后而被遗漏，导致「最高标」识别错误。
      2) 旧版只取 12 天日K，长连板（>11 板）会数不全。
    现改为扫描全部涨停股、取 40 天日K，阈值沿用 limit_threshold
    （北交所30/创业板科创板20/ST 5/主板10），确保连板高度最高的个股不被漏判。
    """
    results = []
    stocks = limit_stocks if max_check is None else limit_stocks[:max_check]
    for s in stocks:
        code = s["code"]  # 如 sz002412 / sh600519 / bj920093
        try:
            kl = fetch_kline(code, 40)
        except Exception:
            continue
        if len(kl) < 2:
            continue
        thr = limit_threshold(code, s["name"])
        days = 0
        for i in range(len(kl) - 1, 0, -1):
            prev_close = kl[i - 1][1]
            if prev_close <= 0:
                break
            pct = (kl[i][1] - prev_close) / prev_close * 100
            if pct >= thr - 0.5:
                days += 1
            else:
                break
        if days > 0:
            results.append({"code": code[2:], "name": s["name"], "chg": s.get("chg"),
                            "consecutive_days": days, "price": s.get("price")})
        time.sleep(0.04)
    results.sort(key=lambda r: r["consecutive_days"], reverse=True)
    return results


# ---------------- 主流程 ----------------

def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def main():
    work_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(work_dir)

    print("== 1/5 抓取全市场涨跌家数（新浪分页） ==")
    breadth = fetch_market_breadth()
    print("上涨{} 下跌{} 平盘{} 涨停{} 跌停{} 总额{}亿 ({}页)".format(
        breadth["up"], breadth["down"], breadth["flat"],
        breadth["limit_up"], breadth["limit_down"], breadth["amount_yi"], breadth["pages"]))
    if breadth["total"] < 4000:
        print("❌ 全市场总数异常({})，可能被限流，退出".format(breadth["total"]))
        sys.exit(1)

    print("== 2/5 抓取三大指数（腾讯） ==")
    indices = fetch_indices()
    for i in indices:
        print("  {} {:.2f} {}{}%".format(i["name"], i["close"], "+" if i["chg_pct"] >= 0 else "", i["chg_pct"]))

    print("== 3/5 抓取上证K线算技术指标 + 区间位置预警 ==")
    kl_full = fetch_kline_full("sh000001", 200)
    closes = [b["close"] for b in kl_full]
    tech = calc_indicators(closes)
    tech["price"] = closes[-1]
    tech["price_source"] = "public_realtime"
    print("  price={} MA5={:.2f} MA20={:.2f}".format(
        tech.get("price"), tech.get("ma5"), tech.get("ma20")))
    range_w = compute_range_warning(kl_full)
    sw = range_w.get("short") or {}
    mw = range_w.get("medium") or {}
    print("  区间预警(短线33日): {} 位置{}% 下沿{}~上沿{}".format(
        sw.get("signal"), sw.get("position_pct"), sw.get("support"), sw.get("resistance")))
    print("  区间预警(中线99日): {} 位置{}% 下沿{}~上沿{}".format(
        mw.get("signal"), mw.get("position_pct"), mw.get("support"), mw.get("resistance")))
    if range_w.get("summary_signal") and range_w["summary_signal"] != "none":
        print("  综合研判: {} - {}".format(range_w.get("summary_chip"), range_w.get("summary_text")))

    print("== 4/5 抓取行业板块涨幅（新浪） + 板块主力净流入 TOP（东方财富） ==")
    top10, bottom5, sector_rising_ratio = fetch_sectors()
    print("  TOP1: {} {}{}% | 领涨 {}".format(
        top10[0]["name"], "+" if top10[0]["zdf"] >= 0 else "", top10[0]["zdf"], top10[0]["leader"]) if top10 else "  无板块数据")
    sector_fund = fetch_sector_fundflow()
    if sector_fund:
        print("  主力净流入TOP1: {} 净流入{}亿 (占比{}%)".format(
            sector_fund[0]["name"], sector_fund[0]["zljlr"], sector_fund[0]["net_inflow_pct"]))
    else:
        print("  ⚠️ 板块资金流获取失败，主力资金维度将沿用涨幅动能代理")

    print("== 5/5 连板高度判断(全扫描) + 评分 ==")
    limitup = build_limitup(breadth["limit_stocks"])
    print("  连板Top: " + "、".join("{} {}连板".format(s["name"], s["consecutive_days"]) for s in limitup) if limitup else "  无连板")

    # ---- 组装 raw（六维数据） ----
    # 板块主力净流入按名称并入涨幅榜，供主力资金维度在资金流可用时直接使用真实净额
    fund_by_name = {r["name"]: r for r in sector_fund}
    raw = {
        "breadth": {
            "up": breadth["up"], "down": breadth["down"], "flat": breadth["flat"],
            "total": breadth["total"],
            "limit_up": breadth["limit_up"], "limit_down": breadth["limit_down"],
        },
        "volume": {"amount_yi": breadth["amount_yi"], "source": "public_realtime"},
        "technical": tech,
        "indices": [{"name": i["name"], "code": i["code"], "close": i["close"], "chg_pct": i["chg_pct"]} for i in indices],
        "sector_top": [{"name": r["name"], "zdf": r["zdf"],
                        "zljlr": fund_by_name.get(r["name"], {}).get("zljlr"),
                        "leader": r["leader"], "leader_zdf": r["leader_zdf"]} for r in top10],
        "sector_bottom": [{"name": r["name"], "zdf": r["zdf"], "zljlr": None} for r in bottom5],
        "sector_rank": top10,
        "sector_rising_ratio": sector_rising_ratio,
        "sector_fundflow": sector_fund,  # 东方财富真实主力净流入榜（供主力资金维度评分）
        "breadth_source": "sina_public_realtime",
    }

    # ---- 冰点状态：从本地 state 续算 ----
    state = load_json("icepoint_state.json", {"last_date": None, "consecutive_days": 0})
    from pipeline import update_icepoint, save_icepoint_state
    # 统一使用北京时间（GitHub Actions 容器默认 UTC，必须显式 +8）
    bj_now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
    date_str = bj_now.strftime("%Y-%m-%d")
    ip = update_icepoint(raw["breadth"], state, date_str)
    save_icepoint_state("icepoint_state.json", state)

    # ---- 评分 ----
    ts = bj_now.strftime("%Y-%m-%d %H:%M")
    out = compute(raw, ts=ts, icepoint=ip)
    out["breadth_realtime"] = True
    out["source"] = "新浪/腾讯公开接口 (GitHub Actions)"
    # ---- 上证指数区间位置操作预警 ----
    range_w["index_name"] = "上证指数"
    out["range_warning"] = range_w

    # ---- 连板最高标（全扫描 + 腾讯日K连续涨停计数） ----
    if limitup:
        out["limitup"] = {
            "highest": {
                "code": limitup[0]["code"], "name": limitup[0]["name"],
                "consecutive_days": limitup[0]["consecutive_days"],
                "chg": limitup[0]["chg"], "price": limitup[0]["price"],
            },
            "top5": [
                {"code": s["code"], "name": s["name"], "chg": s["chg"],
                 "consecutive_days": s["consecutive_days"], "price": s.get("price")}
                for s in limitup[:5]
            ],
            "total": len(limitup),
            "calc_note": "连板天数由腾讯日K从最新往回连续涨停计数(阈值:北交所30/双创20/ST5/主板10)；"
                         "题材与行业需东方财富涨停池(当前限流)暂未补充，故仅输出可验证的连板高度与天数",
        }

    # ---- 板块主力净流入 TOP（按净流入金额降序） ----
    if sector_fund:
        out["sector_fund_top"] = [
            {"name": r["name"], "zdf": r["zdf"],
             "net_inflow_yi": r["zljlr"], "net_inflow_pct": r.get("net_inflow_pct")}
            for r in sector_fund[:10]
        ]

    # ---- 温度历史（追加） ----
    history = load_json("temperature_history.json", {})
    date = ts[:10]; hhmm = ts[11:16]
    day = [e for e in history.get(date, []) if e.get("time") != hhmm]
    day.append({"time": hhmm, "score": out["score"], "position": out["position_pct"]})
    day.sort(key=lambda e: e["time"])
    history[date] = day
    out["temperature_history"] = day
    with open("temperature_history.json", "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("\n✅ 已写入 data.json：score={} 仓位={}% ({})".format(
        out["score"], out["position_pct"], out["position_label"]))
    return out


if __name__ == "__main__":
    main()
