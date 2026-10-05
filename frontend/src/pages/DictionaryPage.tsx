import { useState } from 'react';
import definitions from '../lib/fieldDefinitions.json';
import { asRecord, recordSchema, useResource, type Row } from '../lib/resources';
import { Heading, QueryView, RecordTable, Tabs } from '../components/workspace';
import { Panel } from '../components/ui';
export default function DictionaryPage({ analyst }: { analyst: boolean }) {
  const [table, setTable] = useState('orders');
  const coverage = useResource('/analysis/field_coverage', recordSchema, analyst);
  const tables = definitions as Record<string, Record<string, Row>>;
  const rows = Object.entries(tables[table]).map(([field, def]) => ({
    field,
    ...def,
    coverage:
      def.nullable !== true
        ? 'N/A'
        : table === 'orders' && typeof asRecord(coverage.data?.columns)[field] === 'number'
          ? `${(Number(asRecord(coverage.data?.columns)[field]) * 100).toFixed(1)}%`
          : null,
  }));
  return (
    <>
      <Heading title="数据字典" description="字段定义、平台映射及非空覆盖率。" />
      <Tabs
        value={table}
        onChange={setTable}
        items={Object.keys(tables).map((key) => ({ key, label: key }))}
      />
      <Panel title="字段定义">
        <RecordTable key={table} rows={rows} caption="数据字典" />
      </Panel>
      {analyst && (
        <QueryView query={coverage}>
          {(data) => (
            <p className="footnote">
              覆盖率基于 {String(data.total_rows)} 条订单。以百分比显示，暂无记录时显示空值。
            </p>
          )}
        </QueryView>
      )}
    </>
  );
}
