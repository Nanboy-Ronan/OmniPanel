import { describe, expect, it, vi } from 'vitest';
import { z } from 'zod';
import { ApiError, request, session } from '../lib/api';
import { consumeOAuthCallback, loginUrl, rememberLoginDestination } from '../lib/auth';
import { response } from './fixtures';

describe('HTTP and identity boundary', () => {
  it('sends a bearer token only in headers and includes same-origin cookies', async () => {
    session.set('private-token');
    const fetch = vi.fn().mockResolvedValue(response({ ok: true }));
    vi.stubGlobal('fetch', fetch);
    await request('/check', z.object({ ok: z.boolean() }));
    expect(fetch).toHaveBeenCalledWith(
      '/api/check',
      expect.objectContaining({
        credentials: 'same-origin',
        headers: expect.objectContaining({ Authorization: 'Bearer private-token' }),
      }),
    );
  });
  it('expires the session on 401 without leaking server error content', async () => {
    session.set('private-token');
    const listener = vi.fn();
    window.addEventListener('session-expired', listener);
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(response({ detail: 'sensitive-db-string' }, 401)),
    );
    await expect(request('/check', z.unknown())).rejects.toThrow('登录已过期');
    expect(session.get()).toBeNull();
    expect(listener).toHaveBeenCalledOnce();
    window.removeEventListener('session-expired', listener);
  });
  it('rejects incomplete payloads instead of substituting zero metrics', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response({})));
    await expect(request('/check', z.object({ orders: z.number() }))).rejects.toThrow(
      '数据结构不完整',
    );
  });
  it('distinguishes a timeout from cancellation', async () => {
    vi.useFakeTimers();
    vi.stubGlobal(
      'fetch',
      vi.fn(
        (_url, options: RequestInit) =>
          new Promise((_resolve, reject) => {
            options.signal?.addEventListener('abort', () =>
              reject(new DOMException('Aborted', 'AbortError')),
            );
          }),
      ),
    );
    const timed = request('/check', z.unknown(), { timeoutMs: 20 });
    const assertion = expect(timed).rejects.toThrow('请求超时');
    await vi.advanceTimersByTimeAsync(21);
    await assertion;
    const controller = new AbortController();
    const cancelled = request('/check', z.unknown(), { signal: controller.signal });
    controller.abort();
    await expect(cancelled).rejects.toMatchObject({ name: 'AbortError' });
  });
  it('retains a safe server request ID', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(response({ request_id: 'abc123', detail: 'private' }, 500)),
    );
    await expect(request('/check', z.unknown())).rejects.toMatchObject({
      status: 500,
      requestId: 'abc123',
    } satisfies Partial<ApiError>);
  });
  it('scrubs OAuth code and state before exchange and does not repeat the exchange', async () => {
    window.history.replaceState(null, '', '/console/?code=one-use&state=signed&anchor=2026-10-03');
    const fetch = vi.fn().mockResolvedValue(response({ access_token: 'jwt' }));
    vi.stubGlobal('fetch', fetch);
    const login = consumeOAuthCallback();
    expect(window.location.search).toBe('?anchor=2026-10-03');
    expect(consumeOAuthCallback()).toBeNull();
    await login;
    expect(fetch).toHaveBeenCalledOnce();
    expect(session.get()).toBe('jwt');
    expect(fetch).toHaveBeenCalledWith(
      '/api/auth/wecom/exchange',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ code: 'one-use', state: 'signed' }),
      }),
    );
  });
  it('restores only shareable filters after login', async () => {
    window.history.replaceState(
      null,
      '',
      '/console/?anchor=2026-10-01&period=week&redirect=https://evil.test',
    );
    rememberLoginDestination();
    window.history.replaceState(null, '', '/console/?code=once&state=signed');
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response({ access_token: 'jwt' })));
    await consumeOAuthCallback();
    expect(window.location.pathname).toBe('/console/');
    expect(window.location.search).toBe('?anchor=2026-10-01&period=week');
  });
  it('uses the fixed console callback rather than user query parameters', () => {
    window.history.replaceState(null, '', '/console/?redirect_uri=https://evil.test');
    const url = new URL(loginUrl('qr'), window.location.origin);
    expect(url.searchParams.get('redirect_uri')).toBe(`${window.location.origin}/console/`);
  });
});
