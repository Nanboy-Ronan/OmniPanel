import { useState } from 'react';
import { Drawer } from '../components/Drawer';
import { useQuery } from '@tanstack/react-query';
import {
  asRows,
  endpoint,
  paged,
  recordSchema,
  text,
  useFilters,
  useResource,
  type Row,
} from '../lib/resources';
import {
  Heading,
  Pagination,
  QueryView,
  RangeFilter,
  RecordTable,
  Stats,
  Detail,
  Tabs,
} from '../components/workspace';
import { Panel } from '../components/ui';
import { navigate } from '../lib/navigation';
export default function CustomersPage() {
  const { values, valid, signature, params } = useFilters({ commerce: true });
  const [min, setMin] = useState(1);
  const [selected, setSelected] = useState<Row | null>(null);
  const offset = Math.max(0, Number(params.get('offset')) || 0);
  const path = endpoint('/analysis/customers', {
    ...values,
    search: values.q,
    min_orders: min,
    limit: 25,
    offset,
  });
  const query = useQuery({
    queryKey: ['resource', path],
    queryFn: ({ signal }) => paged(path, signal),
    enabled: valid,
  });
  return (
    <>
      <Heading title="客户管理" description="按购买行为筛选客户，查看客户档案和历史订单。" />
      <RangeFilter key={signature} values={values} search>
        <label>
          最低订单数
          <input
            className="narrow-input"
            type="number"
            min={1}
            max={100000}
            value={min}
            onChange={(e) => {
              setMin(Math.max(1, Number(e.target.value)));
              navigate({ offset: null });
            }}
          />
        </label>
      </RangeFilter>
      <Panel title="客户列表">
        <QueryView query={query}>
          {(data) => (
            <>
              <RecordTable
                paginate={false}
                rows={data.rows}
                columns={[
                  'buyer_nick',
                  'receiver',
                  'mobile',
                  'province',
                  'orders',
                  'revenue',
                  'first_date',
                  'last_date',
                ]}
                caption="客户列表"
                onSelect={setSelected}
              />
              <Pagination
                page={Math.floor(offset / 25)}
                hasNext={data.total !== null ? offset + 25 < data.total : data.rows.length === 25}
                total={data.total}
                pageSize={25}
                onPage={(page) => {
                  navigate({ offset: String(page * 25) });
                  setSelected(null);
                }}
              />
            </>
          )}
        </QueryView>
      </Panel>
      {selected && (
        <CustomerDetail
          key={text(selected.customer_key)}
          row={selected}
          start={values.start_date}
          end={values.end_date}
          onClose={() => setSelected(null)}
        />
      )}
    </>
  );
}
function CustomerDetail({
  row,
  start,
  end,
  onClose,
}: {
  row: Row;
  start: string;
  end: string;
  onClose: () => void;
}) {
  const [all, setAll] = useState(false);
  const query = useResource(
    endpoint(
      `/analysis/customers/${encodeURIComponent(String(row.customer_key))}`,
      all ? {} : { start_date: start, end_date: end },
    ),
    recordSchema,
  );
  return (
    <Drawer
      title={`${text(row.buyer_nick || row.receiver || row.mobile || '客户')} · 客户档案`}
      subtitle={all ? '全部历史订单' : `${start} 至 ${end} 的订单`}
      onClose={onClose}
    >
      <Panel title="客户资料">
        <Detail row={row} />
      </Panel>
      <Panel
        title="购买记录"
        action={
          <label className="checkbox-field">
            <input type="checkbox" checked={all} onChange={(e) => setAll(e.target.checked)} />
            包含全部历史订单
          </label>
        }
      >
        <QueryView query={query}>
          {(data) => (
            <>
              <div className="drawer-stats">
                <Stats
                  items={[
                    { title: '订单数', value: data.count },
                    { title: '消费合计', value: data.total_spend },
                  ]}
                />
              </div>
              <RecordTable rows={asRows(data.orders)} caption="客户订单" />
            </>
          )}
        </QueryView>
      </Panel>
    </Drawer>
  );
}
export function IdentityPage() {
  const { values, valid, signature } = useFilters({ commerce: true });
  const [mode, setMode] = useState('exact');
  const query = useResource(
    endpoint('/analysis/identity/clusters', {
      start_date: values.start_date,
      end_date: values.end_date,
    }),
    recordSchema,
    valid,
  );
  return (
    <>
      <Heading
        title="跨平台客户"
        description="按手机号识别跨商城客户，精确匹配与模糊匹配分开展示。"
      />
      <RangeFilter key={signature} values={values} platform={false} />
      <Tabs
        value={mode}
        onChange={setMode}
        items={[
          { key: 'exact', label: '精确匹配' },
          { key: 'fuzzy', label: '模糊匹配（低置信度）' },
        ]}
      />
      <QueryView query={query}>
        {(data) => {
          const bucket = (data[mode] ?? {}) as Row;
          return (
            <Panel title={mode === 'exact' ? '精确匹配客户' : '模糊匹配客户'}>
              {mode === 'fuzzy' && (
                <p className="stale-notice">
                  {text(bucket.caveat || '脱敏手机号可能误判，不应与精确匹配相加。')}
                </p>
              )}
              <Stats
                items={[
                  { title: '分组数', value: bucket.cluster_count },
                  { title: '订单数', value: bucket.total_orders },
                  { title: '营业额', value: bucket.total_revenue },
                ]}
              />
              <RecordTable rows={asRows(bucket.clusters)} caption="跨平台客户分组" />
            </Panel>
          );
        }}
      </QueryView>
    </>
  );
}
