import { z } from 'zod';

export class ApiError extends Error {
  constructor(
    message: string,
    public status = 0,
    public requestId?: string,
  ) {
    super(message);
  }
}

const TOKEN_KEY = 'omnipanel.console.token';
export const session = {
  get: () => sessionStorage.getItem(TOKEN_KEY),
  set: (token: string) => sessionStorage.setItem(TOKEN_KEY, token),
  clear: () => sessionStorage.removeItem(TOKEN_KEY),
};

const statusMessages: Record<number, string> = {
  400: '请求未能完成，请检查输入或重新登录。',
  401: '登录已过期，请重新登录。',
  403: '当前账号没有查看此数据的权限。',
  404: '请求的数据不存在。',
  413: '文件超过上传大小限制。',
  415: '不支持此文件格式。',
  422: '提交内容无效，请检查必填字段、日期与文件格式。',
  429: '请求过于频繁，请稍后再试。',
  503: '数据服务暂时不可用，请稍后重试。',
};
/** Map a failed response to a user-facing error; server detail is never shown, only its request ID. */
function failure(status: number, payload: unknown) {
  const id = z.object({ request_id: z.string() }).safeParse(payload);
  return new ApiError(
    statusMessages[status] ?? '数据服务发生异常，请稍后重试。',
    status,
    id.success ? id.data.request_id : undefined,
  );
}
function expireSession() {
  session.clear();
  window.dispatchEvent(new Event('session-expired'));
}
function validate<T>(schema: z.ZodType<T>, payload: unknown): T {
  const parsed = schema.safeParse(payload);
  if (!parsed.success) throw new ApiError('数据结构不完整，无法可靠展示，请联系管理员。');
  return parsed.data;
}

// Same-origin proxy keeps OAuth cookies and API traffic on one origin.
export async function request<T>(
  path: string,
  schema: z.ZodType<T>,
  options: {
    signal?: AbortSignal;
    body?: unknown;
    anonymous?: boolean;
    timeoutMs?: number;
    method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
    onHeaders?: (headers: Headers) => void;
    format?: 'json' | 'blob';
  } = {},
): Promise<T> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  options.signal?.addEventListener('abort', abort, { once: true });
  if (options.signal?.aborted) abort();
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, options.timeoutMs ?? 20000);
  try {
    const token = options.anonymous ? null : session.get();
    const response = await fetch(`/api${path}`, {
      method: options.method ?? (options.body === undefined ? 'GET' : 'POST'),
      credentials: 'same-origin',
      cache: 'no-store',
      signal: controller.signal,
      headers: {
        Accept: 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(options.body === undefined || options.body instanceof FormData
          ? {}
          : { 'Content-Type': 'application/json' }),
      },
      body:
        options.body instanceof FormData
          ? options.body
          : options.body === undefined
            ? undefined
            : JSON.stringify(options.body),
    });
    if (response.status === 401 && !options.anonymous) expireSession();
    if (!response.ok) throw failure(response.status, await response.json().catch(() => null));
    options.onHeaders?.(response.headers);
    const payload: unknown =
      response.status === 204
        ? null
        : options.format === 'blob'
          ? await response.blob()
          : await response.json().catch(() => {
              throw new ApiError('服务返回了无效的数据格式。');
            });
    return validate(schema, payload);
  } catch (error) {
    if (timedOut) throw new ApiError('请求超时，请检查连接后重试。');
    if (options.signal?.aborted) throw error;
    if (error instanceof ApiError) throw error;
    throw new ApiError('无法连接数据服务，请检查网络后重试。');
  } finally {
    clearTimeout(timer);
    options.signal?.removeEventListener('abort', abort);
  }
}

export type UploadProgress = { loaded: number; total: number | null; percent: number | null };
/**
 * POST multipart data with upload progress, which fetch cannot report. Auth, error shape, 401
 * handling and schema validation match `request`. The timeout restarts whenever bytes move, so a
 * large file on a slow link is not cut off while it is still uploading.
 */
export function upload<T>(
  path: string,
  schema: z.ZodType<T>,
  options: {
    body: FormData;
    signal?: AbortSignal;
    timeoutMs?: number;
    onProgress?: (progress: UploadProgress) => void;
  },
): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const aborted = () => new DOMException('Aborted', 'AbortError');
    if (options.signal?.aborted) {
      reject(aborted());
      return;
    }
    const xhr = new XMLHttpRequest();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let settled = false;
    const finish = (fn: () => void) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      options.signal?.removeEventListener('abort', cancel);
      fn();
    };
    const arm = () => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        finish(() => reject(new ApiError('请求超时，请检查连接后重试。')));
        xhr.abort();
      }, options.timeoutMs ?? 120000);
    };
    const cancel = () => {
      finish(() => reject(aborted()));
      xhr.abort();
    };
    options.signal?.addEventListener('abort', cancel, { once: true });
    xhr.open('POST', `/api${path}`);
    xhr.setRequestHeader('Accept', 'application/json');
    const token = session.get();
    if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`);
    xhr.upload.addEventListener('progress', (event) => {
      arm();
      const total = event.lengthComputable && event.total > 0 ? event.total : null;
      options.onProgress?.({
        loaded: event.loaded,
        total,
        percent: total ? Math.min(100, Math.round((event.loaded / total) * 100)) : null,
      });
    });
    xhr.addEventListener('load', () => {
      let payload: unknown = null;
      let parsedJson = true;
      try {
        payload = xhr.status === 204 || !xhr.responseText ? null : JSON.parse(xhr.responseText);
      } catch {
        parsedJson = false;
      }
      if (xhr.status === 401) expireSession();
      finish(() => {
        try {
          if (xhr.status < 200 || xhr.status >= 300) throw failure(xhr.status, payload);
          if (!parsedJson) throw new ApiError('服务返回了无效的数据格式。');
          resolve(validate(schema, payload));
        } catch (error) {
          reject(error);
        }
      });
    });
    xhr.addEventListener('error', () =>
      finish(() => reject(new ApiError('无法连接数据服务，请检查网络后重试。'))),
    );
    xhr.addEventListener('abort', () => finish(() => reject(aborted())));
    arm();
    xhr.send(options.body);
  });
}

export const userSchema = z.object({
  id: z.string(),
  email: z.string(),
  role: z.enum(['admin', 'analyst', 'viewer']),
  display_name: z.string().nullable().optional(),
});
export type User = z.infer<typeof userSchema>;
export const tokenSchema = z.object({ access_token: z.string().min(1) });
export const loginStatusSchema = z.object({ enabled: z.boolean() });
