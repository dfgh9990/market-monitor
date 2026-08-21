"""通达信三大指数实时快照落盘：读取 tdx_quotes 原始响应，生成 indices_raw.json。

用法（由自动化 agent 调用；agent 负责用 tdx_quotes 取数，本脚本只负责解析落盘）：
  python tdx_indices.py indices_raw_mcp.json

输入 indices_raw_mcp.json：agent 保存的 tdx_quotes 原始返回（支持纯 JSON / markdown 包裹 ```json 块）。
需包含 4 个指数（缺一可容错，缺失的指数在输出中跳过）：
  000001 上证指数  (setcode=1)
  399001 深证成指  (setcode=0)
  399006 创业板指  (setcode=0)
  399106 深证综指  (setcode=0)  ← 用于两市成交额（深市全部口径，与深证成指同源）

输出 indices_raw.json：
{
  "fetched_at": "YYYY-MM-DD HH:MM",
  "indices": [ {code,name,close,prev_close,chg_pct,amount_yi,hsl,pe,zs_yi}, ... ]  // 上证/深证成指/创业板指
  "market_amount_yi": 18792.6,   // 两市成交额(亿) = 上证Amount + 深证综指Amount
  "source": "tongdaxin_realtime"
}
"""
import json
import sys
import re
import os
from datetime import datetime

# 期望的指数清单（显示用三大指数 + 两市成交额用深证综指）
TARGETS = [
    {"code": "000001", "name": "上证指数"},
    {"code": "399001", "name": "深证成指"},
    {"code": "399006", "name": "创业板指"},
    {"code": "399106", "name": "深证综指"},
]


def _f(v, d=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def extract_json_blocks(raw_text):
    """从文本中提取 JSON：直接解析 → ```json 块 → 首个 { 到末尾 }。返回 list[dict]。"""
    text = raw_text.strip()
    # 1) 直接解析
    try:
        d = json.loads(text)
        return d
    except Exception:
        pass
    # 2) ```json ... ``` 块
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    # 3) 首个 { 到最后一个 }
    i, j = text.find("{"), text.rfind("}")
    if 0 <= i < j:
        try:
            return json.loads(text[i:j + 1])
        except Exception:
            pass
    return None


def parse_quotes(data):
    """把 tdx_quotes 的原始返回解析为 {code: 结构化行情}。"""
    items = []
    if isinstance(data, dict):
        if data.get("ok") is True and isinstance(data.get("data"), list):
            items = data["data"]
        elif isinstance(data.get("data"), dict):
            items = [data["data"]]
        elif "HQInfo" in data:
            items = [data]
    elif isinstance(data, list):
        items = data
    out = {}
    for it in items:
        if not isinstance(it, dict):
            continue
        base = it.get("BaseInfo") or {}
        hq = it.get("HQInfo") or {}
        ext = it.get("ExtInfo") or {}
        code = str(base.get("Code") or "")
        name = str(base.get("Name") or "")
        if not code:
            continue
        now = _f(hq.get("Now"))
        prev = _f(hq.get("Close"))
        amt = _f(hq.get("Amount"))
        out[code] = {
            "code": code,
            "name": name,
            "close": now,
            "prev_close": prev,
            "chg_pct": round((now - prev) / prev * 100, 2) if (now is not None and prev) else None,
            "amount_raw": amt,
            "amount_yi": round(amt / 1e8, 1) if amt is not None else None,
            "hsl": _f(hq.get("HSL")),
            "pe": _f(ext.get("SYL")),
            "zs_yi": round(_f(ext.get("ZSZ")) / 1e8, 1) if _f(ext.get("ZSZ")) is not None else None,
        }
    return out


def main():
    if len(sys.argv) < 2:
        print("usage: python tdx_indices.py indices_raw_mcp.json")
        sys.exit(1)
    src = sys.argv[1]
    if not os.path.exists(src):
        print("❌ 未找到输入文件:", src)
        sys.exit(1)
    with open(src, "r", encoding="utf-8") as f:
        raw_text = f.read()
    parsed = extract_json_blocks(raw_text)
    if parsed is None:
        print("❌ 无法从输入中解析 JSON")
        sys.exit(1)
    quotes = parse_quotes(parsed)
    if not quotes:
        print("❌ 未解析到任何行情（检查 tdx_quotes 返回结构）")
        sys.exit(1)

    indices = []
    for t in TARGETS[:3]:  # 显示用三大指数
        q = quotes.get(t["code"])
        if not q:
            continue
        indices.append({
            "code": q["code"],
            "name": q["name"],
            "close": q["close"],
            "prev_close": q["prev_close"],
            "chg_pct": q["chg_pct"],
            "amount_yi": q["amount_yi"],
            "hsl": q["hsl"],
            "pe": q["pe"],
            "zs_yi": q["zs_yi"],
        })

    # 两市成交额：上证 + 深证综指（深市全部口径）
    sh = quotes.get("000001")
    sz = quotes.get("399106")
    market_amount = None
    if sh and sz and sh.get("amount_yi") is not None and sz.get("amount_yi") is not None:
        market_amount = round(sh["amount_yi"] + sz["amount_yi"], 1)

    out = {
        "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "indices": indices,
        "market_amount_yi": market_amount,
        "source": "tongdaxin_realtime",
    }
    with open("indices_raw.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("✅ 已写入 indices_raw.json：指数 {} 个 | 两市成交额 {} 亿".format(
        len(indices), market_amount if market_amount is not None else "—"))


if __name__ == "__main__":
    main()
