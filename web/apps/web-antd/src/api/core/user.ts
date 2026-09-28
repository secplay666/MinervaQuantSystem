import type { UserInfo } from '@vben/types';

import type { AuthApi } from './auth';

import { meApi } from './auth';

/** vben's UserInfo from /auth/me.  ``roles`` carries the permission codes,
 *  so route ``meta.authority`` lists permission codes (frontend access mode). */
export function toUserInfo(me: AuthApi.Me): UserInfo {
  return {
    avatar: '',
    desc: me.roles.join(', '),
    homePath: me.must_change_password ? '/profile' : '/home',
    realName: me.display_name,
    roles: me.permissions,
    token: '',
    userId: String(me.id),
    username: me.username,
  };
}

export async function getUserInfoApi(): Promise<UserInfo> {
  return toUserInfo(await meApi());
}
