import type { Kpi } from '../lib/data';
export const metrics = { orders: 120, revenue: 30960, aov: 258, unique_customers: 92 };
export const kpi: Kpi = {
  day: metrics,
  prior_day: { ...metrics, orders: 100, revenue: 25000 },
  week: { ...metrics, orders: 684, revenue: 171400 },
  prior_week: { ...metrics, orders: 725, revenue: 186600 },
  month: { ...metrics, orders: 342, revenue: 86125 },
  prior_month: { ...metrics, orders: 301, revenue: 74750 },
};
export const user = {
  id: 'a1',
  email: 'analyst@example.test',
  role: 'analyst',
  display_name: '测试分析师',
};
export const batch = {
  id: 17,
  filename: '订单.csv',
  platform: 'jd',
  uploaded_at: '2026-10-03 15:00:00',
  row_count: 100,
  inserted_orders: 80,
  duplicate_rows: 20,
  invalid_rows: 0,
  status: 'completed',
  error_message: null,
};
/** Minimal XMLHttpRequest double: records the request and lets a test drive progress and the reply. */
export class FakeXHR {
  static last: FakeXHR | null = null;
  static respondWith: ((xhr: FakeXHR) => void) | null = null;
  method = '';
  url = '';
  headers: Record<string, string> = {};
  body: unknown = null;
  status = 0;
  responseText = '';
  aborted = false;
  private listeners: Record<string, (() => void)[]> = {};
  private uploadListeners: ((event: ProgressEvent) => void)[] = [];
  upload = {
    addEventListener: (_type: string, listener: (event: ProgressEvent) => void) => {
      this.uploadListeners.push(listener);
    },
  };
  constructor() {
    FakeXHR.last = this;
  }
  static reset() {
    FakeXHR.last = null;
    FakeXHR.respondWith = null;
  }
  open(method: string, url: string) {
    this.method = method;
    this.url = url;
  }
  setRequestHeader(name: string, value: string) {
    this.headers[name] = value;
  }
  addEventListener(type: string, listener: () => void) {
    (this.listeners[type] ??= []).push(listener);
  }
  send(body: unknown) {
    this.body = body;
    const reply = FakeXHR.respondWith;
    if (reply) queueMicrotask(() => reply(this));
  }
  abort() {
    this.aborted = true;
    this.emit('abort');
  }
  progress(loaded: number, total: number) {
    const event = { lengthComputable: true, loaded, total } as ProgressEvent;
    for (const listener of this.uploadListeners) listener(event);
  }
  respond(payload: unknown, status = 200) {
    this.status = status;
    this.responseText = JSON.stringify(payload);
    this.emit('load');
  }
  fail() {
    this.emit('error');
  }
  private emit(type: string) {
    for (const listener of this.listeners[type] ?? []) listener();
  }
}
export function response(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}
