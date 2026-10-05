import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { z } from 'zod';
import { request } from '../lib/api';
import { asRecord, asRows, recordSchema, text, useResource } from '../lib/resources';
import { Heading, QueryView, RecordTable } from '../components/workspace';
import { ErrorState, Panel } from '../components/ui';
import examples from '../lib/sqlExamples.json';
import schema from '../lib/sqlSchema.json';
const resultSchema = z.object({
  columns: z.array(z.string()),
  rows: z.array(z.array(z.unknown())),
  row_count: z.number(),
  sql: z.string().optional(),
  explanation: z.string().optional(),
  error: z.string().nullable().optional(),
});
export default function SqlPage() {
  const [sql, setSql] = useState(examples[0][1]);
  const [question, setQuestion] = useState('');
  const [provider, setProvider] = useState('');
  const [model, setModel] = useState('');
  const providers = useResource('/analysis/nl-sql/providers', recordSchema);
  const list = asRows(providers.data?.providers);
  const chosen =
    list.find((item) => item.id === provider) ||
    list.find((item) => item.id === providers.data?.default_provider) ||
    list[0];
  const query = useMutation({
    mutationFn: ({ nl }: { nl: boolean }) =>
      request(nl ? '/analysis/nl-sql' : '/analysis/sql', resultSchema, {
        body: nl
          ? { question, provider: provider || undefined, model: model || undefined }
          : { sql },
        timeoutMs: nl ? 90000 : 30000,
      }),
    retry: false,
  });
  return (
    <>
      <Heading
        title="SQL 控制台"
        description="只读查询由服务端限制表范围、超时和返回行数；模型密钥仅保存在服务端。"
      />
      <Panel title="中文问数据">
        <form
          className="editor-form"
          onSubmit={(e) => {
            e.preventDefault();
            query.mutate({ nl: true });
          }}
        >
          <label>
            问题
            <input
              required
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              maxLength={2000}
              placeholder="上个月各平台营业额是多少？"
            />
          </label>
          <QueryView query={providers}>
            {() =>
              list.length ? (
                <div className="inline-form">
                  <label>
                    服务商
                    <select
                      value={String(chosen?.id ?? '')}
                      onChange={(e) => {
                        setProvider(e.target.value);
                        setModel('');
                      }}
                    >
                      {list.map((item) => (
                        <option key={text(item.id)} value={text(item.id)}>
                          {text(item.label || item.name || item.id)}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    模型
                    <select value={model} onChange={(e) => setModel(e.target.value)}>
                      <option value="">默认模型</option>
                      {(Array.isArray(chosen?.models) ? chosen.models : []).map((item) => {
                        const record = asRecord(item);
                        const value = typeof item === 'string' ? item : text(record.id);
                        return (
                          <option key={value} value={value}>
                            {typeof item === 'string' ? item : text(record.label || record.id)}
                          </option>
                        );
                      })}
                    </select>
                  </label>
                </div>
              ) : (
                <p>尚未配置模型服务。</p>
              )
            }
          </QueryView>
          <button disabled={query.isPending || !list.length || !question.trim()}>生成并查询</button>
        </form>
      </Panel>
      <Panel title="SQL 查询">
        <form
          className="editor-form"
          onSubmit={(e) => {
            e.preventDefault();
            query.mutate({ nl: false });
          }}
        >
          <label>
            示例
            <select onChange={(e) => setSql(examples[Number(e.target.value)][1])}>
              {examples.map(([name], index) => (
                <option key={name} value={index}>
                  {name}
                </option>
              ))}
            </select>
          </label>
          <label>
            SQL
            <textarea
              className="code-editor"
              value={sql}
              onChange={(e) => setSql(e.target.value)}
              rows={10}
              spellCheck={false}
              required
            />
          </label>
          <button className="primary" disabled={query.isPending || !sql.trim()}>
            {query.isPending ? '正在查询…' : '执行查询'}
          </button>
        </form>
      </Panel>
      {query.isError && <ErrorState error={query.error} />}{' '}
      {query.data && (
        <Panel title={`查询结果 · ${query.data.row_count} 行`}>
          {query.data.sql && (
            <>
              <pre className="sql-output">{query.data.sql}</pre>
              <button onClick={() => setSql(query.data!.sql!)}>复制到 SQL 编辑器</button>
            </>
          )}
          {query.data.explanation && <p className="panel-copy">{query.data.explanation}</p>}
          {query.data.error ? (
            <p className="stale-notice">{query.data.error}</p>
          ) : (
            <RecordTable
              rows={query.data.rows.map((row) =>
                Object.fromEntries(
                  query.data!.columns.map((column, index) => [column, row[index]]),
                ),
              )}
              columns={query.data.columns}
              caption="查询结果"
            />
          )}
        </Panel>
      )}
      <details>
        <summary>可用表与字段参考</summary>
        {Object.entries(schema).map(([table, fields]) => (
          <Panel key={table} title={table}>
            <RecordTable
              rows={fields.map(([field, type, description]) => ({ field, type, description }))}
              caption={`${table}字段`}
            />
          </Panel>
        ))}
      </details>
    </>
  );
}
