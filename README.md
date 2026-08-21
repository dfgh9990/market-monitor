# 盘中市场强弱监控 · 动态仓位系数工具

基于六维市场强弱评分（涨跌比广度 / 量能健康度 / 板块资金集中度 / 主力资金流向 / 指数趋势 / 市场情绪温度），
加权合成综合评分并映射成动态仓位系数（0~100%），**盘中每 30 分钟自动刷新**。

数据来源为 **腾讯自选股 MCP（westock-mcp）+ 通达信实时广度**：
- **板块资金流 / 量能 / 指数趋势** → 腾讯自选股 MCP（`data_market_overview` + `data_sector`）
- **涨跌比广度 / 市场情绪温度** → 通达信 `tdx_screener`（`message="上涨/下跌/平盘/涨停/跌停"`，读 `meta.total`，盘中实时刷新）

由定时任务抓取真实盘面 → 跑评分引擎 → 生成 `data.json`（含**当日温度曲线历史**与**连板最高标 Top5**）→
托管到静态站点（Cloudflare Pages / GitHub Pages），任意设备浏览器即可访问。**完全免费、无需信用卡、数据真实**。

## 架构（取数在自动化沙箱 · 展示在云端）

```
┌─ 取数层（WorkBuddy 自动化沙箱，通达信 MCP 取实时广度）──────────────┐
│  腾讯自选股 MCP ──> overview_raw.json / sector_raw.json             │
│  通达信 tdx_screener ──> tdx_breadth.py ──> breadth_raw.json (实时) │
│  通达信 tdx_screener(连续涨停) ──> tdx_limitup.py ──> limitup_raw.json │
└──────────────────────────────┬─────────────────────────────────────┘
                                ▼ pipeline.py（六维评分引擎）
                            data.json
                                ▼ 推送到 GitHub 仓库
                       Cloudflare Pages（静态托管）◄── 自动重新部署
                                ▼
                  任意设备浏览器打开 https://<项目>.pages.dev
```

- **云端只托管静态 `data.json` + `index.html`，不直连任何行情接口**，所以浏览器侧无 CORS / 无限流。
- **实时广度走通达信 MCP（`tdx_screener`）**：自动化运行在 WorkBuddy 云端沙箱，东方财富 `push2` 对该出口限流不可用，故实时涨跌家数改由通达信提供（同股票池、含可核验的涨停/跌停个股）。腾讯自选股 `overview` 的 `CNT_*` 仅作为实时广度不可用时的日线回退。
- `tdx_breadth.py` 取数/落盘失败时（tdx_screener 异常、或 total<4000 被截断）**自动回退**到 overview 的日线广度，页面不会崩、不会写脏数据。
- **连板最高标走通达信 `tdx_screener(message="连续涨停")`**：返回含 `连续涨停天数0#` 字段的当前连板股列表，`tdx_limitup.py` 抽取为 `limitup_raw.json`，pipeline 排序取最高标 + Top5。
- **当日温度曲线**：每次盘中刷新把当前综合评分快照追加进 `temperature_history.json`（按日期分桶、同分钟去重），前端绘制 09:30–15:00 的盘中评分曲线。

## 六维权重

| 维度 | 权重 |
|------|------|
| 涨跌比广度 | 25% |
| 量能健康度 | 15% |
| 板块资金集中度 | 20% |
| 主力资金流向 | 20% |
| 指数趋势 | 10% |
| 市场情绪温度 | 10% |

**综合评分 → 动态仓位系数（绝对映射表，非线性阶梯）：**

| 综合评分区间 | 对应仓位 | 市场定性 | 核心操作口诀 |
|------------|:--------:|---------|-------------|
| 80 ~ 100 分 | 100% | 强势主升 | 贪婪持股 |
| 60 ~ 79 分  | 80%  | 偏强操作 | 顺势加码 |
| 40 ~ 59 分  | 60%  | 震荡灵活 | 高抛低吸 |
| 20 ~ 39 分  | 30%  | 偏弱防守 | 只卖不买 |
| 0 ~ 19 分   | 0%   | 极端风险 | 强制休息 |

> 注：无论暴跌（恐慌）还是暴涨（高潮退潮预警），落入 0~19 分区间一律强制空仓休息，不抄底。

## 反身性冷却机制（Contrarian Cooling）· 防"高潮退潮"

传统线性模型"涨得越多分越高"，在实战中会在**情绪高潮末端**误导加仓。本工具对
**涨跌比广度**与**市场情绪温度（涨跌停比）**两个维度改用**倒 U 型抛物线**计分，
专门对"极端狂热"做扣分惩罚而非加分：

```
得分 = 4 × X × (1 - X) × 100
```
- `X` = 上涨比例（广度）或 涨停/(涨停+跌停)（情绪）
- `X = 0.5`（涨跌各半）→ 满分 100（最健康、最稳，给重仓）
- `X → 0`（极弱）或 `X → 1`（极强/高潮）→ 得分都趋近 0
- **广度过热**：`X > 0.80`（普涨高潮）→ 触发全局「高潮预警」，自动降权
- **极弱空仓**：`X < 0.15` → 强制 0 分，不抄底不猜底
- **情绪过热**：涨跌停比 `> 0.8` 且涨停 `≥ 80`（百股涨停级别）的真正极限潮 → 拉响警报（避免日常误报）

> 广度采用**非对称**实现：弱侧 `X ≤ 0.5` 随涨股比单调上升，强侧 `X > 0.5` 才用倒 U 抛物线——避免"33% 上涨"与"67% 上涨"被同分，真实弱势日不会被误判为偏强。
> 情绪维度同样加绝对值门槛：仅有少数涨停 + 跌停稀少（涨跌停比虚高）属结构性偏热，不判为全局亢奋。

核心哲学：**中庸之道**（涨跌平衡时给重仓）+ **盛极而衰**（涨过头自动降权）。
前端在 `index.html` 内置红色「高潮预警」警报条，任一维度进入高潮区即弹出。

## 冰点逆修正（Contrarian Reversal）· 防"跌过头永不抄底"

旧版里只要下跌家数 > 4000（上涨比例 < 15%），涨跌比广度直接归零，导致市场**连续冰点**时系统永远 0 分、从不提示抄底机会。
升级后引入**连续天数感知的逆修正**：广度持续冰点越久，越视为"情绪极致宣泄、物极必反"，逆势上调广度分，给出抄底窗口信号。

| 冰点状态 | 广度分处理 | 系统判定 | 前端提示 |
|---------|-----------|---------|---------|
| **Day 1**（首次出现） | 保持 0 ~ 20 分 | 恐慌初期，不抄底，继续观望 | 普通冰点提示 |
| **Day 2**（连续 2 天） | **逆势修正至 70 分** | 情绪极致宣泄，物极必反，开始关注抄底机会 | 🟡 金色横幅 |
| **Day 3 及以上** | **上调至 85+ 分**（Day3=85，Day4+=90 封顶） | 极度超卖，具备强反弹需求，可分批低吸 | 🟡 金色横幅 |

- **判定口径**：`上涨比例 = up / total < 0.15` 即视为冰点（与反身性冷却的"极弱空仓"阈值一致）。
- **连续天数持久化**：状态存于 `icepoint_state.json`（与 `data.json` 同目录、随仓库推送），
  按**交易日**计数——同一交易日内多次刷新不重复计数；中间夹一个非冰点交易日则计数重置为 Day 1。
- **不强制满仓**：逆修正只上调"广度"单一维度（权重 25%）。真实多日暴跌时趋势/资金/情绪等维度仍偏弱，
  综合分通常落在 40~60 区间（半仓、高抛低吸），即"开始关注、分批低吸"，而非无脑满仓。
- **前端**：`index.html` 内置**金色冰点横幅**（区别于红色高潮警报与绿色常规），显示连续天数与抄底/观望建议；
  `data.json` 的 `icepoint` 字段（`active / days / level / corrected_score / message`）供前端渲染。

## 通达信实时广度（主源）与透明度备注

- **通达信 `tdx_screener` 是盘中实时广度的唯一主源**（沙箱内可用、含可核验的涨停/跌停个股）。
  依次查 `上涨/下跌/平盘/涨停/跌停`，读各自 `meta.total`，由 `tdx_breadth.py` 落盘为 `breadth_raw.json`。
- **腾讯自选股 `overview` 的 `CNT_*` 仅作日线回退**（实时广度不可用时）。
- **两源对比仅供参考，不自动改分**：两者属同一股票池（total 偏差常 <1%），但 up/down 口径可能差异很大——
  典型如"二八分化"（指数红、但多数个股绿，通达信显示涨股比仅约 1/3）。此时以通达信实时广度为准，
  自动化摘要会提示该差异，便于人工识别"指数虚红、个股普跌"的脆弱上涨。

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
python pipeline.py overview_raw.json sector_raw.json data.json breadth_raw.json limitup_raw.json
# breadth_raw.json 可选：带它 → 用通达信实时广度（盘中跳动）；不带 → 回退 overview 日线广度（收盘才更新）
# limitup_raw.json 可选：带它 → 注入连板最高标/Top5；不带 → 前端显示"暂无连板数据"
```

用通达信实时广度生成一次 breadth_raw.json（5 个口径取自 tdx_screener 的 meta.total；取数不完整会自动拒绝写入）：
```bash
python tdx_breadth.py <up> <down> <flat> <limit_up> <limit_down>
```

用通达信连板数据生成 limitup_raw.json（源文件为 tdx_screener 返回的原始 JSON，兼容 markdown 包裹 / 纯 JSON / stdin）：
```bash
python tdx_limitup.py limitup_raw_mcp.json
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
| `index.html` | 前端页面（温度计 / 雷达 / 仓位环 / 六维诊断 / 指数 / 板块主力净流入 / 红色高潮预警条 / 金色冰点横幅 / 当日温度曲线 / 连板最高标+Top5） |
| `score_lib.py` | 六维评分引擎（纯标准库，从 MCP 原始 JSON 抽取并加权；含反身性冷却非对称模型 + 冰点逆修正 + 绝对仓位映射表） |
| `pipeline.py` | 管线入口：读 MCP 原始 JSON（+ 可选 breadth_raw.json / limitup_raw.json）→ 生成 `data.json`；维护 `icepoint_state.json` 并追加 `temperature_history.json` |
| `tdx_breadth.py` | 通达信实时涨跌家数落盘（读 tdx_screener 的 5 个口径，带一致性校验，失败回退日线） |
| `tdx_limitup.py` | 通达信连板数据落盘（读 `tdx_screener(message="连续涨停")` 返回 JSON，抽取 `连续涨停天数0#` 等字段） |
| `realtime_breadth.py` | ⚠️ 已弃用：东方财富 push2 在沙箱被限流，不再使用（保留备用） |
| `make_initial.py` | 用真实收盘快照生成首份 `data.json`（部署 / 自测用） |
| `overview_raw.json` / `sector_raw.json` | MCP 原始数据落盘（供 pipeline 消费） |
| `breadth_raw.json` | 通达信实时广度快照（盘中生成，失败则不存在→回退日线 overview） |
| `limitup_raw.json` | 通达信连板数据快照（`连续涨停天数0#` 排序，供 pipeline 注入最高标与 Top5） |
| `temperature_history.json` | 当日温度曲线历史（按日期分桶，每次刷新追加 `{time, score, position}`；**不可手动删除**） |
| `icepoint_state.json` | 冰点逆修正的连续天数状态（持久化、随仓库推送；**不可手动删除**） |
| `update_github.py` | 推送数据文件到 GitHub（仅数据，不动源码；Token 取自 `GITHUB_PAT`；清单含 `icepoint_state.json`/`limitup_raw.json`/`temperature_history.json`） |
| `data.json` | 评分快照（前端读取，含 `overheat`/`market_status`/`icepoint`/`limitup`/`temperature_history` 字段） |
| `server.py` / `seed.json` | 旧版零依赖后端（备用，仍可用但非主线） |

## 免责声明

数据来自公开行情接口，基于量化规则的参考分析，不构成任何投资建议。
市场有风险，投资需谨慎。
