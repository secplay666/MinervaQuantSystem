import type { Recordable, UserInfo } from '@vben/types';

import type { AuthApi } from '#/api';

import { ref } from 'vue';
import { useRouter } from 'vue-router';

import { LOGIN_PATH } from '@vben/constants';
import { preferences } from '@vben/preferences';
import { resetAllStores, useAccessStore, useUserStore } from '@vben/stores';

import { message, notification } from 'ant-design-vue';
import { defineStore } from 'pinia';

import { getUserInfoApi, loginApi, logoutApi, toUserInfo } from '#/api';
import { apiErrorMessage } from '#/api/request';

export const useAuthStore = defineStore('auth', () => {
  const accessStore = useAccessStore();
  const userStore = useUserStore();
  const router = useRouter();

  const loginLoading = ref(false);

  /** Store a token pair and the user; access codes are the permission codes. */
  function applyTokens(tokens: AuthApi.Tokens): UserInfo {
    accessStore.setAccessToken(tokens.access_token);
    accessStore.setRefreshToken(tokens.refresh_token);
    const userInfo = toUserInfo(tokens.user);
    userStore.setUserInfo(userInfo);
    accessStore.setAccessCodes(tokens.user.permissions);
    return userInfo;
  }

  async function authLogin(params: Recordable<any>, onSuccess?: () => Promise<void> | void) {
    let userInfo: null | UserInfo = null;
    try {
      loginLoading.value = true;
      const tokens = await loginApi({
        password: params.password,
        totp: params.totp || undefined,
        username: params.username,
      });
      userInfo = applyTokens(tokens);
      if (accessStore.loginExpired) {
        accessStore.setLoginExpired(false);
      } else if (tokens.user.must_change_password) {
        message.warning('首次登录或密码已重置，请先修改密码');
        await router.push({ path: '/profile', query: { tab: 'password' } });
      } else {
        onSuccess ? await onSuccess() : await router.push(userInfo.homePath || preferences.app.defaultHomePath);
      }
      notification.success({ description: `欢迎，${userInfo.realName}`, duration: 3, message: '登录成功' });
    } catch (error: any) {
      message.error(apiErrorMessage(error, '登录失败'));
    } finally {
      loginLoading.value = false;
    }
    return { userInfo };
  }

  async function logout(redirect: boolean = true) {
    try {
      await logoutApi(accessStore.refreshToken);
    } catch {
      // the session ends locally either way
    }
    resetAllStores();
    accessStore.setLoginExpired(false);
    const currentRoute = router.currentRoute.value;
    const alreadyOnLogin = currentRoute.path === LOGIN_PATH;
    await router.replace({
      path: LOGIN_PATH,
      query: redirect && !alreadyOnLogin ? { redirect: encodeURIComponent(currentRoute.fullPath) } : {},
    });
  }

  async function fetchUserInfo() {
    const userInfo = await getUserInfoApi();
    userStore.setUserInfo(userInfo);
    accessStore.setAccessCodes(userInfo.roles ?? []);
    return userInfo;
  }

  function $reset() {
    loginLoading.value = false;
  }

  return { $reset, applyTokens, authLogin, fetchUserInfo, loginLoading, logout };
});
