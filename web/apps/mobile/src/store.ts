import { reactive } from 'vue';

import { Capacitor } from '@capacitor/core';

export interface Me {
  display_name: string;
  id: number;
  must_change_password: boolean;
  permissions: string[];
  roles: string[];
  totp_enabled: boolean;
  username: string;
}

const KEY = 'minerva.mobile.auth';
const SERVER = 'minerva.mobile.server';

export const isNative = Capacitor.isNativePlatform();

export const auth = reactive<{ access: string; refresh: string; user: Me | null }>(
  JSON.parse(localStorage.getItem(KEY) ?? 'null') ?? { access: '', refresh: '', user: null },
);

export function saveAuth(tokens: { access_token: string; refresh_token: string; user?: Me }) {
  auth.access = tokens.access_token;
  auth.refresh = tokens.refresh_token;
  if (tokens.user) auth.user = tokens.user;
  localStorage.setItem(KEY, JSON.stringify(auth));
}

export function clearAuth() {
  Object.assign(auth, { access: '', refresh: '', user: null });
  localStorage.removeItem(KEY);
}

/** API origin.  The Android app talks to the configured server; the mobile
 *  web build (served by quant-app) uses its own origin. */
export function serverUrl(): string {
  return localStorage.getItem(SERVER) ?? '';
}

export function setServerUrl(url: string) {
  localStorage.setItem(SERVER, url.trim().replace(/\/+$/, ''));
}

export function can(permission: string): boolean {
  return auth.user?.permissions.includes(permission) ?? false;
}
