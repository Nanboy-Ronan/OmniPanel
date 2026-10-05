import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  allPosts,
  asRecord,
  endpoint,
  numeric,
  recordSchema,
  rowsSchema,
  text,
  useAction,
  useFilters,
  useResource,
  type Row,
} from '../lib/resources';
import { request } from '../lib/api';
import {
  contentConfig,
  groupContent,
  matchTopics,
  normalizeContent,
  type ContentPlatform,
} from '../lib/content';
import { label } from '../lib/labels';
import { navigate, useLocationSearch } from '../lib/navigation';
import { AccountManager, FileImport } from '../components/Accounts';
import {
  ConfirmAction,
  Heading,
  QueryView,
  RangeFilter,
  RecordTable,
  Stats,
  Tabs,
} from '../components/workspace';
import { EmptyState, ErrorState, Panel } from '../components/ui';
import { SourceStatus, useSourceStatus } from '../components/SourceStatus';
import { AnalysisLink } from '../components/AnalysisLink';
import ContentExplorer from '../components/ContentExplorer';
import { SeriesChart, MetricScatter } from '../components/charts';
export default function MediaPage({
  platform: requestedPlatform,
  admin,
}: {
  platform: ContentPlatform;
  admin: boolean;
}) {
  const routeParams = new URLSearchParams(useLocationSearch());
  const trafficWorkspace = requestedPlatform === 'traffic';
  const platform =
    trafficWorkspace && routeParams.get('source') !== 'export' ? 'wechat' : requestedPlatform;
  const cfg = contentConfig[platform];
  const coverage = useSourceStatus(
    platform,
    routeParams.get('account') || '',
    platform === 'zhihu' ? (routeParams.get('content') === 'qa' ? 'qa' : 'article') : '',
  );
  const { values, valid, signature, params } = useFilters({
    dataThrough: coverage.data?.last_date,
    waitingForCoverage: coverage.isPending,
  });
  const account = values.account_id;
  const contentType = params.get('content') === 'qa' ? 'qa' : 'article';
  const tab = params.get('view') || 'explore';
  const setTab = (view: string) => navigate({ view });
  const [minimum, setMinimum] = useState(100);
  const [detail, setDetail] = useState<Row | null>(null);
  useEffect(() => setDetail(null), [platform, account]);
  const sync = useAction();
  const accounts = useResource(cfg.accounts ?? '/media/accounts', rowsSchema, !!cfg.accounts);
  const filters = {
    start_date: values.start_date,
    end_date: values.end_date,
    account_id: account,
    content_type: platform === 'zhihu' ? contentType : undefined,
  };
  const postsPath = platform === 'traffic' ? '/media/traffic' : `${cfg.base}/posts`;
  const path = endpoint(postsPath, filters);
  const posts = useQuery({
    queryKey: ['resource', path],
    queryFn: ({ signal }) =>
      ['wechat', 'traffic'].includes(platform)
        ? request(path, rowsSchema, { signal, timeoutMs: 45000 })
        : allPosts(postsPath, filters, signal),
    enabled: valid,
  });
  const overview = useResource(
    endpoint(platform === 'traffic' ? '/media/traffic/overview' : `${cfg.base}/overview`, filters),
    recordSchema,
    valid,
  );
  const noContent =
    posts.isSuccess &&
    (posts.data.length === 0 || (platform === 'wechat' && overview.data?.posts === 0));
  const rows = normalizeContent(posts.data ?? [], platform).filter(
    (row) => !values.q || text(row.title).toLowerCase().includes(values.q.toLowerCase()),
  );
  const sorted = [...rows].sort(
    (a, b) => (numeric(b[cfg.read]) ?? 0) - (numeric(a[cfg.read]) ?? 0),
  );
  const tabs = [
    { key: 'explore', label: '可视分析' },
    { key: 'overview', label: '内容排行' },
    { key: 'engagement', label: '互动效率' },
    { key: 'trend', label: '发布趋势' },
    ...(platform === 'xhs' ? [{ key: 'funnel', label: '转化与类型' }] : []),
    ...(platform === 'channels' ? [{ key: 'wecom', label: '企微联动' }] : []),
    ...(platform === 'wechat' ? [{ key: 'sources', label: '流量来源' }] : []),
    { key: 'topics', label: '话题分析' },
    { key: 'list', label: '全部明细' },
  ];
  return (
    <>
      <Heading
        title={trafficWorkspace ? '公众号流量' : cfg.title}
        description={
          trafficWorkspace && platform === 'wechat'
            ? '自动同步的阅读、分享与互动表现；每篇文章取所选指标日期内的最新快照。'
            : platform === 'traffic'
              ? '公众号后台导出数据的累计表现；日期按文章发布日期筛选。'
              : '使用完整筛选区间的数据分析内容表现与互动效率。'
        }
        action={
          <div className="heading-actions">
            <AnalysisLink
              filters={{
                start: values.start_date,
                end: values.end_date,
                ...(trafficWorkspace ? { source: platform === 'wechat' ? 'api' : 'export' } : {}),
              }}
            />
            <button
              disabled={posts.isFetching || overview.isFetching}
              onClick={() => {
                void coverage.refetch();
                void posts.refetch();
                void overview.refetch();
              }}
            >
              刷新数据
            </button>
          </div>
        }
      />
      {trafficWorkspace && (
        <section className="traffic-source-picker" aria-label="公众号流量数据来源">
          <div className="bi-segment">
            <button
              aria-pressed={platform === 'wechat'}
              onClick={() =>
                navigate({ source: 'api', start: null, end: null, q: null, view: null })
              }
            >
              自动同步数据
            </button>
            <button
              aria-pressed={platform === 'traffic'}
              onClick={() =>
                navigate({ source: 'export', start: null, end: null, q: null, view: null })
              }
            >
              后台导出数据
            </button>
          </div>
          <p>
            {platform === 'wechat'
              ? '当前使用公众号 API 数据。阅读人数为各篇文章读者数之和，不是跨文章去重人数；与后台导出的累计指标分开统计。'
              : '当前使用后台文件导出的累计数据；不会与自动同步数据相加。'}
          </p>
        </section>
      )}
      {!params.has('start') && !params.has('end') && coverage.data?.last_date && (
        <p className="footnote">
          默认展示最新有数据的 30 天：{values.start_date} — {values.end_date}
          ；不代表截至今天的实时数据。
        </p>
      )}
      {platform === 'traffic' && (
        <div className="media-source-note">
          <strong>数据来源：公众号后台导出 · 累计口径</strong>
          <p>
            此页仅展示已导入的后台导出记录，不包含自动同步的 API
            指标。自动同步数据请查看「公众号内容」。
          </p>
          <button
            onClick={() => navigate({ source: 'api', start: null, end: null, q: null, view: null })}
          >
            查看公众号 API 数据
          </button>
        </div>
      )}
      <RangeFilter key={signature} values={values} platform={false} search>
        {cfg.accounts && (
          <label>
            账号
            <select
              aria-label="账号"
              value={account}
              onChange={(e) => {
                navigate({ account: e.target.value || null });
                setDetail(null);
              }}
            >
              <option value="">全部账号</option>
              {accounts.data?.map((row) => (
                <option key={text(row.id)} value={text(row.id)}>
                  {text(row.name)}
                </option>
              ))}
            </select>
          </label>
        )}
      </RangeFilter>
      {(accounts.isError || platform === 'zhihu') && (
        <div className="inline-form">
          {accounts.isError && (
            <ErrorState error={accounts.error} retry={() => void accounts.refetch()} />
          )}
          {platform === 'zhihu' && (
            <Tabs
              value={contentType}
              onChange={(value) => navigate({ content: value })}
              items={[
                { key: 'article', label: '文章' },
                { key: 'qa', label: '问答' },
              ]}
            />
          )}
        </div>
      )}
      {sync.isError && <ErrorState error={sync.error} />}{' '}
      {sync.isSuccess && (
        <p role="status" className="success-notice">
          {asRecord(sync.data).status === 'partial'
            ? '部分账号同步失败，请核对账号状态后重试。'
            : '微信同步已完成。'}
        </p>
      )}
      <SourceStatus
        source={platform}
        account={account}
        contentType={platform === 'zhihu' ? contentType : ''}
        visibleCount={posts.isSuccess ? (noContent ? 0 : rows.length) : undefined}
        admin={admin}
      />
      <QueryView query={overview}>
        {(data) =>
          noContent ? null : (
            <>
              <Stats
                items={Object.entries(data)
                  .filter(([, value]) => typeof value === 'number')
                  .map(([key, value]) => ({
                    title: label(key),
                    value: key.endsWith('_rate') ? `${(Number(value) * 100).toFixed(1)}%` : value,
                  }))}
              />
              {values.q && (
                <p className="footnote">
                  上方指标覆盖所选账号与日期；下方图表及明细另按标题关键词筛选。
                </p>
              )}
            </>
          )
        }
      </QueryView>
      {!noContent && <Tabs items={tabs} value={tab} onChange={setTab} />}
      <QueryView query={posts}>
        {() =>
          noContent ? (
            platform === 'traffic' ? (
              <TrafficEmpty account={account} />
            ) : (
              <EmptyState title="当前账号与日期范围内没有内容记录">
                请核对上方数据覆盖范围，或点击“查看最近有数据的 30
                天”恢复历史数据。没有记录不代表阅读或播放量为零。
              </EmptyState>
            )
          ) : (
            <>
              {tab === 'explore' && (
                <ContentExplorer
                  key={`${signature}:${account}`}
                  rows={rows}
                  platform={platform}
                  end={values.end_date}
                  onDetail={(row) => {
                    setDetail(row);
                    requestAnimationFrame(() =>
                      document
                        .getElementById('content-detail')
                        ?.scrollIntoView({ behavior: 'smooth', block: 'start' }),
                    );
                  }}
                />
              )}
              {tab === 'overview' && (
                <Panel
                  title="内容表现排行"
                  subtitle="切换指标后，按当前筛选区间的全部内容重新排序。"
                >
                  <SeriesChart rows={rows} x="title" fields={cfg.fields} />
                  <details className="data-disclosure">
                    <summary>查看{label(cfg.read)}前 20 名明细</summary>
                    <RecordTable
                      rows={sorted.slice(0, 20)}
                      columns={[
                        'title',
                        'publish_date',
                        cfg.read,
                        'total_engagement',
                        'engagement_rate',
                      ]}
                      caption="内容排行"
                      onSelect={setDetail}
                    />
                  </details>
                </Panel>
              )}
              {tab === 'engagement' && (
                <Panel
                  title="互动效率"
                  subtitle="互动率以阅读 / 播放量为分母，分母为零时显示空值；比例单位为百分比。"
                >
                  <label className="field-inline">
                    最低阅读 / 播放量
                    <input
                      type="number"
                      min={0}
                      value={minimum}
                      onChange={(e) => setMinimum(Math.max(0, Number(e.target.value)))}
                    />
                  </label>
                  <MetricScatter
                    rows={rows.filter((row) => (numeric(row[cfg.read]) ?? 0) >= minimum)}
                    fields={[
                      cfg.read,
                      'engagement_rate',
                      ...cfg.fields,
                      ...cfg.interactions.map((key) => `${key}_rate`),
                      'follower_rate',
                      'read_finish_rate',
                      'cover_click_rate',
                      'completion_rate',
                    ]}
                  />
                  <SeriesChart
                    rows={[...rows]
                      .filter((row) => (numeric(row[cfg.read]) ?? 0) >= minimum)
                      .sort(
                        (a, b) =>
                          (numeric(b.engagement_rate) ?? -1) - (numeric(a.engagement_rate) ?? -1),
                      )}
                    x="title"
                    fields={['engagement_rate', ...cfg.interactions.map((key) => `${key}_rate`)]}
                  />
                  <RecordTable
                    rows={rows}
                    columns={[
                      'title',
                      cfg.read,
                      'total_engagement',
                      'engagement_rate',
                      ...cfg.interactions.map((key) => `${key}_rate`),
                    ]}
                    caption="互动效率"
                  />
                </Panel>
              )}
              {tab === 'trend' && (
                <>
                  <Panel title="按发布日期的内容表现">
                    <SeriesChart
                      rows={groupContent(rows, 'date', cfg.fields)}
                      x="date"
                      fields={['posts', ...cfg.fields]}
                      kind="line"
                    />
                    <RecordTable
                      rows={groupContent(rows, 'date', cfg.fields)}
                      caption="发布日期趋势"
                    />
                  </Panel>
                  <Panel title="发布星期表现">
                    <SeriesChart
                      rows={groupContent(rows, 'weekday', cfg.fields)}
                      x="weekday"
                      fields={['posts', ...cfg.fields]}
                    />
                  </Panel>
                </>
              )}
              {tab === 'funnel' && (
                <>
                  <Panel
                    title="曝光与互动"
                    subtitle="各环节为累计次数，不能视为严格的用户转化漏斗。"
                  >
                    <SeriesChart
                      rows={['impressions', 'views', 'total_engagement', 'new_followers'].map(
                        (key) => ({
                          stage: label(key),
                          value: rows.reduce((sum, row) => sum + (numeric(row[key]) ?? 0), 0),
                        }),
                      )}
                      x="stage"
                      fields={['value']}
                    />
                  </Panel>
                  <Panel title="内容类型">
                    <SeriesChart
                      rows={groupContent(rows, 'genre', cfg.fields)}
                      x="genre"
                      fields={['posts', ...cfg.fields]}
                    />
                    <RecordTable
                      rows={groupContent(rows, 'genre', cfg.fields)}
                      caption="内容类型"
                    />
                  </Panel>
                </>
              )}
              {tab === 'wecom' && (
                <Panel title="企微联动">
                  <RecordTable
                    rows={rows}
                    columns={[
                      'title',
                      'plays',
                      'wecom_link_clicks',
                      'wecom_link_click_users',
                      'added_to_contacts',
                      'added_to_contacts_users',
                      'avg_watch_duration',
                      'completion_rate',
                      'forwards_chat_moments',
                      'set_as_ringtone',
                      'set_as_status',
                      'set_as_moments_cover',
                    ]}
                    caption="视频号企微联动"
                  />
                </Panel>
              )}
              {tab === 'sources' && <Sources filters={filters} />}
              {tab === 'topics' && (
                <Topics
                  rows={rows}
                  fields={cfg.fields}
                  sourcesPath={
                    platform === 'wechat' ? endpoint('/media/source-by-post', filters) : undefined
                  }
                />
              )}
              {tab === 'list' && (
                <Panel title="内容明细">
                  <RecordTable
                    rows={rows}
                    defaultColumns={[
                      'title',
                      'publish_date',
                      cfg.read,
                      'total_engagement',
                      'engagement_rate',
                      'account_id',
                    ]}
                    caption={cfg.title}
                    onSelect={setDetail}
                  />
                </Panel>
              )}
              {detail && (
                <div id="content-detail">
                  <Panel
                    title={text(detail.title)}
                    action={<button onClick={() => setDetail(null)}>关闭详情</button>}
                  >
                    {platform === 'wechat' ? (
                      <PostHistory id={Number(detail.id)} />
                    ) : (
                      <RecordTable rows={[detail]} caption="内容完整字段" />
                    )}
                  </Panel>
                </div>
              )}
            </>
          )
        }
      </QueryView>
      {platform !== 'traffic' && (admin || platform !== 'wechat') && (
        <details className="data-tools">
          <summary>数据导入与账号管理</summary>
          {cfg.accounts && admin && (
            <AccountManager path={cfg.accounts} admin={admin} xhs={platform === 'xhs'} />
          )}
          {!['wechat', 'traffic'].includes(platform) && (
            <FileImport
              key={`${platform}-${account}-${contentType}`}
              path={`${cfg.base}/upload`}
              fields={
                platform === 'zhihu' ? { content_type: contentType } : { account_id: account }
              }
              disabled={platform !== 'zhihu' && !account}
            />
          )}
          {platform === 'xhs' && (
            <FileImport
              path="/media/xhs/upload_overview"
              fields={{ account_id: account }}
              disabled={!account}
              accept=".json"
              title="导入账号概览 JSON"
            />
          )}
          {platform === 'wechat' && admin && (
            <ConfirmAction
              title="同步微信数据"
              description={`同步 ${values.start_date} 至 ${values.end_date} 的微信数据，可能需要几分钟。`}
              onConfirm={() =>
                sync.mutate({
                  path: '/media/wechat/sync',
                  body: {
                    start_date: values.start_date,
                    end_date: values.end_date,
                    ...(account ? { account_id: Number(account) } : {}),
                  },
                  timeoutMs: 270000,
                })
              }
              busy={sync.isPending}
            />
          )}
        </details>
      )}
    </>
  );
}
function TrafficEmpty({ account }: { account: string }) {
  const coverage = useResource(
    endpoint('/media/traffic/overview', { account_id: account }),
    recordSchema,
  );
  return (
    <Panel title="累计流量数据状态">
      <QueryView query={coverage}>
        {(data) =>
          Number(data.articles) > 0 ? (
            <EmptyState title="当前发布日期范围内没有导出记录">
              所选账号的全部时期共有 {String(data.articles)} 篇导出记录，请扩大上方发布日期范围。
            </EmptyState>
          ) : (
            <EmptyState title={account ? '所选账号尚无后台导出记录' : '尚无公众号后台导出记录'}>
              已检查全部日期。这表示此数据源尚无记录，并不代表公众号阅读量为零。请使用上方入口查看自动同步的公众号
              API 数据。
            </EmptyState>
          )
        }
      </QueryView>
    </Panel>
  );
}
function Sources({ filters }: { filters: Record<string, string | undefined> }) {
  const query = useResource(endpoint('/media/source-breakdown', filters), recordSchema);
  return (
    <Panel title="阅读来源">
      <QueryView query={query}>
        {(data) => {
          const rows = Object.entries(data).map(([scene, count]) => ({ scene, count }));
          return (
            <>
              <SeriesChart rows={rows} x="scene" fields={['count']} />
              <RecordTable rows={rows} caption="阅读来源" />
            </>
          );
        }}
      </QueryView>
    </Panel>
  );
}
function PostHistory({ id }: { id: number }) {
  const query = useResource(`/media/posts/${id}/metrics`, rowsSchema);
  return (
    <QueryView query={query}>
      {(rows) => (
        <>
          <SeriesChart
            rows={rows}
            x="metric_date"
            fields={['read_user_count', 'share_user_count', 'like_user', 'comment_count']}
            kind="line"
          />
          <RecordTable rows={rows} caption="内容每日指标" />
        </>
      )}
    </QueryView>
  );
}
function Topics({
  rows,
  fields,
  sourcesPath,
}: {
  rows: Row[];
  fields: string[];
  sourcesPath?: string;
}) {
  const [topics, setTopics] = useState([
    { name: '新品发布', keywords: '新品,上市,发布' },
    { name: '行业报告', keywords: '行业,报告,趋势' },
    { name: '促销活动', keywords: '折扣,优惠,特惠' },
  ]);
  const [selected, setSelected] = useState('');
  const sources = useResource(sourcesPath ?? '/media/source-by-post', recordSchema, !!sourcesPath);
  const expanded: Row[] = rows.flatMap((row) =>
    matchTopics(text(row.title), topics).map((topic) => ({ ...row, topic })),
  );
  const grouped = groupContent(expanded, 'topic', fields);
  const matrix = expanded.flatMap((row) =>
    Object.entries(asRecord(sources.data?.[String(row.id)])).map(([scene, count]) => ({
      topic: row.topic,
      scene,
      count,
    })),
  );
  const matrixRows = Object.values(
    matrix.reduce<Record<string, Row>>((out, row) => {
      const key = JSON.stringify([row.topic, row.scene]);
      out[key] ??= { topic: row.topic, scene: row.scene, count: 0 };
      out[key].count = Number(out[key].count) + Number(row.count);
      return out;
    }, {}),
  );
  return (
    <>
      <Panel
        title="话题规则"
        subtitle="标题关键词不区分大小写；一篇内容可匹配多个话题，话题合计可能重复。"
      >
        <div className="editor-form">
          {topics.map((topic, index) => (
            <div className="inline-form" key={index}>
              <input
                aria-label={`话题 ${index + 1}`}
                value={topic.name}
                onChange={(e) =>
                  setTopics(
                    topics.map((t, i) => (i === index ? { ...t, name: e.target.value } : t)),
                  )
                }
              />
              <input
                aria-label={`关键词 ${index + 1}`}
                value={topic.keywords}
                onChange={(e) =>
                  setTopics(
                    topics.map((t, i) => (i === index ? { ...t, keywords: e.target.value } : t)),
                  )
                }
              />
              <button onClick={() => setTopics(topics.filter((_, i) => i !== index))}>移除</button>
            </div>
          ))}
          <button
            disabled={topics.length >= 6}
            onClick={() =>
              setTopics([...topics, { name: `话题${topics.length + 1}`, keywords: '' }])
            }
          >
            添加话题
          </button>
        </div>
      </Panel>
      <Panel title="话题表现">
        <SeriesChart rows={grouped} x="topic" fields={['posts', ...fields]} />
        <RecordTable
          rows={grouped}
          caption="话题汇总"
          onSelect={(row) => setSelected(text(row.topic))}
        />
        {selected && (
          <RecordTable
            rows={expanded.filter((row) => row.topic === selected)}
            caption={`${selected}明细`}
          />
        )}
      </Panel>
      {sourcesPath && (
        <Panel title="话题 × 来源">
          <QueryView query={sources}>
            {() => <RecordTable rows={matrixRows} caption="话题来源矩阵" />}
          </QueryView>
        </Panel>
      )}
    </>
  );
}
