<script lang="ts" setup>
import type { VbenFormSchema } from '@vben/common-ui';

import { computed, ref } from 'vue';
import { useRoute } from 'vue-router';

import { AuthenticationRegister, z } from '@vben/common-ui';

import { message } from 'ant-design-vue';

import { registerApi } from '#/api';
import { apiErrorMessage } from '#/api/request';
import { useAuthStore } from '#/store';

defineOptions({ name: 'Register' });

const authStore = useAuthStore();
const route = useRoute();
const loading = ref(false);

// The code may come in the link (?code=...), so an administrator can send a ready-made address.
const formSchema = computed((): VbenFormSchema[] => [
  {
    component: 'VbenInput',
    componentProps: { placeholder: '邀请码，例如 ABCD-EFGH-JKLM' },
    defaultValue: typeof route.query.code === 'string' ? route.query.code : '',
    fieldName: 'code',
    label: '邀请码',
    rules: z.string().min(4, { message: '请输入管理员给你的邀请码' }),
  },
  {
    component: 'VbenInput',
    componentProps: { autocomplete: 'username', placeholder: '用户名（3–64 位字母、数字或 _.-）' },
    fieldName: 'username',
    label: '用户名',
    rules: z.string().regex(/^[\w.-]{3,64}$/, { message: '用户名为 3–64 位字母、数字或 _.-' }),
  },
  {
    component: 'VbenInput',
    componentProps: { placeholder: '显示名（姓名或昵称）' },
    fieldName: 'display_name',
    label: '显示名',
    rules: z.string().min(1, { message: '请输入显示名' }).max(64),
  },
  {
    component: 'VbenInputPassword',
    componentProps: { autocomplete: 'new-password', passwordStrength: true, placeholder: '密码（至少 10 位）' },
    fieldName: 'password',
    label: '密码',
    rules: z.string().min(10, { message: '密码至少 10 位' }),
  },
  {
    component: 'VbenInputPassword',
    componentProps: { autocomplete: 'new-password', placeholder: '再次输入密码' },
    dependencies: {
      rules(values) {
        return z.string().min(1, { message: '请再次输入密码' })
          .refine((value) => value === values.password, { message: '两次输入的密码不一致' });
      },
      triggerFields: ['password'],
    },
    fieldName: 'confirmPassword',
    label: '确认密码',
  },
]);

async function submit(values: Record<string, any>) {
  loading.value = true;
  try {
    await registerApi({ code: values.code, display_name: values.display_name, password: values.password,
                        username: values.username });
    message.success('注册成功，正在登录');
  } catch (error) {
    message.error(apiErrorMessage(error, '注册失败'));
    loading.value = false;
    return;
  }
  loading.value = false;
  await authStore.authLogin({ password: values.password, username: values.username });
}
</script>

<template>
  <AuthenticationRegister
    :form-schema="formSchema"
    :loading="loading || authStore.loginLoading"
    sub-title="凭管理员发放的邀请码创建账号"
    submit-button-text="注册"
    title="注册 Minerva"
    @submit="submit"
  />
</template>
