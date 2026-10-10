import { beforeEach, describe, expect, it, vi } from 'vitest';
import { z } from 'zod';
import { ApiError, session, upload } from '../lib/api';
import { FakeXHR } from './fixtures';

const accepted = z.object({ batch_id: z.number() });
function form() {
  const body = new FormData();
  body.set('file', new File(['a,b'], 'orders.csv', { type: 'text/csv' }));
  return body;
}

describe('XHR upload helper', () => {
  beforeEach(() => {
    FakeXHR.reset();
    vi.stubGlobal('XMLHttpRequest', FakeXHR);
  });
  it('posts the form with the bearer token and reports progress', async () => {
    session.set('private-token');
    const onProgress = vi.fn();
    const body = form();
    const pending = upload('/upload/?expected_platform=jd', accepted, { body, onProgress });
    const xhr = FakeXHR.last!;
    expect(xhr.method).toBe('POST');
    expect(xhr.url).toBe('/api/upload/?expected_platform=jd');
    expect(xhr.body).toBe(body);
    expect(xhr.headers.Authorization).toBe('Bearer private-token');
    // The browser writes the multipart boundary; a manual Content-Type would break it.
    expect(xhr.headers['Content-Type']).toBeUndefined();
    xhr.progress(25, 100);
    xhr.progress(100, 100);
    expect(onProgress).toHaveBeenNthCalledWith(1, { loaded: 25, total: 100, percent: 25 });
    expect(onProgress).toHaveBeenLastCalledWith({ loaded: 100, total: 100, percent: 100 });
    xhr.respond({ batch_id: 9 }, 202);
    await expect(pending).resolves.toEqual({ batch_id: 9 });
  });
  it('expires the session on 401 and keeps the shared error shape', async () => {
    session.set('private-token');
    const listener = vi.fn();
    window.addEventListener('session-expired', listener);
    const pending = upload('/upload/', accepted, { body: form() });
    FakeXHR.last!.respond({ detail: 'sensitive', request_id: 'req-1' }, 401);
    await expect(pending).rejects.toMatchObject({
      message: '登录已过期，请重新登录。',
      status: 401,
      requestId: 'req-1',
    } satisfies Partial<ApiError>);
    expect(session.get()).toBeNull();
    expect(listener).toHaveBeenCalledOnce();
    window.removeEventListener('session-expired', listener);
  });
  it('validates the accepted payload', async () => {
    const pending = upload('/upload/', accepted, { body: form() });
    FakeXHR.last!.respond({ status: 'processing' }, 202);
    await expect(pending).rejects.toThrow('数据结构不完整');
  });
  it('rejects with AbortError when cancelled and aborts the transfer', async () => {
    const controller = new AbortController();
    const pending = upload('/upload/', accepted, { body: form(), signal: controller.signal });
    FakeXHR.last!.progress(10, 100);
    controller.abort();
    await expect(pending).rejects.toMatchObject({ name: 'AbortError' });
    expect(FakeXHR.last!.aborted).toBe(true);
  });
  it('times out only after the transfer stalls', async () => {
    vi.useFakeTimers();
    const pending = upload('/upload/', accepted, { body: form(), timeoutMs: 100 });
    const assertion = expect(pending).rejects.toThrow('请求超时');
    await vi.advanceTimersByTimeAsync(80);
    FakeXHR.last!.progress(50, 100);
    await vi.advanceTimersByTimeAsync(80);
    expect(FakeXHR.last!.aborted).toBe(false);
    await vi.advanceTimersByTimeAsync(30);
    await assertion;
    expect(FakeXHR.last!.aborted).toBe(true);
  });
  it('reports network failures without leaking details', async () => {
    const pending = upload('/upload/', accepted, { body: form() });
    FakeXHR.last!.fail();
    await expect(pending).rejects.toThrow('无法连接数据服务');
  });
});
