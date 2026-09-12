# Windows：搭建 DeepSeek Harness A 股研究智能体

本包搭建的是**只读的 A 股研究智能体**：查询行情、计算技术指标、阅读财报与公告、生成“关注 / 观望 / 复核”这类研究动作建议。它不连接券商账户、不发送交易指令、不自动买卖。

## 1. 最终组成

```text
DeepSeek Harness (dsh)
├── dsh-astock-research：现成的个股/公告/财报研究插件（可选但建议）
└── dsh-finance-agent：本包提供
    ├── 10 个研究工具（其中 1 个仅保存本地持仓）
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
| `dsh-astock-research` | 现成的 A 股检索、公告、财报功能 | 建议 |
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
- 安装外部插件 `dsh-astock-research`；
- 以本地目录注册 `dsh-finance-agent`；
- 在独立窗口启动本地数据服务，并启动 `dsh web`。

已完成安装时，双击启动器会跳过安装步骤，直接启动本地数据服务和 `dsh web`。

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
dsh plugin --profile web add dsh-astock-research
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
| `get_valuation` | PE/PB 等可得估值字段 | 需要二次核对数据日期 |
| `get_saved_portfolio` | 读取本地保存的研究持仓清单 | 不连接券商或读取账户 |
| `set_portfolio` | 保存代码、可选成本/数量和研究备注 | 仅写入本项目 `data/portfolio.json`，不执行交易 |
| `get_market_brief` | 指数快照与可得市场快讯 | 快讯是待核验线索，不等同于价格影响 |
| `get_market_movers` | 当日涨跌幅靠前标的的研究候选池 | 异动不构成推荐或追涨信号 |

AKShare 适合原型和个人研究；任何公开发布、收费服务或高频使用前，都应改接已获授权且有服务等级承诺的数据源，并审查数据授权条款。

持仓清单仅用于研究上下文，默认保存于本地 `data/portfolio.json`，不会包含券商登录信息或交易凭证。该文件已加入忽略列表；不要把它上传、共享或提交到版本库。

## 7. 内置 Skills

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

其中“动作建议”只能使用研究性表述，例如“加入观察名单”“等待公告确认”“降低单一标的暴露”“复核估值假设”。不得把它改写成个性化买卖指令。

## 8. 推荐提问方式

```text
请研究 600519，数据截止到最近一个可得交易日。
先调用行情、技术指标、财务和公告工具；然后输出：
1. 已确认事实（带来源与日期）；
2. 趋势、基本面、估值和事件的分别判断；
3. 乐观/中性/悲观三种情景及其失效条件；
4. 研究动作建议：关注、观望、复核或排除，并说明证据；
5. 风险清单。
不要给出买卖指令、仓位比例或收益承诺。
```

## 9. 数据时效和责任边界

- 工具的 `as_of` 字段是唯一可依赖的数据时间；没有该字段就不要把数据说成实时。
- 行情、复权、停牌、除权除息、公告归档都可能影响结论；需要交易用途时请从权威/授权数据源复核。
- 历史回测不代表未来结果。回测必须包含手续费、滑点、涨跌停、停牌与样本外验证。
- 本包不构成证券投资咨询服务或投资建议；使用者自行作出并承担投资决策。

## 10. 常见问题

**`dsh` 不是内部或外部命令**：关闭并重开 PowerShell；确认 Node.js 已加入 PATH，再重新执行 `npm install -g @deepseek-ai/dsh pnpm`。

**工具提示无法连接数据服务**：先运行 `Invoke-RestMethod http://127.0.0.1:8765/health`；确认 Python 服务窗口仍在运行且端口 8765 未被占用。

**AKShare 查询失败或字段变化**：升级依赖 `pip install --upgrade akshare`。若仍不稳定，应在 `scripts/stock_data_server.py` 的 Provider 层替换为 Tushare 或企业数据源；不要让模型以旧缓存补写数据。

**需要港股或美股**：保留 Skill 和报告规范，但必须新增经授权的对应市场数据适配器；不要将 6 位 A 股代码规则用于其他市场。
