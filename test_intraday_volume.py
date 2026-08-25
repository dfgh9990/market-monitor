"""盘中实时口径验证：量能健康度 + 六维输出（合成数据，无需联网）。

验证目标：
  1. 量能健康度改用「当前时点累计占比」归一化后，上午不再被全天阈值压到 0。
  2. 六个维度在盘中各时点输出符合"实时累计"口径（输出含 methodology 字段可核对）。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import score_lib as S

# 模拟一个"温和放量、市场偏强"的交易日：全天预计约 15000 亿（= 基准）
# 给定各时点的"实际累计成交额"，验证 pace 与分数。
SCENARIOS = [
    # (时点, 实际累计成交额_亿, 上涨家数占比 up_ratio)
    ("10:00", 3000, 0.55),
    ("11:00", 6200, 0.55),
    ("13:30", 9500, 0.55),
    ("14:30", 12800, 0.55),
    ("14:55", 14800, 0.55),
    # 缩量场景：同样时点但累计额只有预期的 70%
    ("10:00", 2100, 0.40),
    ("14:30", 9000, 0.40),
]


def build_raw(amount_yi, up_ratio, hhmm):
    total = 5000
    up = int(total * up_ratio)
    down = total - up
    return {
        "breadth": {"up": up, "down": down, "flat": 0, "total": total,
                    "limit_up": 40, "limit_down": 5},
        "volume": {"amount_yi": amount_yi, "source": "public_realtime"},
        "technical": {"price": 3200, "ma5": 3180, "ma10": 3160, "ma20": 3120,
                      "ma60": 3050, "ma250": 2900, "macd": 12, "rsi12": 58},
        "indices": [{"name": "上证指数", "code": "000001", "close": 3200, "chg_pct": 0.6}],
        "sector_top": [{"name": "半导体", "zdf": 3.2, "zljlr": None},
                       {"name": "军工", "zdf": 2.1, "zljlr": None}],
        "sector_bottom": [{"name": "银行", "zdf": -0.5, "zljlr": None}],
        "sector_rank": [],
        "sector_rising_ratio": 0.62,
        "breadth_source": "sina_public_realtime",
    }


def old_volume_score(amount_yi):
    """复刻旧逻辑（全天阈值 1.2~2.2 万亿），仅用于对比演示。"""
    base = (amount_yi - 12000) / (22000 - 12000) * 100
    return max(0, min(100, base))


print("=" * 78)
print("量能健康度：旧逻辑(全天阈值) vs 新逻辑(盘中时点归一化) 对比")
print("=" * 78)
print("{:6} {:>10} {:>8} {:>10} {:>10} {:>8}".format(
    "时点", "累计额(亿)", "旧分数", "新分数", "预期占比", "pace"))
for hhmm, amt, ur in SCENARIOS:
    raw = build_raw(amt, ur, hhmm)
    ts = "2026-08-25 " + hhmm
    out = S.compute(raw, ts=ts)
    v = out["volume_detail"]
    print("{:6} {:>10} {:>8.1f} {:>10.1f} {:>9} {:>7.2f}".format(
        hhmm, amt, old_volume_score(amt), out["dimensions"]["volume"]["score"],
        v["预期累计占比"], v["量能节奏_pace"]))

print()
print("=" * 78)
print("六维输出示例（时点 14:30，累计 12800 亿，偏强市）")
print("=" * 78)
raw = build_raw(12800, 0.55, "14:30")
out = S.compute(raw, ts="2026-08-25 14:30")
print("综合评分:", out["score"], "仓位:", out["position_pct"], "%",
      out["position_label"], "/", out["verdict"])
print("数据口径:", out["caliber"])
print()
for k, v in out["dimensions"].items():
    print("  {:10} 分数 {:5}  标签 {}".format(k, v["score"], v["label"]))
print()
print("量能明细:", out["volume_detail"])
print()
print("时段切分规则(methodology):")
for k, m in out["methodology"].items():
    print("  [{}] {}".format(k, m["时段口径"]))
