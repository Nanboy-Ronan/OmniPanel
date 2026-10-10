/** Local-only synthetic UI review server. Never use for production/authentication. */
import http from 'node:http';
import { readFile } from 'node:fs/promises';
import { dirname, resolve, extname, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const dist = resolve(dirname(fileURLToPath(import.meta.url)), '../dist');
const metrics = { orders: 120, revenue: 30960, aov: 258, unique_customers: 92 };
const kpi = {
  day: metrics,
  prior_day: { orders: 100, revenue: 25000, aov: 250, unique_customers: 78 },
  week: { orders: 684, revenue: 171400, aov: 250.58, unique_customers: 502 },
  prior_week: { orders: 725, revenue: 186600, aov: 257.38, unique_customers: 544 },
  month: { orders: 342, revenue: 86125, aov: 251.83, unique_customers: 269 },
  prior_month: { orders: 301, revenue: 74750, aov: 248.34, unique_customers: 231 },
};
const batch = {
  id: 17,
  filename: '示例订单.csv',
  platform: 'jd',
  uploaded_at: '2026-10-03 15:00:00',
  row_count: 100,
  inserted_orders: 80,
  duplicate_rows: 20,
  invalid_rows: 0,
  status: 'completed',
  error_message: null,
};
const payloads = {
  '/api/auth/wecom/status': { enabled: true },
  '/api/auth/wecom/exchange': { access_token: 'synthetic-preview-only' },
  '/api/auth/me': {
    id: 'preview',
    email: 'preview@example.test',
    display_name: '演示账号',
    role: 'admin',
  },
  '/api/analysis/latest_order_date': { latest_order_date: '2026-10-03' },
  '/api/analysis/kpi-periods': kpi,
  '/api/data/freshness': {
    orders: { coverage_through: '2026-10-03', last_import_at: '2026-10-04 09:30:00' },
  },
  '/api/upload/batches': [
    batch,
    {
      ...batch,
      id: 16,
      status: 'failed',
      filename: '示例校验失败.csv',
      error_message: '示例：文件中缺少订单日期列。',
    },
  ],
};
Object.assign(
  payloads,
  Object.fromEntries(
    Object.entries(
      JSON.parse(
        await readFile(new URL('../src/test/workspace-fixtures.json', import.meta.url), 'utf8'),
      ),
    ).map(([path, value]) => ['/api' + path, value]),
  ),
);
// Include a long uninterrupted export filename for table layout review.
// A varied, explicitly synthetic cohort exercises bubble/chart interactions.
for (const path of [
  '/media/posts',
  '/media/xhs/posts',
  '/media/zhihu/posts',
  '/media/channels/posts',
]) {
  const base = payloads['/api' + path][0];
  payloads['/api' + path] = Array.from({ length: 24 }, (_, i) => {
    const reach = Math.round(400 + ((i * 791) % 7200));
    const interactions = Math.round(reach * (0.015 + ((i * 7) % 13) / 100));
    return {
      ...base,
      id: i + 1,
      title: ['新品体验', '使用指南', '用户故事', '品牌日记'][i % 4] + ' · 模拟内容 ' + (i + 1),
      account_name: ['品牌主账号', '产品实验室', '生活方式'][i % 3],
      publish_date: new Date(Date.UTC(2026, 9, 3 - ((i * 3) % 28))).toISOString().slice(0, 10),
      read_user_count: reach,
      read_count: reach,
      views: reach,
      plays: reach,
      reads: reach,
      likes: Math.round(interactions * 0.6),
      like_user: Math.round(interactions * 0.6),
      likes_thumb: Math.round(interactions * 0.6),
      shares: Math.round(interactions * 0.15),
      share_user_count: Math.round(interactions * 0.15),
      comments: Math.round(interactions * 0.1),
      comment_count: Math.round(interactions * 0.1),
      collects: Math.round(interactions * 0.15),
      collection_user: Math.round(interactions * 0.15),
    };
  });
}
const xhsPosts = payloads['/api/media/xhs/posts'];
payloads['/api/media/xhs/overview'] = {
  posts: xhsPosts.length,
  views: xhsPosts.reduce((sum, post) => sum + post.views, 0),
  engagement: xhsPosts.reduce(
    (sum, post) => sum + post.likes + post.comments + post.shares + post.collects,
    0,
  ),
};
const pgyBase = payloads['/api/media/pgy/notes'][0];
payloads['/api/media/pgy/notes'] = Array.from({ length: 29 }, (_, i) => ({
  ...pgyBase,
  id: i + 1,
  note_title: '模拟合作内容 · ' + (i + 1),
  blogger_nickname: [
    '生活方式体验官',
    '产品观察员',
    '品牌合作达人',
    '长名称用于布局检查的内容创作者',
  ][i % 4],
  cooperation_name: ['春季新品体验', '暑期品牌合作', '年度内容共创计划'][i % 3],
  publish_date: new Date(Date.UTC(2026, 6, 6 + i * 3)).toISOString().slice(0, 10),
  blogger_quote: i === 28 ? null : 300 + ((i * 173) % 1500),
  service_fee: i === 28 ? null : 30 + ((i * 17) % 150),
  impressions: 2000 + ((i * 1777) % 48000),
  interactions: 20 + ((i * 43) % 820),
}));
payloads['/api/upload/batches'].push({
  ...batch,
  id: 18,
  filename:
    '订单明细信息--2026-06-01-00-00-00-TO-2026-06-30-23-59-59--' +
    'example-export-'.repeat(8) +
    '.xlsx',
  status: 'failed',
  error_message: '示例：文件中缺少订单日期列。',
});
{
  const html = await readFile(new URL('../src/test/report-fixture.html', import.meta.url), 'utf8');
  const weeks = [
    ['2026-09-28', 'success', true, null],
    ['2026-09-21', 'success', true, null],
    ['2026-09-14', 'partial', false, '示例：小红书数据概览未采集。'],
    ['2026-09-07', 'error', false, '示例：公众号接口超时，报告未生成。'],
    ['2026-08-31', 'success', true, null],
  ];
  payloads['/api/reports/weekly'] = weeks.map(([start, status, sent], i) => {
    const end = new Date(Date.parse(start) + 6 * 86400000).toISOString().slice(0, 10);
    return {
      id: i + 1,
      week_start: start,
      week_end: end,
      generated_at: `${new Date(Date.parse(start) + 8 * 86400000).toISOString().slice(0, 10)}T09:00:00`,
      status,
      wecom_sent: sent,
    };
  });
  payloads['/api/reports/weekly'].forEach((row, i) => {
    payloads['/api/reports/weekly/' + row.id] = {
      ...row,
      narrative: null,
      html_content: row.status === 'error' ? null : html,
      error_message: weeks[i][3],
    };
  });
}
{
  const ua =
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36 wxwork/4.1';
  const at = (h, m) => `2026-10-10 ${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:00`;
  payloads['/api/admin/logs'] = [
    {
      id: 9,
      email: null,
      action: 'wecom_login_failed',
      timestamp: at(9, 41),
      ip: '203.0.113.24',
      user_agent: ua,
      detail: { reason: 'invalid_state' },
    },
    {
      id: 8,
      email: 'newstaff@example.test',
      action: 'wecom_login_failed',
      timestamp: at(9, 38),
      ip: '198.51.100.7',
      user_agent: ua,
      detail: { reason: 'pending_approval', wecom_userid: 'newstaff' },
    },
    {
      id: 7,
      email: 'preview@example.test',
      action: 'export_client',
      timestamp: at(9, 30),
      ip: '198.51.100.2',
      user_agent: ua,
      detail: { source: '客户列表', rows: 92, columns: ['mobile', 'receiver'] },
    },
    {
      id: 6,
      email: 'preview@example.test',
      action: 'view_customer',
      timestamp: at(9, 29),
      ip: '198.51.100.2',
      user_agent: ua,
      detail: { customer_id: '13800000001' },
    },
    {
      id: 5,
      email: 'preview@example.test',
      action: 'xhs_account_update',
      timestamp: at(9, 20),
      ip: '198.51.100.2',
      user_agent: ua,
      detail: { account_id: 2, changed: ['pgy_enabled'] },
    },
    {
      id: 4,
      email: 'preview@example.test',
      action: 'upload',
      timestamp: at(9, 12),
      ip: '198.51.100.2',
      user_agent: ua,
      detail: { filename: '示例订单.csv', batch_id: 17 },
    },
    {
      id: 3,
      email: 'preview@example.test',
      action: 'sql_query',
      timestamp: at(9, 5),
      ip: '198.51.100.2',
      user_agent: ua,
      detail: { sql: 'SELECT platform, count(*) FROM orders GROUP BY platform', row_count: 3 },
    },
    {
      id: 2,
      email: 'preview@example.test',
      action: 'wecom_login',
      timestamp: at(9, 0),
      ip: '198.51.100.2',
      user_agent: ua,
      detail: { wecom_userid: 'preview' },
    },
    {
      id: 1,
      email: 'preview@example.test',
      action: 'logout',
      timestamp: at(8, 55),
      ip: '198.51.100.2',
      user_agent:
        'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1',
      detail: null,
    },
  ];
}
const types = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript',
  '.css': 'text/css',
  '.svg': 'image/svg+xml',
};
http
  .createServer(async (req, res) => {
    const url = new URL(req.url, 'http://127.0.0.1:5180');
    res.setHeader('Cache-Control', 'no-store');
    if (url.pathname === '/api/auth/wecom/start') {
      res.writeHead(302, { Location: '/console/?code=synthetic&state=synthetic' });
      res.end();
      return;
    }
    if (url.pathname in payloads) {
      res.setHeader('Content-Type', 'application/json');
      if (url.pathname === '/api/orders_all/' || url.pathname === '/api/analysis/customers')
        res.setHeader('X-Total-Count', '1');
      if (url.pathname === '/api/admin/logs')
        res.setHeader('X-Total-Count', String(payloads[url.pathname].length));
      let payload = payloads[url.pathname];
      if (url.pathname === '/api/data/source-status') {
        const source = url.searchParams.get('source');
        payload = {
          ...payload,
          source,
          records: source === 'pgy' ? 29 : source === 'orders' ? 120 : 24,
          first_date: '2026-07-06',
          last_date: '2026-10-03',
          date_basis: source === 'orders' ? '订单日期' : '发布日期',
          updated_at: '2026-10-04T09:30:00',
          runs: ['pgy', 'xhs', 'channels'].includes(source)
            ? [
                {
                  account_id: 1,
                  content_type: null,
                  status: 'success',
                  started_at: '2026-10-04T22:30:00',
                  finished_at: '2026-10-04T22:31:00',
                },
              ]
            : [],
        };
      }
      if (url.pathname === '/api/media/pgy/notes') {
        payload = payload.filter(
          (r) =>
            (!url.searchParams.get('start_date') ||
              r.publish_date >= url.searchParams.get('start_date')) &&
            (!url.searchParams.get('end_date') ||
              r.publish_date <= url.searchParams.get('end_date')),
        );
      }
      res.end(JSON.stringify(payload));
      return;
    }
    if (!url.pathname.startsWith('/console/')) {
      res.writeHead(404);
      res.end('Fixture preview only: /console/');
      return;
    }
    const asset = url.pathname.slice('/console/'.length) || 'index.html';
    const file = resolve(dist, asset);
    if (!file.startsWith(dist + sep)) {
      res.writeHead(403);
      res.end();
      return;
    }
    try {
      let content = await readFile(file);
      if (asset === 'index.html') {
        content = Buffer.from(
          content
            .toString()
            .replace(
              '<body>',
              '<body><div style="position:fixed;bottom:0;right:0;z-index:1000;background:#fff3cd;color:#654a13;padding:5px 12px;font:12px sans-serif">本地预览 · 全部为模拟数据</div>',
            ),
        );
      }
      res.setHeader('Content-Type', types[extname(file)] || 'application/octet-stream');
      res.end(content);
    } catch {
      res.writeHead(404);
      res.end('Run npm run build first.');
    }
  })
  .listen(5180, '127.0.0.1', () =>
    console.log('SYNTHETIC DATA ONLY: http://127.0.0.1:5180/console/'),
  );
