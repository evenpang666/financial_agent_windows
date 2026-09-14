# Windows：搭建 DeepSeek Harness A 股研究智能体

本包搭建的是**只读的 A 股分析智能体**：查询行情、计算技术指标、阅读财报与公告，并基于客观指标给出明确买卖结论以及合计为 100% 的买入/卖出倾向。倾向百分比不是仓位或涨跌概率；系统不连接券商账户、不发送交易指令、不自动买卖。

## 1. 最终组成

```text
DeepSeek Harness (dsh)
└── dsh-finance-agent：本包提供
    ├── 18 个研究工具（检索、市场全景、同行龙头、短长期分析、交易日、日报与候选排行）
    └── 7 个内置研究 Skills
            ↑
    Python 本地数据服务（AKShare，默认 127.0.0.1:8765）
```

## 2. 需要安装的内容

| 项目 | 用途 | 是否必须 |
|---|---|---:|
| Node.js 22 LTS | 运行 dsh 和插件 | 是 |
| Python 3.11+ | 本地数据和指标服务 | 是 |
| `@deepseek-ai/dsh` | Agent 运行环境 | 是 |
| 本包 `dsh-finance-agent` | 本地行情、指标、估值工具与 Skills | 是 |
| AKShare / pandas / numpy | 数据适配与指标计算 | 是 |

请从 Node.js 和 Python 官方网站安装对应 Windows x64 版本；安装 Node 时勾选“Add to PATH”。安装后，在**新的 PowerShell**运行：

```powershell
node --version
python --version
```

## 3. 一键安装与启动（Windows 推荐）

桌面上的 `启动财务研究智能体.cmd` 可以直接双击运行。首次运行会自动：

- 安装 `dsh` 与 `pnpm`；
- 创建 `.venv` 并安装 Python 依赖；
- 以本地目录注册 `dsh-finance-agent`；
- 在独立窗口启动本地数据服务，并启动 `dsh web`。
- 注册每日 09:20 日报任务和登录时启动的本地日报网页，目标在 09:30（开盘前10分钟）前送达。

已完成安装时，双击启动器会跳过安装步骤，直接启动本地数据服务和 `dsh web`。

如需关闭当前运行的 DSH Web、行情服务、日报网页及本项目后台调度器，可双击 `关闭财务研究智能体.cmd`。该脚本不会删除持仓、配置、日报归档或 Windows 计划任务；下次双击启动器仍可重新启动。

也可以在项目根目录手动运行相同的启动脚本：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\install-and-start.ps1
```

首次启动后，浏览器打开终端显示的地址（通常是 `http://127.0.0.1:3080`），在“设置 → 模型”中填写 DeepSeek API Key。API Key 不要写入本项目文件或提交到 Git。

## 4. 手动安装（可选）

如果不使用一键脚本，请在项目根目录以普通 PowerShell 运行：

```powershell
npm install -g @deepseek-ai/dsh pnpm
dsh plugin --profile web add .\dsh-finance-agent
```

`dsh-finance-agent` 是本项目内的本地包，未发布到 npm 公共仓库；必须以路径形式添加。不要运行 `dsh plugin --profile web add dsh-finance-agent`，否则 dsh 会在 npm 中查找该包并返回 404。

该插件默认调用 `http://127.0.0.1:8765` 的本地数据服务，并自动注册本包内的 7 个 Skills。

## 5. 启动本地数据服务（手动方式）

第一次创建 Python 虚拟环境并安装依赖：

```powershell
# 先切换到包含 requirements.txt 的项目根目录
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

若 PowerShell 阻止激活脚本，只对当前窗口临时放开：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

启动服务：

```powershell
.\.venv\Scripts\python.exe .\scripts\stock_data_server.py
```

看到 `Serving on http://127.0.0.1:8765` 后，另开一个 PowerShell 再运行：

```powershell
dsh web
```

验证服务：

```powershell
Invoke-RestMethod http://127.0.0.1:8765/health
```

## 6. 本包新增的工具

| 工具名 | 用途 | 关键限制 |
|---|---|---|
| `get_quote_snapshot` | 最近可得价格快照、成交量、PE/PB（如源提供） | 不是逐笔实时行情 |
| `get_ohlcv` | 后复权/前复权日线 | 返回数据日期和来源 |
| `get_technical_indicators` | MA、RSI、MACD、波动率、最大回撤 | 指标由程序计算，不由模型心算 |
| `get_fundamentals` | 财务分析指标 | 数据源缺失时明确报错 |
| `get_announcements` | 巨潮公告标题和链接 | 仅陈述披露事实 |
| `get_related_large_enterprises` | 按目标股所属行业和总市值识别同行龙头并查询近期公告 | 行业背景只用于核验，不按标题机械评分 |
| `get_valuation` | PE/PB 等可得估值字段 | 需要二次核对数据日期 |
| `get_saved_portfolio` | 读取本地保存的研究持仓清单 | 不连接券商或读取账户 |
| `set_portfolio` | 保存代码、可选成本/数量和研究备注 | 仅写入本项目 `data/portfolio.json`，不执行交易 |
| `get_market_brief` | 指数快照与可得市场快讯 | 快讯是待核验线索，不等同于价格影响 |
| `get_market_movers` | 当日涨跌幅靠前标的的研究候选池 | 异动不构成推荐或追涨信号 |
| `get_market_panorama` | 国际事件、国内政策、社交/关注情绪、大盘宽度、风险等级与参考仓位 | 情绪含价格代理；参考仓位不是个性化建议 |
| `stock_search` | 按 6 位代码或名称检索 A 股 | 名称有歧义时返回多个匹配项 |
| `get_trading_day` | 使用交易日历判断指定日期 | 每日任务在非交易日跳过 |
| `get_market_events` | 按 7 日、30 日等窗口取得事件线索 | 快讯需以公告或权威原文复核 |
| `get_candidate_ranking` | 按透明规则生成热门研究候选排行 | 基础分会按市场风险档校准，排名不是买入顺序 |
| `analyze_stock` | 汇总财务、行情、指标、估值、公告、市场覆盖状态和同行龙头动态 | 输出经市场状态校准的买入/卖出倾向和明确结论 |
| `get_daily_research_report` | 生成含买卖建议的持仓表、周/月事件和候选排行 | 数据较多时可能需要较长时间 |

AKShare 适合原型和个人研究；任何公开发布、收费服务或高频使用前，都应改接已获授权且有服务等级承诺的数据源，并审查数据授权条款。

持仓清单仅用于研究上下文，默认保存于本地 `data/portfolio.json`，不会包含券商登录信息或交易凭证。该文件已加入忽略列表；不要把它上传、共享或提交到版本库。

## 7. 每日开盘前推送配置

### 7.1 使用流程

1. 双击 `启动财务研究智能体.cmd`，等待 DSH Web 打开。启动器同时启动本地数据服务和日报网页。
2. 在 DSH Web 对话框中发送持仓。例如：

```text
请保存我的持仓：
600519，名称贵州茅台，成本1280，数量100；
000858，名称五粮液，成本125，数量200。
```

智能体会调用 `set_portfolio`，将持仓保存到仅供本机使用的 `data/portfolio.json`。以后可以在 DSH Web 中发送“查看我的持仓”核对，或发送新的完整列表覆盖旧记录。

3. 如果持仓列表不存在或为空，系统仍会生成、归档并推送市场全景和推荐股，只省略“已持仓股票”及其建议；保存至少一只持仓后，日报会自动增加持仓分析。
4. 每个交易日 09:20 开始汇总，目标在 09:30 前完成。非交易日不生成日报。
5. 日报生成后会保存到 `data/reports/YYYY-MM-DD.md`，随后通过本地实时通知推送到网页；如果实时连接暂时中断，网页也会在 30 秒内自动刷新读取，无需手动上传。

### 7.2 在网页查看日报

本机访问：`http://127.0.0.1:8766`

同一局域网的手机或电脑访问：`http://本机局域网IP:8766`。启动脚本会在终端中列出可用的局域网地址，例如 `http://192.168.1.20:8766`。网页支持最新日报、历史日报切换和手动刷新。

如果其他设备无法访问，请确认两台设备连接同一局域网、Windows 网络类型为“专用网络”，并以管理员 PowerShell 运行：

```powershell
.\scripts\configure-lan-firewall.ps1
```

该命令只允许专用网络的本地子网访问 TCP 8766 端口。日报可能包含持仓信息，不建议在公共 Wi-Fi 或不可信局域网开放。

### 7.3 调度与 Webhook

首次运行 `install-and-start.ps1` 会将 `config/daily-push.example.json` 复制为不纳入 Git 的 `config/daily-push.json`，并注册 Windows 计划任务 `DSH A-Share Pre-open Research`。任务每天北京时间 09:20 开始并行汇总，目标于 09:30 前送达；数据服务会再次核验交易日，因此周末和休市日不会推送。若系统不允许注册任务，启动器会自动退回为当前登录会话内的后台调度器。

启动器还会注册 `DSH A-Share Report Site` 登录任务，使日报网页在用户登录 Windows 后自动运行。

默认情况下，简报归档到 `data/reports/YYYY-MM-DD.md`。如需远程推送，在配置中填写 Webhook：

```json
{
  "enabled": true,
  "scheduled_time": "09:20",
  "candidate_limit": 5,
  "webhook_type": "feishu",
  "webhook_url": "https://your.invalid/your-webhook"
}
```

`webhook_type` 支持 `feishu`、`wecom`、`dingtalk` 和 `generic`。更推荐把地址放入环境变量 `FINANCE_AGENT_WEBHOOK_URL`，避免把凭证写入文件。配置文件已加入 `.gitignore`。

可手动验证一次；非交易日会正常跳过，`--force` 只忽略重复发送状态，不绕过交易日检查：

```powershell
.\.venv\Scripts\python.exe .\scripts\daily_push.py --once --force
```

## 8. 内置 Skills

`dsh-finance-agent/skills/` 中的文件随插件自动加载：

| Skill | 何时使用 |
|---|---|
| `a-share-research` | 用户要求研究、比较或复盘 A 股时 |
| `investment-risk-control` | 每次涉及市场判断或动作建议时 |
| `earnings-and-announcement-analysis` | 解读财报、业绩预告和公告事件时 |
| `quant-strategy-research` | 要求回测或评估规则时 |
| `portfolio-daily-review` | 开盘日或每周复盘已保存持仓时 |
| `market-event-briefing` | 汇总每日/每周市场大事并分析可能传导路径时 |
| `daily-stock-candidate-screen` | 生成每日待研究候选池时 |

分析结果会使用“建议买入 / 建议持有观察 / 建议卖出 / 信息不足，暂缓决策”，并输出买入、卖出倾向百分比。百分比是可复核的证据方向评分，不是建议仓位，也不会触发交易。

市场全景以全市场涨跌家数、5/20日参与度、涨跌停分布和关注榜构造宽度与情绪代理，并把最近事件拆为国际事件、国内政策和其他事件。综合风险档产生统一的市场调整值（-20 至 +12 个百分点），同时校准持仓股与候选股的基础买入倾向。报告会保留基础分和调整值，便于复核。参考仓位仅是分散组合的市场风险预算区间，不针对个人资产配置。

单股分析同时返回 `market_context`，逐项标记国际事件、国内政策、大盘宽度和社交/关注数据的覆盖状态、窗口、截止日期及缺失原因，防止数据源失败时被误读为“中性”。`related_large_enterprises` 会识别目标公司的东方财富行业，从行业成分中按总市值选取最多 3 家同行龙头，并查询其近 30 日巨潮公告；这些公告只作为行业背景与风险核验，不直接改变个股评分。

## 9. 推荐提问方式

```text
请研究 600519，数据截止到最近一个可得交易日。
先调用行情、技术指标、财务和公告工具；然后输出：
1. 已确认事实（带来源与日期）；
2. 趋势、基本面、估值和事件的分别判断；
3. 乐观/中性/悲观三种情景及其失效条件；
4. 分别给出短期和长期建议；
5. 总结为建议买入、建议持有观察、建议卖出或信息不足，并给出合计100%的买入/卖出倾向；
6. 风险清单和结论失效条件。
百分比不得解释为仓位或涨跌概率，不要作收益承诺。
```

## 10. 数据时效和责任边界

- 工具的 `as_of` 字段是唯一可依赖的数据时间；没有该字段就不要把数据说成实时。
- 行情、复权、停牌、除权除息、公告归档都可能影响结论；需要交易用途时请从权威/授权数据源复核。
- 历史回测不代表未来结果。回测必须包含手续费、滑点、涨跌停、停牌与样本外验证。
- 本包输出的是程序化买卖建议和证据倾向分，不是持牌证券投资咨询；百分比不是仓位或收益概率，使用者自行作出并承担投资决策。

## 11. 常见问题

**`dsh` 不是内部或外部命令**：关闭并重开 PowerShell；确认 Node.js 已加入 PATH，再重新执行 `npm install -g @deepseek-ai/dsh pnpm`。

**工具提示无法连接数据服务**：先运行 `Invoke-RestMethod http://127.0.0.1:8765/health`；确认 Python 服务窗口仍在运行且端口 8765 未被占用。

**AKShare 查询失败或字段变化**：升级依赖 `pip install --upgrade akshare`。若仍不稳定，应在 `scripts/stock_data_server.py` 的 Provider 层替换为 Tushare 或企业数据源；不要让模型以旧缓存补写数据。

**需要港股或美股**：保留 Skill 和报告规范，但必须新增经授权的对应市场数据适配器；不要将 6 位 A 股代码规则用于其他市场。
