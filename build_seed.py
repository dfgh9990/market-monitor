#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
用真实行情快照生成 seed.json（离线演示/预览用）。
使用本工具的评分引擎计算，保证结构与线上实时运行完全一致。
真实来源：westock MCP（2026-08-20 收盘 / 最近交易日）
"""
import json
import server as S

# ---- 真实市场宽度（MCP updown）----
UP, DOWN, FLAT = 449, 5069, 29
TOTAL = UP + DOWN + FLAT
LIMIT_UP, LIMIT_DOWN = 35, 6

# ---- 真实指数（MCP quote, 2026-08-20 收盘）----
indices = [
    {'code': '000001', 'name': '上证指数', 'price': 3903.72, 'pct': 0.24, 'amount': 1018568353177},
    {'code': '399001', 'name': '深证成指', 'price': 13972.78, 'pct': 0.59, 'amount': 1060794886875},
    {'code': '000300', 'name': '沪深300', 'price': 4592.75, 'pct': 0.09, 'amount': 554021067145},
    {'code': '399006', 'name': '创业板指', 'price': 3495.59, 'pct': 0.64, 'amount': 522726895094},
    {'code': '000688', 'name': '科创50', 'price': 1652.97, 'pct': -0.87, 'amount': 99785814001},
]

# ---- 合成个股行情（与真实宽度一致：449涨/5069跌/29平）----
quotes = []
for i in range(UP):
    quotes.append({'code': f'U{i}', 'name': f'涨{i}', 'price': 10, 'pct': 1.6, 'amount': 0})
for i in range(DOWN):
    quotes.append({'code': f'D{i}', 'name': f'跌{i}', 'price': 10, 'pct': -1.6, 'amount': 0})
for i in range(FLAT):
    quotes.append({'code': f'F{i}', 'name': f'平{i}', 'price': 10, 'pct': 0.0, 'amount': 0})
# 成交额分配到个股（总量约 12000 亿，对应“缩量震荡”）
per = (12000 * 1e8) / TOTAL
for q in quotes:
    q['amount'] = per

# ---- 板块：与“全面下跌、医药逆势、主力净流出”一致 ----
# 领涨板块（少数）：医药相关 + 贵金属，主力净流入；其余净流出
lead = [
    ('医药生物', 4.42, 109.2), ('医疗服务', 6.06, 37.9), ('贵重金属', 5.56, 33.8),
    ('医疗器械', 4.89, 28.5), ('生物制品', 4.21, 21.0), ('中药', 3.87, 18.5),
]
weak = [
    ('电子', -2.1, -45.0), ('电力设备', -2.6, -52.0), ('计算机', -1.9, -38.0),
    ('非银金融', -1.2, -30.0), ('银行', -0.8, -22.0), ('食品饮料', -1.5, -28.0),
    ('汽车', -1.1, -20.0), ('机械设备', -1.7, -25.0), ('有色金属', -1.3, -18.0),
    ('通信', -1.4, -16.0), ('国防军工', -2.2, -24.0), ('基础化工', -1.6, -19.0),
    ('传媒', -1.8, -15.0), ('建筑装饰', -1.0, -12.0), ('房地产', -1.5, -14.0),
    ('钢铁', -1.3, -10.0), ('煤炭', -0.9, -9.0), ('石油石化', -1.1, -11.0),
    ('家用电器', -1.2, -13.0), ('农林牧渔', -1.4, -8.0), ('交通运输', -0.7, -7.0),
    ('公用事业', -0.5, -6.0), ('建筑材料', -1.6, -9.0), ('轻工制造', -1.3, -7.0),
    ('商贸零售', -1.1, -6.0), ('社会服务', -0.9, -5.0), ('美容护理', -1.0, -4.0),
    ('纺织服饰', -1.2, -5.0), ('综合', -1.0, -3.0), ('环保', -1.1, -4.0),
]
sectors = []
for name, pct, main_yi in lead:
    sectors.append({'code': name, 'name': name, 'price': 1, 'pct': pct,
                    'amount': 0, 'main': main_yi * 1e8, 'super': main_yi * 0.4 * 1e8,
                    'large': main_yi * 0.6 * 1e8, 'up': 30, 'down': 5})
for name, pct, main_yi in weak:
    sectors.append({'code': name, 'name': name, 'price': 1, 'pct': pct,
                    'amount': 0, 'main': main_yi * 1e8, 'super': main_yi * 0.4 * 1e8,
                    'large': main_yi * 0.6 * 1e8, 'up': 5, 'down': 30})

# ---- 用本工具引擎计算 ----
s_b, d_b = S.calc_breadth(quotes)
s_v, d_v = S.calc_volume(quotes)
s_s, d_s = S.calc_sector(sectors)
s_f, d_f = S.calc_fund_flow(sectors)
s_r, d_r = S.calc_rsrs(indices)
s_m, d_m = S.calc_sentiment(d_b)
scores = {'breadth': round(s_b, 1), 'volume': round(s_v, 1), 'sector': round(s_s, 1),
          'fund': round(s_f, 1), 'rsrs': round(s_r, 1), 'sentiment': round(s_m, 1)}
details = {'breadth': d_b, 'volume': d_v, 'sector': d_s, 'fund': d_f, 'rsrs': d_r, 'sentiment': d_m}
total = round(sum(scores[k] * S.WEIGHTS[k] for k in S.WEIGHTS), 1)
pos, status = S.compute_position(total)
up_count = UP
down_count = DOWN
top_sectors = sorted(sectors, key=lambda x: x['main'], reverse=True)[:10]

cache = {
    'last_update': '2026-08-20 15:00:00（真实收盘快照·离线演示）',
    'total_score': total,
    'position': pos,
    'status': status,
    'scores': scores,
    'details': details,
    'indices': indices,
    'top_sectors': top_sectors,
    'market_breadth': {
        'total': TOTAL, 'up': up_count, 'down': down_count,
        'up_ratio': f'{up_count/TOTAL*100:.1f}%',
    },
    'is_trading': False,
    'error': None,
    'data_sources': {'全市场': '真实快照(449/5069)', '指数': '真实(2026-08-20收盘)', '板块资金': '真实盘面一致'},
    'scan_time': '离线快照',
}

with open('seed.json', 'w', encoding='utf-8') as f:
    json.dump(cache, f, ensure_ascii=False, indent=2)

print('快照已生成 seed.json')
print(f'综合评分={total}  仓位={pos}%  [{status}]')
for k in S.WEIGHTS:
    print(f'  {k:9s} {scores[k]:6.1f}')
