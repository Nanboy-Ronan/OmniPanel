import { useQuery } from '@tanstack/react-query';
import {
  allPosts,
  asRecord,
  asRows,
  endpoint,
  rowsSchema,
  text,
  useFilters,
  useResource,
  type Row,
} from '../lib/resources';
import { navigate } from '../lib/navigation';
import { pgyRows } from '../lib/pgy';
import { AccountManager, FileImport } from '../components/Accounts';
import { Heading, QueryView, RangeFilter, RecordTable, Tabs } from '../components/workspace';
import { EmptyState, Panel } from '../components/ui';
import { SourceStatus } from '../components/SourceStatus';
import { AnalysisLink } from '../components/AnalysisLink';
import PgyAnalytics, { PgyGroups } from '../components/PgyAnalytics';
export default function PgyPage({ admin }: { admin: boolean }) {
  const { values, valid, signature, params } = useFilters();
  const allTime =
    params.get('range') === 'all' ||
    (!params.has('start') && !params.has('end') && params.get('range') !== 'dates');
  const tab = params.get('view') || 'overview';
  const accounts = useResource('/media/xhs/accounts', rowsSchema);
  const filters = {
    account_id: values.account_id,
    ...(!allTime ? { start_date: values.start_date, end_date: values.end_date } : {}),
  };
  const path = endpoint('/media/pgy/notes', filters);
  const query = useQuery({
    queryKey: ['resource', path],
    queryFn: ({ signal }) => allPosts('/media/pgy/notes', filters, signal),
    enabled: allTime || valid,
  });
  const rows = pgyRows(query.data ?? []).filter(
    (r) =>
      (!values.q ||
        ['note_title', 'blogger_nickname', 'cooperation_name'].some((k) =>
          text(r[k]).toLowerCase().includes(values.q.toLowerCase()),
        )) &&
      (!params.get('blogger') || r.blogger_nickname === params.get('blogger')) &&
      (!params.get('campaign') || r.cooperation_name === params.get('campaign')),
  );
  const showAll = () =>
    navigate({
      range: 'all',
      start: null,
      end: null,
      q: null,
      blogger: null,
      campaign: null,
      note: null,
    });
  const onNote = (r: Row) => navigate({ note: String(r.id) });
  const onBlogger = (blogger: string) => navigate({ blogger, note: null });
  const onCampaign = (campaign: string) => navigate({ campaign, note: null });
  return (
    <div className="pgy-workspace">
      <Heading
        title="蒲公英合作"
        description="衡量合作投入、达人贡献与内容效率，逐步追查到单篇笔记。"
        action={<AnalysisLink />}
      />
      <div className="pgy-scope">
        <div className="bi-segment" aria-label="合作日期范围">
          <button aria-pressed={allTime} onClick={showAll}>
            全部合作
          </button>
          <button
            aria-pressed={!allTime}
            onClick={() =>
              navigate({ range: 'dates', start: values.start_date, end: values.end_date })
            }
          >
            按发布日期
          </button>
        </div>
        <label>
          账号{' '}
          <select
            value={values.account_id}
            onChange={(e) =>
              navigate({
                account: e.target.value || null,
                blogger: null,
                campaign: null,
                note: null,
              })
            }
          >
            <option value="">全部账号</option>
            {accounts.data?.map((r) => (
              <option key={text(r.id)} value={text(r.id)}>
                {text(r.name)}
              </option>
            ))}
          </select>
        </label>
        {allTime && (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              navigate({ q: String(new FormData(e.currentTarget).get('q')), note: null });
            }}
          >
            <input
              aria-label="搜索合作"
              name="q"
              key={values.q}
              defaultValue={values.q}
              placeholder="搜索笔记、达人或项目"
            />
            <button>搜索</button>
          </form>
        )}
      </div>
      {!allTime && <RangeFilter key={signature} values={values} platform={false} search />}
      <SourceStatus
        source="pgy"
        account={values.account_id}
        visibleCount={query.isSuccess ? rows.length : undefined}
        onAll={showAll}
        admin={admin}
      />
      <div className="analysis-context">
        <span>
          {allTime ? '全部发布日期（含日期缺失记录）' : `${values.start_date} — ${values.end_date}`}
          {values.q && ` / 搜索：${values.q}`}
        </span>
        {params.get('blogger') && (
          <button onClick={() => navigate({ blogger: null, note: null })}>
            达人：{params.get('blogger')} ×
          </button>
        )}
        {params.get('campaign') && (
          <button onClick={() => navigate({ campaign: null, note: null })}>
            项目：{params.get('campaign')} ×
          </button>
        )}
        {(params.get('blogger') || params.get('campaign') || values.q) && (
          <button onClick={() => navigate({ blogger: null, campaign: null, q: null, note: null })}>
            清除分析筛选
          </button>
        )}
      </div>
      <Tabs
        value={tab}
        onChange={(view) => navigate({ view })}
        items={[
          { key: 'overview', label: '投效概览' },
          { key: 'notes', label: '合作明细' },
          { key: 'bloggers', label: '达人对比' },
          { key: 'campaigns', label: '合作项目' },
          { key: 'audience', label: '受众画像' },
          { key: 'components', label: '组件数据' },
        ]}
      />
      <QueryView query={query}>
        {() =>
          rows.length ? (
            <>
              {tab === 'overview' && (
                <PgyAnalytics
                  rows={rows}
                  selected={rows.find((r) => String(r.id) === params.get('note'))}
                  onNote={onNote}
                  onBlogger={onBlogger}
                  onCampaign={onCampaign}
                />
              )}
              {tab === 'bloggers' && (
                <Panel title="达人对比" subtitle="点击达人，筛选指标与笔记明细">
                  <PgyGroups rows={rows} field="blogger_nickname" onSelect={onBlogger} />
                </Panel>
              )}
              {tab === 'campaigns' && (
                <Panel title="合作项目" subtitle="点击项目，筛选指标与笔记明细">
                  <PgyGroups rows={rows} field="cooperation_name" onSelect={onCampaign} />
                </Panel>
              )}
              {tab === 'audience' ? (
                <Panel title="受众画像">
                  <RecordTable rows={expandJson(rows, 'audience_json')} caption="受众画像" />
                </Panel>
              ) : tab === 'components' ? (
                <Panel title="营销组件">
                  <RecordTable rows={expandJson(rows, 'component_json')} caption="组件数据" />
                </Panel>
              ) : (
                <Panel title="合作明细" subtitle="当前全部分析条件下的记录；缺失金额显示为 —。">
                  <RecordTable
                    rows={rows}
                    defaultColumns={[
                      'note_title',
                      'publish_date',
                      'blogger_nickname',
                      'cooperation_name',
                      'spend',
                      'impressions',
                      'interactions',
                      'calculated_cpe',
                    ]}
                    caption="合作笔记"
                    onSelect={(r) => {
                      navigate({ view: 'overview', note: String(r.id) });
                      requestAnimationFrame(() =>
                        document
                          .querySelector('.pgy-decision-strip')
                          ?.scrollIntoView({ block: 'start', behavior: 'smooth' }),
                      );
                    }}
                  />
                </Panel>
              )}
            </>
          ) : (
            <EmptyState title="当前筛选未命中合作记录">
              请核对上方历史覆盖范围、账号与关键词。
              <button onClick={showAll}>清除日期与分析筛选，查看全部合作</button>
            </EmptyState>
          )
        }
      </QueryView>
      <details className="data-tools">
        <summary>导入合作数据与账号管理</summary>
        {admin && <AccountManager path="/media/xhs/accounts" admin xhs />}
        <FileImport
          path="/media/pgy/upload"
          fields={{ account_id: values.account_id }}
          disabled={!values.account_id}
          title="导入蒲公英合作文件"
        />
      </details>
    </div>
  );
}
function expandJson(rows: Row[], field: string): Row[] {
  return rows.flatMap<Row>((row) => {
    try {
      const data = typeof row[field] === 'string' ? JSON.parse(String(row[field])) : row[field];
      const list = Array.isArray(data)
        ? asRows(data)
        : Object.entries(asRecord(data)).map(([key, value]) => ({ 维度: key, 值: value }));
      return list.map((item) => ({
        note_title: row.note_title,
        blogger_nickname: row.blogger_nickname,
        ...item,
      }));
    } catch {
      return [{ note_title: row.note_title, 状态: '数据格式无效', 原始值: row[field] }];
    }
  });
}
