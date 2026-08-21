"""用通达信 tdx_screener 的实时涨跌家数生成 breadth_raw.json。

用法（由自动化 agent 调用；agent 负责用 tdx_screener 取数，本脚本只负责落盘）：
  python tdx_breadth.py <up> <down> <flat> <limit_up> <limit_down>

参数即 tdx_screener 各 message 查询返回的 meta.total：
  - 上涨 -> up
  - 下跌 -> down
  - 平盘 -> flat
  - 涨停 -> limit_up
  - 跌停 -> limit_down

输出 breadth_raw.json（覆盖写入），source=tongdaxin_realtime。
落盘前做一致性校验：total = up+down+flat，且 涨停/跌停 必须分别是 上涨/下跌 的子集；
若不满足则报错退出，不写脏数据（pipeline 会回退到日线广度，页面不会崩）。
"""
import sys
import json
import os
from datetime import datetime


def main():
    if len(sys.argv) < 6:
        print("usage: python tdx_breadth.py <up> <down> <flat> <limit_up> <limit_down>")
        sys.exit(1)

    try:
        up = int(sys.argv[1])
        down = int(sys.argv[2])
        flat = int(sys.argv[3])
        limit_up = int(sys.argv[4])
        limit_down = int(sys.argv[5])
    except ValueError:
        print("❌ 参数必须是整数（来自 tdx_screener 的 meta.total）")
        sys.exit(1)

    if up < 0 or down < 0 or flat < 0 or limit_up < 0 or limit_down < 0:
        print("❌ 涨跌家数不能为负")
        sys.exit(1)

    total = up + down + flat
    if total < 4000:
        # A股全市场常态 >5000 只，低于此值说明 tdx_screener 返回被截断/异常
        print("❌ 总数 {} 异常（应 >4000），疑似 tdx_screener 返回被截断，拒绝写入".format(total))
        sys.exit(1)

    if limit_up > up or limit_down > down:
        print("⚠️ 子集校验异常：涨停({})>上涨({}) 或 跌停({})>下跌({})，可能口径不一致".format(
            limit_up, up, limit_down, down))
        sys.exit(1)

    out = {
        "up": up,
        "down": down,
        "flat": flat,
        "total": total,
        "limit_up": limit_up,
        "limit_down": limit_down,
        "source": "tongdaxin_realtime",
        "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    with open("breadth_raw.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("✅ 已写入 breadth_raw.json：上涨{} 下跌{} 平盘{} 涨停{} 跌停{} 总计{}（通达信实时广度）".format(
        up, down, flat, limit_up, limit_down, total))


if __name__ == "__main__":
    main()
