// Historical reports contain Chart.js bootstraps. Read their serialized numbers
// as data only; never execute report-provided code or enable iframe scripts.
const escape = (value: unknown) =>
  String(value).replace(
    /[&<>"']/g,
    (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]!,
  );
function arrays(code: string, property: string): unknown[][] {
  const matches = code.matchAll(
    new RegExp(`${property}:\\s*\\[((?:"(?:\\\\.|[^"\\\\])*"|[^\\]"])*)\\]`, 'g'),
  );
  return [...matches].map((match) => {
    try {
      const body = match[1].replace(/,\s*$/, '');
      // Historical Jinja reports may serialize an unavailable metric as an
      // empty array slot. Preserve that position as missing, never as zero.
      const normalized =
        property === 'data'
          ? body
              .split(',')
              .map((value) => (value.trim() === '' || value.trim() === 'None' ? 'null' : value))
              .join(',')
          : body;
      const value: unknown = JSON.parse(`[${normalized}]`);
      return Array.isArray(value) ? value : [];
    } catch {
      return [];
    }
  });
}
function bars(labels: string[], series: { name: string; values: (number | null)[] }[]) {
  const width = 600,
    left = 170,
    plot = 330,
    line = series.length * 22 + 20;
  const values = series.flatMap((s) => s.values).filter((value): value is number => value !== null);
  const min = Math.min(0, ...values),
    max = Math.max(1, ...values);
  const scale = (n: number) => left + ((n - min) / (max - min)) * plot;
  const zero = scale(0);
  const height = labels.length * line + 35;
  const colors = ['#2263a6', '#95a9c0'];
  return `<svg xmlns="http://www.w3.org/2000/svg" role="img" aria-label="报告图表，数值标注在柱旁" viewBox="0 0 ${width} ${height}" style="width:100%;height:auto;min-height:160px;font:12px sans-serif"><title>报告图表</title>${labels
    .map(
      (label, index) =>
        `<text x="0" y="${index * line + 22}" fill="#203246">${escape(label.slice(0, 18))}<title>${escape(label)}</title></text>${series
          .map((s, i) => {
            const n = s.values[index],
              position = scale(n ?? 0),
              y = index * line + 8 + i * 22;
            return `${n === null ? '' : `<rect x="${Math.min(zero, position)}" y="${y}" width="${Math.abs(position - zero)}" height="14" rx="2" fill="${colors[i % colors.length]}"/>`}<text x="${plot + left + 8}" y="${y + 12}" fill="#203246">${escape(s.name)} ${n === null ? '未采集' : escape(n.toLocaleString('zh-CN', { maximumFractionDigits: 2 }))}</text>`;
          })
          .join('')}`,
    )
    .join('')}<line x1="${zero}" x2="${zero}" y1="0" y2="${height - 15}" stroke="#c8d3df"/></svg>`;
}
export function prepareReportHtml(html: string): string {
  const doc = new DOMParser().parseFromString(html, 'text/html');
  const code = [...doc.querySelectorAll('script:not([src])')]
    .map((node) => node.textContent ?? '')
    .join('\n');
  const charts = [
    {
      id: 'chartPlatformCompare',
      token: 'ctxCompare',
      labels: ['公众号阅读', '小红书曝光', '视频号播放', '知乎阅读', '蒲公英曝光'],
      names: ['本周', '上周'],
    },
    { id: 'chartEcomDonut', token: 'ctxEcom', names: ['GMV'] },
    { id: 'chartTopSkus', token: 'ctxSkus', names: ['GMV'] },
  ];
  for (const chart of charts) {
    const canvas = doc.getElementById(chart.id);
    if (!canvas) continue;
    const start = code.indexOf(`new Chart(${chart.token},`);
    const end = code.indexOf('new Chart(', start + 10);
    const block = start < 0 ? '' : code.slice(start, end < 0 ? undefined : end);
    const labels =
      chart.labels ??
      arrays(block, 'labels')[0]?.filter((v): v is string => typeof v === 'string') ??
      [];
    const data = arrays(block, 'data').slice(0, chart.names.length);
    const holder = doc.createElement('div');
    if (
      labels.length &&
      labels.length <= 100 &&
      data.length === chart.names.length &&
      data.every(
        (row) =>
          row.length === labels.length &&
          row.every((v) => v === null || (typeof v === 'number' && Number.isFinite(v))),
      )
    ) {
      holder.innerHTML = bars(
        labels,
        data.map((values, i) => ({ name: chart.names[i], values: values as (number | null)[] })),
      );
      if (chart.id === 'chartPlatformCompare') {
        const note = doc.createElement('p');
        note.textContent =
          '沿用归档图表中的指标值；多账号平台不代表账号合计。未采集的指标不记为零。';
        note.style.cssText = 'font-size:12px;color:#60758a;line-height:1.6';
        holder.append(note);
      }
    } else {
      holder.textContent = '此历史图表未包含完整数据，请查看下方明细。';
    }
    if (canvas.parentElement) canvas.parentElement.style.height = 'auto';
    canvas.replaceWith(holder);
  }
  // Static anchors work in a script-free report, including existing saved HTML.
  doc.body.id = 'report-top';
  for (const button of doc.querySelectorAll('button[onclick]')) {
    const panel = button.getAttribute('onclick')?.match(/^switchPanel\('([a-z]+)',\s*this\)$/)?.[1];
    if (panel) {
      const link = doc.createElement('a');
      link.className = button.className;
      link.href = panel === 'all' ? '#report-top' : `#panel-${panel}`;
      link.textContent = button.textContent;
      button.replaceWith(link);
    }
  }
  for (const node of doc.querySelectorAll(
    'script,iframe,object,embed,base,meta[http-equiv="refresh"]',
  ))
    node.remove();
  for (const node of doc.querySelectorAll('*'))
    for (const attr of [...node.attributes]) {
      if (
        attr.name.toLowerCase().startsWith('on') ||
        (['href', 'src', 'xlink:href', 'action'].includes(attr.name) &&
          /^\s*(javascript|vbscript):/i.test(attr.value))
      )
        node.removeAttribute(attr.name);
    }
  // srcdoc inherits the parent page's base URL. A bare #fragment would load
  // the console inside this script-free frame instead of scrolling the report.
  for (const link of doc.querySelectorAll('a[href^="#"],area[href^="#"]')) {
    link.setAttribute('href', `about:srcdoc${link.getAttribute('href')}`);
    link.setAttribute('target', '_self');
  }
  const style = doc.createElement('style');
  style.textContent =
    '.nav-tab{text-decoration:none;display:inline-block}.chart-container{overflow:auto}.chart-grid{grid-template-columns:repeat(auto-fit,minmax(min(100%,340px),1fr))}';
  doc.head.append(style);
  return '<!doctype html>' + doc.documentElement.outerHTML;
}
