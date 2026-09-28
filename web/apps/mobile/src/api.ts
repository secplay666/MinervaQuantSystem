import { auth, clearAuth, saveAuth, serverUrl } from './store';

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string) {
    super(message);
  }
}

let refreshing: null | Promise<boolean> = null;

async function refresh(): Promise<boolean> {
  if (!auth.refresh) return false;
  refreshing ??= fetch(`${serverUrl()}/api/v1/auth/refresh`, {
    body: JSON.stringify({ refresh_token: auth.refresh }),
    headers: { 'Content-Type': 'application/json' },
    method: 'POST',
  })
    .then(async (res) => {
      if (!res.ok) return false;
      saveAuth(await res.json());
      return true;
    })
    .catch(() => false)
    .finally(() => {
      refreshing = null;
    });
  return refreshing;
}

export async function request<T = any>(path: string, init: RequestInit = {}, retry = true): Promise<T> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json', ...(init.headers as Record<string, string>) };
  if (auth.access) headers.Authorization = `Bearer ${auth.access}`;
  let res: Response;
  try {
    res = await fetch(`${serverUrl()}/api/v1${path}`, { ...init, headers });
  } catch (error: any) {
    throw new ApiError(0, 'network', `无法连接服务器：${error?.message ?? error}`);
  }
  if (res.status === 401 && retry && !path.startsWith('/auth/login')) {
    if (await refresh()) return request<T>(path, init, false);
    clearAuth();
    throw new ApiError(401, 'not_authenticated', '登录已过期，请重新登录');
  }
  if (!res.ok) {
    let detail: any = null;
    try {
      detail = (await res.json()).detail;
    } catch {
      // not JSON
    }
    const message = typeof detail === 'string' ? detail : detail?.message ?? `请求失败（${res.status}）`;
    throw new ApiError(res.status, detail?.code ?? 'error', message);
  }
  return (res.headers.get('content-type') ?? '').includes('json') ? res.json() : ((await res.text()) as T);
}

export const post = <T = any>(path: string, body?: unknown) =>
  request<T>(path, { body: body === undefined ? undefined : JSON.stringify(body), method: 'POST' });
