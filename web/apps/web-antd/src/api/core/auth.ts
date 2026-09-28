import { baseRequestClient, requestClient } from '#/api/request';

export namespace AuthApi {
  export interface LoginParams {
    password: string;
    totp?: string;
    username: string;
  }

  export interface Me {
    display_name: string;
    id: number;
    is_active: boolean;
    last_login_at: null | string;
    must_change_password: boolean;
    permissions: string[];
    roles: string[];
    totp_enabled: boolean;
    username: string;
  }

  export interface Tokens {
    access_token: string;
    expires_in: number;
    refresh_token: string;
    token_type: string;
    user: Me;
  }
}

export async function loginApi(data: AuthApi.LoginParams) {
  return baseRequestClient.post<AuthApi.Tokens>('/auth/login', data);
}

export async function refreshTokenApi(refreshToken: string) {
  return baseRequestClient.post<AuthApi.Tokens>('/auth/refresh', { refresh_token: refreshToken });
}

export async function logoutApi(refreshToken: null | string) {
  if (!refreshToken) return;
  return baseRequestClient.post('/auth/logout', { refresh_token: refreshToken });
}

export async function meApi() {
  return requestClient.get<AuthApi.Me>('/auth/me');
}

export async function changePasswordApi(oldPassword: string, newPassword: string) {
  return requestClient.post<AuthApi.Tokens>('/auth/password', {
    new_password: newPassword,
    old_password: oldPassword,
  });
}

export async function totpSetupApi() {
  return requestClient.post<{ secret: string; uri: string }>('/auth/totp/setup');
}

export async function totpEnableApi(secret: string, code: string) {
  return requestClient.post('/auth/totp/enable', { code, secret });
}

export async function totpDisableApi(password: string) {
  return requestClient.post('/auth/totp/disable', { password });
}
