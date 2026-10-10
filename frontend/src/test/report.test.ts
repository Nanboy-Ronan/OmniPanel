import { describe, expect, it } from 'vitest';
import { prepareReport, prepareReportHtml } from '../lib/report';
import renderedReport from './report-fixture.html?raw';
describe('historical report migration', () => {
  it('renders collected metrics when archived reports contain empty array slots', () => {
    const html = prepareReportHtml(
      '<canvas id="chartPlatformCompare"></canvas><script>new Chart(ctxCompare,{data:[12,,0,4,0],previous:{data:[8,,0,2,0]}});</script>',
    );
    const doc = new DOMParser().parseFromString(html, 'text/html');
    expect(doc.querySelector('svg')).not.toBeNull();
    expect(doc.body.textContent).toContain('本周 12');
    expect(doc.body.textContent).toContain('本周 未采集');
    expect(doc.body.textContent).toContain('上周 未采集');
    expect(doc.querySelectorAll('rect')).toHaveLength(8);
  });
  it('supports HTML produced by the real weekly report renderer with synthetic data', () => {
    const html = prepareReportHtml(renderedReport);
    const doc = new DOMParser().parseFromString(html, 'text/html');
    expect(doc.querySelectorAll('svg[role="img"]')).toHaveLength(3);
    expect(doc.querySelector('script,canvas,[onclick]')).toBeNull();
    expect(doc.body.textContent).toContain('GMV 774');
    expect(doc.body.textContent).not.toContain('未包含完整数据');
    expect(doc.querySelector('a[href="about:srcdoc#panel-ecommerce"]')).not.toBeNull();
  });
  it('renders all three archived Chart.js graphs without executing scripts', () => {
    const html =
      prepareReportHtml(`<html><head><script src="https://cdn.example.test/chart.js"></script></head><body><button onclick="switchPanel('ecommerce', this)">商城</button><section id="panel-ecommerce"><canvas id="chartPlatformCompare"></canvas><canvas id="chartEcomDonut"></canvas><canvas id="chartTopSkus"></canvas></section><script>
 new Chart(ctxCompare, {data:{labels:['unused'],datasets:[{data:[1,2,3,4,5],},{data:[0,1,2,3,4],}]}});
 new Chart(ctxEcom, {data:{labels:["jd [商城]","tmall",],datasets:[{data:[100,200,],}]}});
 new Chart(ctxSkus, {data:{labels:["<img src=x onerror=alert(1)>",],datasets:[{data:[-10,],}]}});
 </script></body></html>`);
    const doc = new DOMParser().parseFromString(html, 'text/html');
    expect(doc.querySelectorAll('svg')).toHaveLength(3);
    expect(doc.querySelector('script')).toBeNull();
    expect(doc.querySelector('canvas')).toBeNull();
    expect(doc.querySelector('img')).toBeNull();
    expect(doc.querySelector('a')?.getAttribute('href')).toBe('about:srcdoc#panel-ecommerce');
    expect(doc.body.textContent).toContain('GMV -10');
    expect(doc.body.textContent).toContain('本周 5');
  });
  it('keeps report navigation inside srcdoc even when the console URL has query parameters', () => {
    const html = prepareReportHtml(
      '<button onclick="switchPanel(\'all\', this)">总览</button><a href="#details" target="_blank">明细</a><a href="https://example.test/article" target="_blank">原文</a><section id="details">报告内容</section>',
    );
    const doc = new DOMParser().parseFromString(html, 'text/html');
    const links = [...doc.querySelectorAll('a')];
    expect(
      links
        .slice(0, 2)
        .map(
          (link) =>
            new URL(
              link.getAttribute('href')!,
              'https://dashboard.example.com/console/?appid=example&page=reports',
            ).href,
        ),
    ).toEqual(['about:srcdoc#report-top', 'about:srcdoc#details']);
    expect(links.slice(0, 2).every((link) => link.target === '_self')).toBe(true);
    expect(links[2].getAttribute('href')).toBe('https://example.test/article');
    expect(links[2].target).toBe('_blank');
    expect(doc.querySelector('script,base')).toBeNull();
  });
  it('rejects malformed chart data without evaluating expressions', () => {
    const html = prepareReportHtml(
      '<canvas id="chartEcomDonut"></canvas><script>new Chart(ctxEcom,{labels:["A"],data:[alert(1)]});</script><a onclick="alert(1)" href="javascript:alert(1)">link</a>',
    );
    const doc = new DOMParser().parseFromString(html, 'text/html');
    expect(doc.body.textContent).toContain('未包含完整数据');
    expect(doc.querySelector('script')).toBeNull();
    expect(doc.querySelector('[onclick]')).toBeNull();
    expect(doc.querySelector('a')?.hasAttribute('href')).toBe(false);
  });
  it('keeps already-static SVG reports and escaped narrative content intact', () => {
    const html = prepareReportHtml(
      '<p>&lt;script&gt;safe&lt;/script&gt;</p><svg><text>120</text></svg>',
    );
    expect(html).toContain('&lt;script&gt;safe&lt;/script&gt;');
    expect(new DOMParser().parseFromString(html, 'text/html').querySelectorAll('svg')).toHaveLength(
      1,
    );
  });
  it('lists platform sections for the console nav and hides the report chrome when embedded', () => {
    const { html, sections, hasNarrative } = prepareReport(renderedReport, { embedded: true });
    expect(sections).toEqual([
      { id: 'panel-ecommerce', label: '商城 (有赞/京东/天猫)', count: null },
    ]);
    expect(hasNarrative).toBe(true);
    expect(html).toContain('.nav-bar{display:none!important}');
    expect(prepareReportHtml(renderedReport)).not.toContain('.nav-bar{display:none');
    const counted = prepareReport(
      `<a class="nav-tab" onclick="x"></a><button class="nav-tab" onclick="switchPanel('xhs', this)">📕 小红书 <span class="pill-count">2</span></button><section id="panel-xhs"></section><button class="nav-tab" onclick="switchPanel('pgy', this)">缺失分区</button>`,
    );
    expect(counted.sections).toEqual([{ id: 'panel-xhs', label: '小红书', count: 2 }]);
  });
});
