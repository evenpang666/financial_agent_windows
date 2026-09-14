import { readFileSync, readdirSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

export const name = 'finance-agent'
export const inject = ['tools', 'skills']

const OUTPUT = {
  schema: { type: 'object' },
  render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }],
}

const DESCRIPTIONS = {
  'portfolio-daily-review': 'Review locally saved A-share holdings on trading days using evidence, risk limits, and research-only action labels.',
  'market-event-briefing': 'Summarize daily or weekly market events with source dates, explicit uncertainty, and relevance to A-share research.',
  'daily-stock-candidate-screen': 'Create a research candidate list from current evidence; never present it as a personalized buy list or trading order.',
  'a-share-research': '在用户要求研究、比较或复盘A股标的时使用；以工具事实为依据，输出可追溯的研究报告和非交易性动作建议。',
  'investment-risk-control': '每次涉及股票走势、估值、预测或动作建议时使用；约束数据时效、证据和非投资建议边界。',
  'earnings-and-announcement-analysis': '在解读财报、业绩预告、回购、减持、监管问询或其他公告时使用。',
  'quant-strategy-research': '用户要求策略规则、回测或历史表现评估时使用；要求避免未来函数并报告交易摩擦和样本外结果。',
}

export function apply(ctx, config = {}) {
  const baseUrl = (config.baseUrl || 'http://127.0.0.1:8765').replace(/\/+$/, '')
  const here = dirname(fileURLToPath(import.meta.url))
  const skillDir = join(here, 'skills')

  for (const filename of readdirSync(skillDir).filter((file) => file.endsWith('.md'))) {
    const skillName = filename.replace(/\.md$/, '')
    ctx.skills.register({
      name: skillName,
      description: DESCRIPTIONS[skillName] || `A股研究辅助技能：${skillName}`,
      content: readFileSync(join(skillDir, filename), 'utf8'),
      source: 'bundled',
    })
  }

  async function call(path, options = {}) {
    const { timeoutMs = 30000, ...fetchOptions } = options
    let response
    try {
      response = await fetch(`${baseUrl}${path}`, { signal: AbortSignal.timeout(timeoutMs), ...fetchOptions })
    } catch (error) {
      throw new Error(`本地股票数据服务不可用（${baseUrl}）。请启动 scripts/stock_data_server.py；原始错误：${error.message}`)
    }
    const data = await response.json().catch(() => ({}))
    if (!response.ok) throw new Error(data.error || `数据服务返回 HTTP ${response.status}`)
    return data
  }

  const code = (value) => {
    if (!/^\d{6}$/.test(value)) throw new Error('symbol 必须是 6 位 A 股代码；请先用 stock_search 确认代码。')
    return value
  }
  const register = (definition) => ctx.tools.register({ output: OUTPUT, ...definition })

  register({
    name: 'stock_search',
    description: 'Search the local A-share universe by six-digit symbol or company name before research. Returns matching symbols and names.',
    parameters: { type: 'object', properties: {
      query: { type: 'string', minLength: 1, description: 'Six-digit symbol or part/all of a company name.' },
      limit: { type: 'integer', minimum: 1, maximum: 50 },
    }, required: ['query'], additionalProperties: false },
    execute: ({ query, limit = 10 }) => call(`/v1/search?query=${encodeURIComponent(query)}&limit=${limit}`),
  })
  register({
    name: 'get_saved_portfolio',
    description: 'Read the locally saved research portfolio. It contains only user-supplied symbols, optional cost basis, shares, and notes; it has no broker connection.',
    parameters: { type: 'object', properties: {}, additionalProperties: false },
    execute: () => call('/v1/portfolio'),
  })
  register({
    name: 'set_portfolio',
    description: 'Replace the locally saved research portfolio. This only stores the provided entries in the project data folder and cannot place orders or access a brokerage account.',
    parameters: { type: 'object', properties: {
      holdings: { type: 'array', minItems: 0, maxItems: 100, items: { type: 'object', properties: {
        symbol: { type: 'string', description: 'Six-digit A-share symbol, such as 600519.' },
        name: { type: 'string', description: 'Optional display name.' },
        cost_basis: { type: 'number', minimum: 0, description: 'Optional average cost for research context.' },
        shares: { type: 'number', minimum: 0, description: 'Optional share count for exposure review.' },
        note: { type: 'string', description: 'Optional non-sensitive research note.' },
      }, required: ['symbol'], additionalProperties: false } },
    }, required: ['holdings'], additionalProperties: false },
    execute: ({ holdings }) => call('/v1/portfolio', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ holdings }),
    }),
  })
  register({
    name: 'get_trading_day',
    description: 'Check whether a date is an official mainland China A-share trading day.',
    parameters: { type: 'object', properties: { date: { type: 'string', description: 'YYYY-MM-DD; defaults to today.' } }, additionalProperties: false },
    execute: ({ date = '' }) => call(`/v1/trading-day?date=${encodeURIComponent(date)}`),
  })
  register({
    name: 'get_market_brief',
    description: 'Get an A-share index snapshot and recent market-event headlines for daily or weekly research. Headlines are leads to verify, not proof of price impact.',
    parameters: { type: 'object', properties: { event_limit: { type: 'integer', minimum: 1, maximum: 50 } }, additionalProperties: false },
    execute: ({ event_limit = 20 }) => call(`/v1/market-brief?event_limit=${event_limit}`),
  })
  register({
    name: 'get_market_events',
    description: 'Get dated A-share market-event leads for an explicit daily, weekly or monthly lookback window.',
    parameters: { type: 'object', properties: {
      days: { type: 'integer', minimum: 1, maximum: 90 },
      limit: { type: 'integer', minimum: 1, maximum: 100 },
    }, additionalProperties: false },
    execute: ({ days = 7, limit = 20 }) => call(`/v1/market-events?days=${days}&limit=${limit}`),
  })
  register({
    name: 'get_market_movers',
    description: 'Get the day’s leading A-share gainers and losers as an evidence-based research universe. It is not a stock recommendation or a signal to chase a move.',
    parameters: { type: 'object', properties: { limit: { type: 'integer', minimum: 1, maximum: 50 } }, additionalProperties: false },
    execute: ({ limit = 20 }) => call(`/v1/market-movers?limit=${limit}`),
  })
  register({
    name: 'get_market_panorama',
    description: 'Get the A-share market panorama: international events, domestic policy, social/attention sentiment, market breadth, risk level, reference position range, and the adjustment applied to stock buy/sell tendency percentages.',
    parameters: { type: 'object', properties: {}, additionalProperties: false },
    execute: () => call('/v1/market-panorama', { timeoutMs: 120000 }),
  })
  register({
    name: 'get_candidate_ranking',
    description: 'Rank 1-20 non-ST A-share research candidates with a transparent momentum, liquidity and valuation score. Not a buy list.',
    parameters: { type: 'object', properties: { limit: { type: 'integer', minimum: 1, maximum: 20 } }, additionalProperties: false },
    execute: ({ limit = 5 }) => call(`/v1/candidate-ranking?limit=${limit}`),
  })
  register({
    name: 'get_daily_research_report',
    description: 'Build the trading-day pre-open report with market panorama and ranked candidates; include the portfolio table and short/long views only when holdings are saved.',
    parameters: { type: 'object', properties: { candidate_limit: { type: 'integer', minimum: 1, maximum: 20 } }, additionalProperties: false },
    execute: ({ candidate_limit = 5 }) => call(`/v1/daily-report?candidate_limit=${candidate_limit}`, { timeoutMs: 180000 }),
  })

  register({
    name: 'get_quote_snapshot',
    description: '获取A股最近可得的价格快照、成交和可得估值字段。它不是逐笔实时行情；回复必须引用返回的 as_of。',
    parameters: { type: 'object', properties: { symbol: { type: 'string', description: '6位A股代码，例如600519' } }, required: ['symbol'], additionalProperties: false },
    execute: ({ symbol }) => call(`/v1/quote?symbol=${code(symbol)}`),
  })
  register({
    name: 'get_ohlcv',
    description: '获取A股历史日线OHLCV。用于描述走势时必须说明复权方式和数据区间。',
    parameters: { type: 'object', properties: { symbol: { type: 'string' }, start: { type: 'string', description: 'YYYY-MM-DD，可选' }, end: { type: 'string', description: 'YYYY-MM-DD，可选' }, adjust: { type: 'string', enum: ['qfq', 'hfq', ''], description: 'qfq前复权、hfq后复权、空为不复权' } }, required: ['symbol'], additionalProperties: false },
    execute: ({ symbol, start = '', end = '', adjust = 'qfq' }) => call(`/v1/ohlcv?symbol=${code(symbol)}&start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}&adjust=${encodeURIComponent(adjust)}`),
  })
  register({
    name: 'get_technical_indicators',
    description: '由程序基于历史日线计算MA、RSI、MACD、波动率和最大回撤。不得要求模型自行计算这些指标。',
    parameters: { type: 'object', properties: { symbol: { type: 'string' }, lookback: { type: 'integer', minimum: 60, maximum: 1000, description: '计算使用的最近交易日数量，默认260' }, adjust: { type: 'string', enum: ['qfq', 'hfq', ''] } }, required: ['symbol'], additionalProperties: false },
    execute: ({ symbol, lookback = 260, adjust = 'qfq' }) => call(`/v1/indicators?symbol=${code(symbol)}&lookback=${lookback}&adjust=${encodeURIComponent(adjust)}`),
  })
  register({
    name: 'get_fundamentals',
    description: '获取A股财务分析指标。只能基于返回的报告期与字段陈述事实；缺失数据不得补写。',
    parameters: { type: 'object', properties: { symbol: { type: 'string' } }, required: ['symbol'], additionalProperties: false },
    execute: ({ symbol }) => call(`/v1/fundamentals?symbol=${code(symbol)}`),
  })
  register({
    name: 'get_announcements',
    description: '查询巨潮资讯A股公告标题、日期和原文链接。公告披露事实不等于价格影响；不要将标题机械归为利好或利空。',
    parameters: { type: 'object', properties: { symbol: { type: 'string' }, start: { type: 'string', description: 'YYYY-MM-DD，可选' }, end: { type: 'string', description: 'YYYY-MM-DD，可选' }, limit: { type: 'integer', minimum: 1, maximum: 100 } }, required: ['symbol'], additionalProperties: false },
    execute: ({ symbol, start = '', end = '', limit = 20 }) => call(`/v1/announcements?symbol=${code(symbol)}&start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}&limit=${limit}`),
  })
  register({
    name: 'get_related_large_enterprises',
    description: 'Identify the target A-share industry, select up to five same-industry leaders by total market capitalization, and retrieve their recent CNINFO announcements for context and risk checks.',
    parameters: { type: 'object', properties: {
      symbol: { type: 'string' },
      days: { type: 'integer', minimum: 1, maximum: 180 },
      limit: { type: 'integer', minimum: 1, maximum: 5 },
    }, required: ['symbol'], additionalProperties: false },
    execute: ({ symbol, days = 30, limit = 3 }) => call(`/v1/related-enterprises?symbol=${code(symbol)}&days=${days}&limit=${limit}`, { timeoutMs: 120000 }),
  })
  register({
    name: 'get_valuation',
    description: '获取数据源可用的PE、PB等估值快照。它不是完整估值模型；使用时必须标记数据日期和不可用字段。',
    parameters: { type: 'object', properties: { symbol: { type: 'string' } }, required: ['symbol'], additionalProperties: false },
    execute: ({ symbol }) => call(`/v1/valuation?symbol=${code(symbol)}`),
  })
  register({
    name: 'analyze_stock',
    description: 'Analyze one A-share using financials, technicals, valuation, company announcements, dated market-context coverage, and recent announcements from same-industry large-cap leaders.',
    parameters: { type: 'object', properties: {
      symbol: { type: 'string' },
      announcement_days: { type: 'integer', minimum: 1, maximum: 730 },
    }, required: ['symbol'], additionalProperties: false },
    execute: ({ symbol, announcement_days = 180 }) => call(`/v1/analysis?symbol=${code(symbol)}&announcement_days=${announcement_days}`),
  })

  ctx.logger?.info?.('finance-agent: local data tools and %d bundled skills registered (baseUrl=%s)', Object.keys(DESCRIPTIONS).length, baseUrl)
}
