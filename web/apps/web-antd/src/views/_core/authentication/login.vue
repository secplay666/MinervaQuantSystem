<script lang="ts" setup>
import type { VbenFormSchema } from '@vben/common-ui';

import { computed } from 'vue';

import { AuthenticationLogin, z } from '@vben/common-ui';

import { useAuthStore } from '#/store';

defineOptions({ name: 'Login' });

const authStore = useAuthStore();

// Accounts are created by an administrator: no registration, password
// recovery, QR or SMS login.  The TOTP field is only needed when enabled.
const formSchema = computed((): VbenFormSchema[] => [
  {
    component: 'VbenInput',
    componentProps: { autocomplete: 'username', placeholder: '用户名' },
    fieldName: 'username',
    label: '用户名',
    rules: z.string().min(1, { message: '请输入用户名' }),
  },
  {
    component: 'VbenInputPassword',
    componentProps: { autocomplete: 'current-password', placeholder: '密码' },
    fieldName: 'password',
    label: '密码',
    rules: z.string().min(1, { message: '请输入密码' }),
  },
  {
    component: 'VbenInput',
    componentProps: { autocomplete: 'one-time-code', maxlength: 6, placeholder: '两步验证码（未开启可不填）' },
    fieldName: 'totp',
    label: '两步验证码',
    rules: z.string().optional(),
  },
]);
</script>

<template>
  <AuthenticationLogin
    :form-schema="formSchema"
    :loading="authStore.loginLoading"
    :show-code-login="false"
    :show-forget-password="false"
    :show-qrcode-login="false"
    :show-register="false"
    :show-remember-me="false"
    :show-third-party-login="false"
    sub-title="请使用管理员分配的账号登录"
    title="Minerva 决策辅助"
    @submit="authStore.authLogin"
  />
</template>
