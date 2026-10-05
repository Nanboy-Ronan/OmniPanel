import { request, session, tokenSchema } from './api';

const RETURN_KEY = 'omnipanel.console.return';
const FILTER_KEYS = [
  'page',
  'anchor',
  'period',
  'start',
  'end',
  'platform',
  'account',
  'content',
  'q',
  'columns',
] as const;
export function rememberLoginDestination() {
  const current = new URLSearchParams(window.location.search);
  const saved = new URLSearchParams();
  for (const key of FILTER_KEYS) {
    const value = current.get(key);
    if (value) saved.set(key, value);
  }
  sessionStorage.setItem(RETURN_KEY, saved.toString());
}

// Capture and scrub the callback once, before rendering. A shared promise prevents
// React remounts from exchanging a single-use OAuth code twice.
export function consumeOAuthCallback(): Promise<string> | null {
  const url = new URL(window.location.href);
  const code = url.searchParams.get('code');
  const state = url.searchParams.get('state');
  if (!code && !state) return null;
  url.searchParams.delete('code');
  url.searchParams.delete('state');
  const saved = new URLSearchParams(sessionStorage.getItem(RETURN_KEY) ?? '');
  sessionStorage.removeItem(RETURN_KEY);
  for (const key of FILTER_KEYS) {
    const value = saved.get(key);
    if (value && !url.searchParams.has(key)) url.searchParams.set(key, value);
  }
  window.history.replaceState(null, '', url);
  session.clear();
  if (!code || !state) return Promise.reject(new Error('登录回调不完整，请重新发起企业微信登录。'));
  return request('/auth/wecom/exchange', tokenSchema, {
    body: { code, state },
    anonymous: true,
  }).then((result) => {
    session.set(result.access_token);
    return result.access_token;
  });
}
export function loginUrl(flow: 'qr' | 'mobile') {
  const callback = new URL(import.meta.env.BASE_URL, window.location.origin);
  return `/api/auth/wecom/start?${new URLSearchParams({ redirect_uri: callback.href, flow })}`;
}
