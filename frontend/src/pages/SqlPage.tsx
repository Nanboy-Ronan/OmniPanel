import { useState, type KeyboardEvent } from 'react';
import { Download, History, Trash2 } from 'lucide-react';
import { useMutation } from '@tanstack/react-query';
import { z } from 'zod';
import { request } from '../lib/api';
import { asRecord, asRows, exportCsv, recordSchema, text, useResource } from '../lib/resources';
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
const HISTORY_KEY = 'omnipanel.console.sql.history';
const HISTORY_LIMIT = 20;
type HistoryEntry = { sql: string; at: string };
const historySchema = z.array(z.object({ sql: z.string(), at: z.string() }));
function readHistory(): HistoryEntry[] {
  try {
    return (
      historySchema.safeParse(JSON.parse(localStorage.getItem(HISTORY_KEY) ?? '[]')).data ?? []
    );
  } catch {
    return [];
  }
}
function writeHistory(entries: HistoryEntry[]) {
  try {
    if (entries.length) localStorage.setItem(HISTORY_KEY, JSON.stringify(entries));
    else localStorage.removeItem(HISTORY_KEY);
  } catch {
    // History is a convenience; queries still run without storage.
  }
}
const stamp = (date: Date) =>
  date.toLocaleString('zh-CN', {
    hour12: false,
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
const fileStamp = (date: Date) =>
  [date.getFullYear(), date.getMonth() + 1, date.getDate(), date.getHours(), date.getMinutes()]
    .map((part) => String(part).padStart(2, '0'))
    .join('');
const isMac = /Mac|iPhone|iPad/.test(navigator.userAgent);
export default function SqlPage() {
  const [sql, setSql] = useState(examples[0][1]);
  const [history, setHistory] = useState(readHistory);
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
    mutationFn: async ({ nl }: { nl: boolean }) => {
      const started = performance.now();
      const result = await request(nl ? '/analysis/nl-sql' : '/analysis/sql', resultSchema, {
        body: nl
          ? { question, provider: provider || undefined, model: model || undefined }
          : { sql },
        timeoutMs: nl ? 90000 : 30000,
      });
      return { ...result, elapsed: performance.now() - started, finishedAt: new Date() };
    },
    retry: false,
  });
  const remember = (text: string) => {
    const entry = { sql: text.trim(), at: new Date().toISOString() };
    const next = [entry, ...history.filter((item) => item.sql !== entry.sql)].slice(
      0,
      HISTORY_LIMIT,
    );
    setHistory(next);
    writeHistory(next);
  };
  const runSql = () => {
    if (query.isPending || !sql.trim()) return;
    remember(sql);
    query.mutate({ nl: false });
  };
  const runShortcut = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      runSql();
    }
  };
  const resultRows = query.data
    ? query.data.rows.map((row) =>
        Object.fromEntries(query.data!.columns.map((column, index) => [column, row[index]])),
      )
    : [];
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
            runSql();
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
              onKeyDown={runShortcut}
              aria-keyshortcuts="Control+Enter Meta+Enter"
              rows={10}
              spellCheck={false}
              required
            />
          </label>
          <div className="sql-actions">
            <button className="primary" disabled={query.isPending || !sql.trim()}>
              {query.isPending ? '正在查询…' : '执行查询'}
            </button>
            <small>
              <kbd>{isMac ? '⌘' : 'Ctrl'}</kbd> + <kbd>Enter</kbd> 执行
            </small>
          </div>
        </form>
        {history.length > 0 && (
          <details className="sql-history">
            <summary>
              <History size={14} aria-hidden />
              最近查询 {history.length}
            </summary>
            <ol>
              {history.map((entry) => (
                <li key={entry.at + entry.sql}>
                  <button
                    type="button"
                    className="sql-history-item"
                    title={entry.sql}
                    onClick={() => setSql(entry.sql)}
                  >
                    <code>{entry.sql.replace(/\s+/g, ' ')}</code>
                    <small>{stamp(new Date(entry.at))}</small>
                  </button>
                </li>
              ))}
            </ol>
            <button
              type="button"
              className="text-button"
              onClick={() => {
                setHistory([]);
                writeHistory([]);
              }}
            >
              <Trash2 size={13} aria-hidden />
              清空历史
            </button>
          </details>
        )}
      </Panel>
      {query.isError && <ErrorState error={query.error} />}{' '}
      {query.data && (
        <Panel
          title="查询结果"
          subtitle={`${query.data.row_count.toLocaleString()} 行 · 用时 ${(query.data.elapsed / 1000).toFixed(2)} 秒`}
          action={
            !query.data.error && query.data.columns.length > 0 ? (
              <button
                onClick={() =>
                  exportCsv(
                    resultRows,
                    query.data!.columns,
                    `查询结果-${fileStamp(query.data!.finishedAt)}.csv`,
                    'SQL 查询结果',
                  )
                }
              >
                <Download size={14} aria-hidden />
                导出 CSV
              </button>
            ) : undefined
          }
        >
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
              rows={resultRows}
              columns={query.data.columns}
              caption="查询结果"
              exportable={false}
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
