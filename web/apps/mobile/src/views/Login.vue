<script setup lang="ts">
import { reactive, ref } from 'vue';
import { useRouter } from 'vue-router';

import { showFailToast } from 'vant';

import { post } from '../api';
import { isNative, saveAuth, serverUrl, setServerUrl } from '../store';

const router = useRouter();
const form = reactive({ password: '', server: serverUrl() || (isNative ? 'https://8.159.139.145:' : ''), totp: '', username: '' });
const loading = ref(false);

async function submit() {
  if (isNative && !/^https:\/\/[^/]+$/.test(form.server.trim().replace(/\/+$/, ''))) {
    showFailToast('服务器地址格式：https://IP:端口');
    return;
  }
  loading.value = true;
  try {
    if (isNative) setServerUrl(form.server);
    const tokens = await post('/auth/login', { password: form.password, totp: form.totp || undefined, username: form.username });
    saveAuth(tokens);
    router.replace(tokens.user.must_change_password ? '/me' : '/');
  } catch (error: any) {
    showFailToast(error.message);
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <div style="padding: 48px 20px 0">
    <h2 style="margin: 0 0 4px">Minerva 决策辅助</h2>
    <p class="muted" style="margin: 0 0 24px">请使用管理员分配的账号登录</p>
    <van-form @submit="submit">
      <van-cell-group inset>
        <van-field v-if="isNative" v-model="form.server" label="服务器" placeholder="https://IP:端口" autocomplete="url" />
        <van-field v-model="form.username" label="用户名" placeholder="用户名" autocomplete="username" :rules="[{ required: true, message: '请输入用户名' }]" />
        <van-field v-model="form.password" type="password" label="密码" placeholder="密码" autocomplete="current-password"
                   :rules="[{ required: true, message: '请输入密码' }]" />
        <van-field v-model="form.totp" type="digit" maxlength="6" label="验证码" placeholder="两步验证码（未开启可不填）" />
      </van-cell-group>
      <div style="margin: 24px 16px">
        <van-button round block type="primary" native-type="submit" :loading="loading">登录</van-button>
      </div>
    </van-form>
  </div>
</template>
