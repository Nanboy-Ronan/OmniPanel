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
{
  // WorkBuddy connection, repair and consent pages (synthetic).
  const future = '2026-12-31T09:00:00+08:00';
  payloads['/api/integrations/workbuddy/status'] = { mcp_enabled: true, report_api_ready: false };
  payloads['/api/integrations/workbuddy/tokens'] = [
    {
      id: 'tok-1',
      name: 'WorkBuddy 自动授权',
      scopes: ['metadata:read', 'analytics:ecommerce', 'analytics:media', 'reports:read'],
      created_at: '2026-10-10T09:00:00+08:00',
      expires_at: '2026-10-15T10:00:00+08:00',
      revoked_at: null,
      authorization_expires_at: future,
    },
    {
      id: 'tok-2',
      name: '旧版手动令牌',
      scopes: ['metadata:read'],
      created_at: '2026-09-01T09:00:00+08:00',
      expires_at: '2026-10-01T09:00:00+08:00',
      revoked_at: '2026-09-20T09:00:00+08:00',
      authorization_expires_at: null,
    },
  ];
  payloads['/api/integrations/workbuddy/queries'] = [
    {
      timestamp: '2026-10-14 10:21:00',
      request_id: 'a1b2c3',
      tool: 'query_media_posts',
      outcome: 'success',
      duration_ms: 182,
      returned_rows: 50,
    },
    {
      timestamp: '2026-10-14 10:20:30',
      request_id: 'd4e5f6',
      tool: 'query_ecommerce_metrics',
      outcome: 'denied',
      duration_ms: 4,
      returned_rows: null,
    },
    {
      timestamp: '2026-10-14 10:20:00',
      request_id: '0789ab',
      tool: 'get_metric_definitions',
      outcome: 'rate_limited',
      duration_ms: 1,
      returned_rows: null,
    },
  ];
  payloads['/api/admin/integrations/workbuddy/tokens'] = [
    {
      ...payloads['/api/integrations/workbuddy/tokens'][0],
      user_id: 'u1',
      user_email: 'analyst@example.test',
      user_name: '示例分析员',
      kind: 'oauth',
    },
    {
      id: 'tok-3',
      name: '周报脚本',
      scopes: ['reports:read'],
      created_at: '2026-10-01T09:00:00+08:00',
      expires_at: future,
      revoked_at: null,
      authorization_expires_at: null,
      user_id: 'u2',
      user_email: 'admin@example.test',
      user_name: null,
      kind: 'token',
    },
  ];
  payloads['/api/collector/repair/readiness'] = {
    ready: false,
    browser_ready: false,
    sessions_writable: true,
    browsers_path_configured: false,
  };
  const targets = [
    {
      platform: 'xhs',
      account_id: 1,
      name: '品牌主账号',
      label: '小红书 · 品牌主账号',
      owner_id: 'u1',
      can_repair: true,
      verify_status: 'session_expired',
      verified_at: '2026-10-14T06:10:00',
      login_missing: false,
    },
    {
      platform: 'pugongying',
      account_id: 2,
      name: '产品实验室',
      label: '蒲公英 · 产品实验室',
      owner_id: null,
      can_repair: true,
      verify_status: 'success',
      verified_at: '2026-10-14T06:12:00',
      login_missing: false,
    },
    {
      platform: 'channels',
      account_id: 3,
      name: '生活方式',
      label: '视频号 · 生活方式',
      owner_id: 'u-off',
      can_repair: true,
      verify_status: null,
      verified_at: null,
      login_missing: true,
    },
  ];
  payloads['/api/collector/repair/targets'] = targets;
  targets[0].collect_pending = true;
  payloads['/api/admin/collector/requests'] = [];
  payloads['/api/collector/repair/requests/demo'] = {
    status: 'pending',
    targets,
    requested_at: '2026-10-14T10:00:00+08:00',
    expires_at: '2026-10-14T10:15:00+08:00',
  };
  payloads['/api/integrations/workbuddy/oauth/requests/demo'] = {
    scopes: ['metadata:read', 'analytics:media', 'reports:read'],
    can_authorize: true,
    client: 'WorkBuddy 桌面客户端',
    requested_at: '2026-10-14T10:00:00+08:00',
    expires_at: '2026-10-14T10:10:00+08:00',
  };
  payloads['/api/admin/users'] = [
    {
      id: 'u1',
      email: 'analyst@example.test',
      display_name: '示例分析员',
      role: 'analyst',
      is_active: true,
      wecom_userid: 'analyst',
      wecom_alert_enabled: true,
    },
    {
      id: 'u2',
      email: 'admin@example.test',
      display_name: null,
      role: 'admin',
      is_active: true,
      wecom_userid: null,
      wecom_alert_enabled: false,
    },
    {
      id: 'u-off',
      email: 'former@example.test',
      display_name: '已离职同事',
      role: 'viewer',
      is_active: false,
      wecom_userid: null,
      wecom_alert_enabled: false,
    },
  ];
  const weekly = payloads['/api/reports/weekly'];
  Object.assign(weekly[0], {
    job_state: 'delivery_unknown',
    narrative_status: 'success',
    worker_enabled: true,
  });
  Object.assign(weekly[3], {
    job_state: 'failed',
    narrative_status: 'pending',
    worker_enabled: true,
  });
  for (const row of weekly)
    Object.assign(
      payloads['/api/reports/weekly/' + row.id],
      row,
      row.job_state === 'delivery_unknown'
        ? { delivery_error: '发送结果未确认，请核对企业微信是否已收到再决定重试。' }
        : {},
    );
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
