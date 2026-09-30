/**
 * HTTP client for the Minerva API (/api/v1).
 *
 * The API returns plain JSON bodies and errors as {"detail": {"code", "message"}}.
 * Access tokens last 15 minutes; on 401 the refresh token (kept in the access
 * store) is exchanged once for a new pair, otherwise the user logs in again.
 */
import type { RequestClientOptions } from '@vben/request';

import { useAppConfig } from '@vben/hooks';
import { preferences } from '@vben/preferences';
import {
  authenticateResponseInterceptor,
  defaultResponseInterceptor,
  errorMessageResponseInterceptor,
  RequestClient,
} from '@vben/request';
import { useAccessStore } from '@vben/stores';

import { message } from 'ant-design-vue';

import { useAuthStore } from '#/store';

import { refreshTokenApi } from './core';

const { apiURL } = useAppConfig(import.meta.env, import.meta.env.PROD);

export function apiErrorMessage(error: any, fallback = '请求失败'): string {
  const detail = error?.response?.data?.detail;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) return detail.map((d) => d.msg).join('；');
  return detail?.message ?? error?.message ?? fallback;
}

function createRequestClient(baseURL: string, options?: RequestClientOptions) {
  const client = new RequestClient({ ...options, baseURL, timeout: 30_000 });

  async function doReAuthenticate() {
    const accessStore = useAccessStore();
    const authStore = useAuthStore();
    accessStore.setAccessToken(null);
    if (preferences.app.loginExpiredMode === 'modal' && accessStore.isAccessChecked) {
      accessStore.setLoginExpired(true);
    } else {
      await authStore.logout();
    }
  }

  /**
   * Browser tabs share the stored tokens but each keeps its own copy in memory.
   * One tab refreshes at a time (Web Locks); a tab that waited, or whose copy is
   * older than the stored one, takes the tokens another tab has just stored
   * instead of presenting a rotated refresh token.
   */
  async function doRefreshToken() {
    const accessStore = useAccessStore();
    const refresh = async () => {
      const mine = accessStore.refreshToken;
      (accessStore as unknown as { $hydrate?: () => void }).$hydrate?.();
      if (accessStore.refreshToken && accessStore.refreshToken !== mine && accessStore.accessToken) {
        return accessStore.accessToken;
      }
      const current = accessStore.refreshToken;
      if (!current) throw new Error('no refresh token');
      const tokens = await refreshTokenApi(current);
      accessStore.setAccessToken(tokens.access_token);
      accessStore.setRefreshToken(tokens.refresh_token);
      return tokens.access_token;
    };
    return navigator.locks ? navigator.locks.request('minerva-token-refresh', refresh) : refresh();
  }

  function formatToken(token: null | string) {
    return token ? `Bearer ${token}` : null;
  }

  client.addRequestInterceptor({
    fulfilled: async (config) => {
      const accessStore = useAccessStore();
      config.headers.Authorization = formatToken(accessStore.accessToken);
      return config;
    },
  });

  // responseReturn 'body': 2xx responses resolve to the JSON body.
  client.addResponseInterceptor(
    defaultResponseInterceptor({ codeField: 'code', dataField: 'data', successCode: 0 }),
  );

  client.addResponseInterceptor(
    authenticateResponseInterceptor({
      client,
      doReAuthenticate,
      doRefreshToken,
      enableRefreshToken: preferences.app.enableRefreshToken,
      formatToken,
    }),
  );

  client.addResponseInterceptor(
    errorMessageResponseInterceptor((msg: string, error) => {
      if (error?.config?.silent) return;
      message.error(apiErrorMessage(error, msg));
    }),
  );

  return client;
}

export const requestClient = createRequestClient(apiURL, { responseReturn: 'body' });

/** Without interceptors: login and token refresh. */
export const baseRequestClient = new RequestClient({ baseURL: apiURL, responseReturn: 'body' });
baseRequestClient.addResponseInterceptor({ fulfilled: (response: any) => response.data });

export { apiURL };
