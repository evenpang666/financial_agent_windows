const reportElement = document.querySelector('#report')
const statusCard = document.querySelector('#statusCard')
const updatedAt = document.querySelector('#updatedAt')
const connectionState = document.querySelector('#connectionState')
const reportDate = document.querySelector('#reportDate')
const refreshButton = document.querySelector('#refreshButton')

function escapeHtml(value) {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;')
}

function inlineMarkdown(value) {
  return escapeHtml(value).replace(/`([^`]+)`/g, '<code>$1</code>').replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
}

function isTableDivider(line) {
  return /^\s*\|?(\s*:?-+:?\s*\|)+\s*:?-+:?\s*\|?\s*$/.test(line)
}

function cells(line) {
  return line.trim().replace(/^\||\|$/g, '').split('|').map(cell => cell.trim())
}

function renderMarkdown(markdown) {
  const lines = markdown.replaceAll('\r\n', '\n').split('\n')
  const html = []
  for (let index = 0; index < lines.length;) {
    const line = lines[index].trim()
    if (!line) { index += 1; continue }
    if (line.startsWith('# ')) { html.push(`<h1>${inlineMarkdown(line.slice(2))}</h1>`); index += 1; continue }
    if (line.startsWith('## ')) { html.push(`<h2>${inlineMarkdown(line.slice(3))}</h2>`); index += 1; continue }
    if (line.startsWith('### ')) { html.push(`<h3>${inlineMarkdown(line.slice(4))}</h3>`); index += 1; continue }
    if (line.startsWith('|') && index + 1 < lines.length && isTableDivider(lines[index + 1])) {
      const headings = cells(line)
      index += 2
      const rows = []
      while (index < lines.length && lines[index].trim().startsWith('|')) {
        rows.push(cells(lines[index]))
        index += 1
      }
      html.push('<div class="table-wrap"><table><thead><tr>' + headings.map(value => `<th>${inlineMarkdown(value)}</th>`).join('') + '</tr></thead><tbody>' + rows.map(row => '<tr>' + row.map(value => `<td>${inlineMarkdown(value)}</td>`).join('') + '</tr>').join('') + '</tbody></table></div>')
      continue
    }
    if (line.startsWith('- ')) {
      const items = []
      while (index < lines.length && lines[index].trim().startsWith('- ')) {
        items.push(`<li>${inlineMarkdown(lines[index].trim().slice(2))}</li>`)
        index += 1
      }
      html.push(`<ul>${items.join('')}</ul>`)
      continue
    }
    html.push(`<p>${inlineMarkdown(line)}</p>`)
    index += 1
  }
  return html.join('')
}

function showEmpty(title = '尚无可展示的日报', message = '请先在 DSH Web 中保存至少一只持仓。交易日上午生成后，此页面会自动更新。', isError = false) {
  statusCard.hidden = false
  statusCard.classList.toggle('error', isError)
  statusCard.querySelector('h2').textContent = title
  statusCard.querySelector('p').textContent = message
  reportElement.hidden = true
}

function showReport(payload) {
  if (!payload.available || !payload.markdown) {
    showEmpty()
    return
  }
  reportElement.innerHTML = renderMarkdown(payload.markdown)
  reportElement.hidden = false
  statusCard.hidden = true
  updatedAt.textContent = `${payload.date} · 更新于 ${new Date(payload.updated_at).toLocaleString('zh-CN')}`
}

async function requestJson(url) {
  const response = await fetch(url, { cache: 'no-store' })
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  return response.json()
}

async function loadIndex(preferredDate = '') {
  try {
    const index = await requestJson('/api/reports')
    reportDate.replaceChildren()
    if (!index.reports.length) {
      reportDate.add(new Option('暂无日报', ''))
      reportDate.disabled = true
      showEmpty()
    } else {
      index.reports.forEach(item => reportDate.add(new Option(item.date, item.date)))
      reportDate.disabled = false
      const selected = preferredDate || index.reports[0].date
      reportDate.value = selected
      showReport(await requestJson(`/api/reports/${encodeURIComponent(selected)}`))
    }
    connectionState.textContent = '已连接'
  } catch (error) {
    connectionState.textContent = '连接失败'
    updatedAt.textContent = '无法连接本地日报服务'
    showEmpty('日报服务不可用', '请确认启动器正在运行，并检查局域网或防火墙设置。', true)
  }
}

reportDate.addEventListener('change', () => loadIndex(reportDate.value))
refreshButton.addEventListener('click', () => loadIndex(reportDate.value))
loadIndex()
setInterval(() => loadIndex(reportDate.value), 30_000)

const reportEvents = new EventSource('/api/events')
reportEvents.addEventListener('report', event => {
  const payload = JSON.parse(event.data)
  loadIndex(payload.date)
})
reportEvents.addEventListener('open', () => { connectionState.textContent = '实时连接' })
reportEvents.addEventListener('error', () => { connectionState.textContent = '自动刷新中' })
