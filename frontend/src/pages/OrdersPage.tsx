import { useState } from 'react';
import { Drawer } from '../components/Drawer';
import { z } from 'zod';
import { useQuery } from '@tanstack/react-query';
import {
  asRecord,
  asRows,
  endpoint,
  paged,
  useResource,
  recordSchema,
  download,
  type Row,
} from '../lib/resources';
import { useLocationSearch, navigate } from '../lib/navigation';
import { platformNames, label } from '../lib/labels';
import { Detail, Heading, Pagination, QueryView, RecordTable } from '../components/workspace';
import { ErrorState, Panel } from '../components/ui';

const fields = [
  'order_id',
  'order_date',
  'sku',
  'quantity',
  'price',
  'receiver',
  'receiver_phone',
  'province',
  'area',
  'full_address',
  'buyer_nick',
  'coupon_name',
  'distributor',
  'raw_status',
  'refunded_amount',
  'customer_key',
];
type Filter = { field: string; value?: string; min?: string; max?: string };
export default function OrdersPage() {
  const search = useLocationSearch();
  const params = new URLSearchParams(search);
  const offset = Math.max(0, Number(params.get('offset')) || 0);
  const q = params.get('q') ?? '';
  const platform = params.get('platform') ?? '';
  const [filters, setFilters] = useState<Filter[]>(() => {
    try {
      const value = JSON.parse(params.get('columns') ?? '[]');
      const parsed = z
        .array(
          z.object({
            field: z.string(),
            value: z.string().optional(),
            min: z.string().optional(),
            max: z.string().optional(),
          }),
        )
        .max(12)
        .safeParse(value);
      return parsed.success ? parsed.data : [];
    } catch {
      return [];
    }
  });
  const [selected, setSelected] = useState<number | null>(null);
  const [exporting, setExporting] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const path = endpoint('/orders_all/', {
    limit: 25,
    offset,
    search: q,
    platform,
    column_filters: params.get('columns'),
  });
  const query = useQuery({
    queryKey: ['resource', path],
    queryFn: ({ signal }) => paged(path, signal),
  });
  return (
    <>
      <Heading
        title="订单明细"
        description="服务端搜索、字段筛选和分页，查看原始平台记录。"
        action={
          <button
            disabled={exporting}
            onClick={async () => {
              setExporting(true);
              setError(null);
              try {
                await download(
                  endpoint('/orders_all/export', {
                    search: q,
                    platform,
                    column_filters: params.get('columns'),
                  }),
                  '订单明细.csv',
                );
              } catch (e) {
                setError(e as Error);
              } finally {
                setExporting(false);
              }
            }}
          >
            {exporting ? '正在导出…' : '导出全部筛选结果'}
          </button>
        }
      />
      <form
        className="filter-bar range-filter"
        onSubmit={(e) => {
          e.preventDefault();
          const data = new FormData(e.currentTarget);
          navigate({
            q: String(data.get('q')),
            platform: String(data.get('platform')),
            columns: filters.length ? JSON.stringify(filters) : null,
            offset: null,
          });
          setSelected(null);
        }}
      >
        <label>
          关键词
          <input name="q" defaultValue={q} maxLength={200} />
        </label>
        <label>
          平台
          <select name="platform" defaultValue={platform}>
            <option value="">全部</option>
            {['youzan', 'jd', 'tmall'].map((p) => (
              <option key={p} value={p}>
                {platformNames[p]}
              </option>
            ))}
          </select>
        </label>
        <button type="submit">应用筛选</button>
        <details className="wide">
          <summary>字段筛选（最多 12 项）</summary>
          {filters.map((filter, index) => (
            <div className="filter-row" key={index}>
              <select
                aria-label={`筛选字段 ${index + 1}`}
                value={filter.field}
                onChange={(e) =>
                  setFilters(filters.map((f, i) => (i === index ? { field: e.target.value } : f)))
                }
              >
                {fields.map((field) => (
                  <option key={field} value={field}>
                    {label(field)}
                  </option>
                ))}
              </select>
              {['price', 'quantity', 'order_date'].includes(filter.field) ? (
                <>
                  <input
                    aria-label="最小值"
                    type={filter.field === 'order_date' ? 'date' : 'number'}
                    step="any"
                    value={filter.min ?? ''}
                    onChange={(e) =>
                      setFilters(
                        filters.map((f, i) =>
                          i === index ? { ...f, min: e.target.value || undefined } : f,
                        ),
                      )
                    }
                  />
                  <input
                    aria-label="最大值"
                    type={filter.field === 'order_date' ? 'date' : 'number'}
                    step="any"
                    value={filter.max ?? ''}
                    onChange={(e) =>
                      setFilters(
                        filters.map((f, i) =>
                          i === index ? { ...f, max: e.target.value || undefined } : f,
                        ),
                      )
                    }
                  />
                </>
              ) : (
                <input
                  aria-label="包含文字"
                  value={filter.value ?? ''}
                  maxLength={200}
                  onChange={(e) =>
                    setFilters(
                      filters.map((f, i) => (i === index ? { ...f, value: e.target.value } : f)),
                    )
                  }
                />
              )}
              <button
                type="button"
                onClick={() => setFilters(filters.filter((_, i) => i !== index))}
              >
                移除
              </button>
            </div>
          ))}
          <button
            type="button"
            disabled={filters.length >= 12}
            onClick={() => setFilters([...filters, { field: 'sku', value: '' }])}
          >
            添加条件
          </button>
        </details>
      </form>
      {error && <ErrorState error={error} />}
      <Panel title="订单列表">
        <QueryView query={query}>
          {(data) => (
            <>
              <RecordTable
                paginate={false}
                rows={data.rows}
                caption="订单列表"
                defaultColumns={[
                  'order_id',
                  'order_date',
                  'platform',
                  'sku',
                  'quantity',
                  'price',
                  'receiver',
                  'province',
                ]}
                exportable={false}
                onSelect={(row: Row) => setSelected(Number(row.id))}
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
      {selected !== null && <OrderDetail id={selected} onClose={() => setSelected(null)} />}
    </>
  );
}
function OrderDetail({ id, onClose }: { id: number; onClose: () => void }) {
  const query = useResource(`/orders_all/${id}/raw`, recordSchema);
  return (
    <Drawer title={`订单 #${id}`} subtitle="统一订单字段与平台导出的原始记录" onClose={onClose}>
      <QueryView query={query}>
        {(row) => (
          <>
            <Panel title="统一订单">
              <Detail row={asRecord(row.order)} />
            </Panel>
            <Panel title="平台原始字段">
              <RecordTable rows={asRows(row.rows)} caption="平台原始字段" />
            </Panel>
          </>
        )}
      </QueryView>
    </Drawer>
  );
}
