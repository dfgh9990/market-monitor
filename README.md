# 盘中市场强弱监控 · 动态仓位系数工具

基于六维市场强弱评分（涨跌比广度 / 量能健康度 / 板块资金集中度 / 主力资金流向 / 指数趋势 / 市场情绪温度），
加权合成综合评分并映射成动态仓位系数（0~100%），**盘中每 30 分钟自动刷新**。

数据来源为 **腾讯自选股 MCP（westock-mcp）**，由定时任务抓取真实盘面 → 跑评分引擎 → 生成 `data.json` →
托管到静态站点（Cloudflare Pages / GitHub Pages），任意设备浏览器即可访问。**完全免费、无需信用卡、数据真实**。

## 架构

```
腾讯自选股 MCP ──(定时任务 每30分/交易时段)──> pipeline.py ──> data.json
                                                        │
                                                        ▼
                                              静态站点 (Cloudflare Pages)
                                                        │
                                                        ▼
                                              任意设备浏览器打开 index.html
```

- 云端只托管静态 `data.json` + `index.html`，**不直连行情接口**，彻底规避东方财富对云 IP 的限流
- 取数在 WorkBuddy 会话内完成（腾讯自选股 MCP 真实可用）

## 六维权重

| 维度 | 权重 |
|------|------|
| 涨跌比广度 | 25% |
| 量能健康度 | 15% |
| 板块资金集中度 | 20% |
| 主力资金流向 | 20% |
| 指数趋势 | 10% |
| 市场情绪温度 | 10% |

仓位映射：80-100→强势进攻(80-100%)；60-80→偏强操作(60-80%)；40-60→震荡灵活(40-60%)；
20-40→偏弱防守(20-40%)；0-20→空仓观望(0-20%)。

## 本地预览 / 离线生成

```bash
cd market-monitor
# 用真实快照生成 data.json（首次部署 / 自测）
python make_initial.py
# 启动一个本地静态服务器预览
python -m http.server 8080
# 浏览器打开 http://127.0.0.1:8080
```

仅更新评分（已有 overview_raw.json / sector_raw.json 时）：
```bash
python pipeline.py overview_raw.json sector_raw.json data.json
```

## 部署到 Cloudflare Pages（推荐，国内可达、免费无卡）

1. 打开 https://pages.cloudflare.com ，用 GitHub 登录
2. 新建项目 → 关联本仓库 `dfgh9990/market-monitor`
3. 构建设置：**Framework preset = None（无）**，Build command 留空，**Build output directory = `.`**（仓库根）
4. 部署完成后获得 `https://<项目名>.pages.dev` 公网地址
5. 定时任务每次更新会推送新的 `data.json` 到仓库，Cloudflare Pages 自动重新部署

> GitHub Pages 也可行，但国内访问较慢；如需更快可选腾讯云 EdgeOne Pages。

## 文件说明

| 文件 | 作用 |
|------|------|
| `index.html` | 前端页面（温度计 / 雷达 / 仓位环 / 六维诊断 / 指数 / 板块主力净流入） |
| `score_lib.py` | 六维评分引擎（纯标准库，从 MCP 原始 JSON 抽取并加权） |
| `pipeline.py` | 管线入口：读 MCP 原始 JSON → 生成 `data.json` |
| `make_initial.py` | 用真实收盘快照生成首份 `data.json`（部署 / 自测用） |
| `overview_raw.json` / `sector_raw.json` | MCP 原始数据落盘（供 pipeline 消费） |
| `data.json` | 评分快照（前端读取） |
| `server.py` / `seed.json` | 旧版零依赖后端（备用，仍可用但非主线） |

## 免责声明

数据来自公开行情接口，基于量化规则的参考分析，不构成任何投资建议。
市场有风险，投资需谨慎。
