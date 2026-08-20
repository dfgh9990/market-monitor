#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""评分引擎单元测试：用模拟数据验证六维→综合→仓位映射正确（与网络无关）"""
import server as S

def build_quotes(up_n, down_n, flat_n=29, total_amount_yi=20794):
    """构造 total_amount_yi 亿元成交额、up_n 只上涨、down_n 只下跌的行情"""
    quotes = []
    n = up_n + down_n + flat_n
    for i in range(up_n):
        quotes.append({'code': f'U{i}', 'name': f'涨{i}', 'price': 10, 'pct': 1.5, 'amount': 0})
    for i in range(down_n):
        quotes.append({'code': f'D{i}', 'name': f'跌{i}', 'price': 10, 'pct': -1.5, 'amount': 0})
    for i in range(flat_n):
        quotes.append({'code': f'F{i}', 'name': f'平{i}', 'price': 10, 'pct': 0.0, 'amount': 0})
    per = (total_amount_yi * 1e8) / n if n else 0
    for q in quotes:
        q['amount'] = per
    return quotes, n

def build_sectors(inflow_n, total_main_yi, super_yi):
    sectors = []
    for i in range(inflow_n):
        sectors.append({'code': f'S{i}', 'name': f'板块{i}', 'price': 1, 'pct': 2.5,
                        'amount': 0, 'main': (total_main_yi*1e8)/max(inflow_n,1),
                        'super': (super_yi*1e8)/max(inflow_n,1), 'large': 0, 'up': 20, 'down': 5})
    for i in range(30 - inflow_n):
        sectors.append({'code': f'W{i}', 'name': f'弱板块{i}', 'price': 1, 'pct': -1.0,
                        'amount': 0, 'main': -(total_main_yi*1e8)/max(30-inflow_n,1)*0.3,
                        'super': -(super_yi*1e8)/max(30-inflow_n,1)*0.3, 'large': 0, 'up': 5, 'down': 20})
    return sectors

def run(label, quotes, indices, sectors):
    s_b, d_b = S.calc_breadth(quotes)
    s_v, d_v = S.calc_volume(quotes)
    s_s, d_s = S.calc_sector(sectors)
    s_f, d_f = S.calc_fund_flow(sectors)
    s_r, d_r = S.calc_rsrs(indices)
    s_m, d_m = S.calc_sentiment(d_b)
    scores = {'breadth': round(s_b,1),'volume': round(s_v,1),'sector': round(s_s,1),
              'fund': round(s_f,1),'rsrs': round(s_r,1),'sentiment': round(s_m,1)}
    total = round(sum(scores[k]*S.WEIGHTS[k] for k in S.WEIGHTS),1)
    pos, status = S.compute_position(total)
    print(f'\n【{label}】')
    for k in S.WEIGHTS:
        print(f'  {k:9s} {scores[k]:6.1f}  (权{S.WEIGHTS[k]})')
    print(f'  综合评分 = {total}  →  仓位 {pos}%  [{status}]')
    return total, pos

print('=' * 56)
print(' 评分引擎单元测试（模拟数据，验证逻辑，与网络无关）')
print('=' * 56)

# 场景1：弱势（8/20 真实盘面特征）：普跌、主力流出
q, n = build_quotes(449, 5069, total_amount_yi=20794)
idx = [{'name':'上证指数','code':'000001','price':3903,'pct':0.24,'amount':1e11},
       {'name':'沪深300','code':'000300','price':4592,'pct':0.09,'amount':5e10}]
sec = build_sectors(inflow_n=5, total_main_yi=-390, super_yi=-120)
run('场景1 · 弱势（普跌+主力流出）', q, idx, sec)

# 场景2：强势：普涨、放量、主力双流入
q2, n2 = build_quotes(4200, 800, total_amount_yi=15000)
idx2 = [{'name':'上证指数','code':'000001','price':3903,'pct':1.8,'amount':1e11},
        {'name':'沪深300','code':'000300','price':4592,'pct':2.1,'amount':5e10}]
sec2 = build_sectors(inflow_n=28, total_main_yi=+350, super_yi=+200)
run('场景2 · 强势（普涨+放量+双流入）', q2, idx2, sec2)

# 场景3：震荡：涨跌各半、量能中性、资金分歧
q3, n3 = build_quotes(2600, 2700, total_amount_yi=5000)
idx3 = [{'name':'上证指数','code':'000001','price':3903,'pct':0.05,'amount':1e11},
        {'name':'沪深300','code':'000300','price':4592,'pct':-0.02,'amount':5e10}]
sec3 = build_sectors(inflow_n=15, total_main_yi=+20, super_yi=-10)
run('场景3 · 震荡（涨跌各半+资金分歧）', q3, idx3, sec3)

print('\n' + '=' * 56)
print(' 边界映射自检')
print('=' * 56)
for t in [85, 65, 50, 30, 5]:
    p, s = S.compute_position(t)
    print(f'  评分 {t:3d}  → 仓位 {p}%  [{s}]')
