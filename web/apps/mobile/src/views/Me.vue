<script setup lang="ts">
import { reactive, ref } from 'vue';
import { useRouter } from 'vue-router';

import { showFailToast, showSuccessToast } from 'vant';

import { post, request } from '../api';
import { auth, clearAuth, isNative, saveAuth, serverUrl } from '../store';

const router = useRouter();
const form = reactive({ confirm: '', current: '', next: '' });
const saving = ref(false);
const ROLE: Record<string, string> = { admin: '管理员', reviewer: '审核员', viewer: '只读' };

async function changePassword() {
  if (form.next !== form.confirm) {
    showFailToast('两次输入的新密码不一致');
    return;
  }
  saving.value = true;
  try {
    const tokens = await post('/auth/password', { new_password: form.next, old_password: form.current });
    saveAuth(tokens);
    Object.assign(form, { confirm: '', current: '', next: '' });
    showSuccessToast('密码已修改');
    router.replace('/');
  } catch (error: any) {
    showFailToast(error.message);
  } finally {
    saving.value = false;
  }
}

async function logout() {
  try {
    await post('/auth/logout', { refresh_token: auth.refresh });
  } catch {
    // ends locally either way
  }
  clearAuth();
  router.replace('/login');
}

async function reloadMe() {
  auth.user = await request('/auth/me');
}
</script>

<template>
  <van-nav-bar title="我的" />
  <van-notice-bar v-if="auth.user?.must_change_password" wrapable text="当前是临时密码，请先修改密码。" />
  <van-cell-group inset style="margin-top: 12px">
    <van-cell title="用户" :value="`${auth.user?.display_name} (${auth.user?.username})`" @click="reloadMe" />
    <van-cell title="角色" :value="(auth.user?.roles ?? []).map((r) => ROLE[r] ?? r).join('、')" />
    <van-cell v-if="isNative" title="服务器" :value="serverUrl()" />
    <van-cell title="两步验证" :value="auth.user?.totp_enabled ? '已开启' : '未开启（在电脑端设置）'" />
  </van-cell-group>
  <van-cell-group inset title="修改密码">
    <van-field v-model="form.current" type="password" label="当前密码" autocomplete="current-password" />
    <van-field v-model="form.next" type="password" label="新密码" placeholder="至少 10 位，含字母和数字" autocomplete="new-password" />
    <van-field v-model="form.confirm" type="password" label="确认" autocomplete="new-password" />
  </van-cell-group>
  <div style="margin: 16px">
    <van-button block type="primary" :loading="saving" :disabled="!form.current || !form.next" @click="changePassword">修改密码</van-button>
    <van-button block style="margin-top: 12px" @click="logout">退出登录</van-button>
  </div>
</template>
