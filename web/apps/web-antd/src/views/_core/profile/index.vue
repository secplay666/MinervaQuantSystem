<script lang="ts" setup>
import type { AuthApi } from '#/api';

import { computed, onMounted, reactive, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';
import { useQRCode } from '@vueuse/integrations/useQRCode';

import { Alert, Button, Card, Descriptions, Form, Input, message, Space, Tabs, Tag, Typography } from 'ant-design-vue';

import { changePasswordApi, meApi, totpDisableApi, totpEnableApi, totpSetupApi } from '#/api';
import { useAuthStore } from '#/store';
import { dateTime, ROLE_LABEL } from '#/utils/format';

defineOptions({ name: 'Profile' });

const route = useRoute();
const router = useRouter();
const authStore = useAuthStore();
const me = ref<AuthApi.Me>();
const tab = ref(String(route.query.tab ?? 'info'));
const password = reactive({ confirm: '', current: '', next: '', saving: false });
const totp = reactive({ code: '', password: '', secret: '', uri: '' });
const qrcode = useQRCode(computed(() => totp.uri));

async function load() {
  me.value = await meApi();
  if (me.value.must_change_password) tab.value = 'password';
}

async function savePassword() {
  if (password.next !== password.confirm) {
    message.warning('两次输入的新密码不一致');
    return;
  }
  password.saving = true;
  try {
    const wasForced = me.value?.must_change_password;
    const tokens = await changePasswordApi(password.current, password.next);
    authStore.applyTokens(tokens);  // other sessions were ended; this one continues with the new pair
    Object.assign(password, { confirm: '', current: '', next: '' });
    message.success('密码已修改，其他设备上的登录已注销');
    await load();
    if (wasForced) router.push('/home');
  } finally {
    password.saving = false;
  }
}

async function startTotp() {
  Object.assign(totp, await totpSetupApi(), { code: '' });
}

async function enableTotp() {
  await totpEnableApi(totp.secret, totp.code, totp.password);
  message.success('两步验证已开启；以后登录需要输入验证码');
  Object.assign(totp, { code: '', password: '', secret: '', uri: '' });
  await load();
}

async function disableTotp() {
  await totpDisableApi(totp.password);
  message.success('两步验证已关闭');
  totp.password = '';
  await load();
}

onMounted(load);
</script>

<template>
  <Page title="个人设置">
    <Alert v-if="me?.must_change_password" type="warning" show-icon class="mb-4"
           message="当前使用的是临时密码，修改后才能使用其他功能。" />
    <Card size="small">
      <Tabs v-model:active-key="tab">
        <Tabs.TabPane key="info" tab="基本信息">
          <Descriptions v-if="me" :column="1" size="small" bordered>
            <Descriptions.Item label="用户名">{{ me.username }}</Descriptions.Item>
            <Descriptions.Item label="显示名">{{ me.display_name }}</Descriptions.Item>
            <Descriptions.Item label="角色">
              <Tag v-for="role in me.roles" :key="role" color="blue">{{ ROLE_LABEL[role] ?? role }}</Tag>
            </Descriptions.Item>
            <Descriptions.Item label="权限">
              <Tag v-for="p in me.permissions" :key="p" class="mb-1">{{ p }}</Tag>
            </Descriptions.Item>
            <Descriptions.Item label="两步验证">{{ me.totp_enabled ? '已开启' : '未开启' }}</Descriptions.Item>
            <Descriptions.Item label="上次登录">{{ dateTime(me.last_login_at) }}</Descriptions.Item>
          </Descriptions>
        </Tabs.TabPane>
        <Tabs.TabPane key="password" tab="修改密码">
          <Form layout="vertical" style="max-width: 420px">
            <Form.Item label="当前密码" required>
              <Input.Password v-model:value="password.current" autocomplete="current-password" />
            </Form.Item>
            <Form.Item label="新密码" required extra="至少 10 位，同时包含字母和数字或符号，不能包含用户名">
              <Input.Password v-model:value="password.next" autocomplete="new-password" />
            </Form.Item>
            <Form.Item label="确认新密码" required>
              <Input.Password v-model:value="password.confirm" autocomplete="new-password" />
            </Form.Item>
            <Button type="primary" :loading="password.saving" @click="savePassword">修改密码</Button>
          </Form>
        </Tabs.TabPane>
        <Tabs.TabPane key="totp" tab="两步验证">
          <template v-if="me?.totp_enabled">
            <Alert type="success" show-icon message="已开启两步验证。关闭需要输入当前密码。" class="mb-3" />
            <Space>
              <Input.Password v-model:value="totp.password" placeholder="当前密码" style="width: 240px" />
              <Button danger :disabled="!totp.password" @click="disableTotp">关闭两步验证</Button>
            </Space>
          </template>
          <template v-else>
            <p class="text-muted-foreground mb-3">
              使用手机上的验证器 App（如 Google Authenticator、Microsoft Authenticator）扫描二维码，然后输入 App 显示的 6 位验证码完成开启。
            </p>
            <Button v-if="!totp.secret" type="primary" @click="startTotp">开始设置</Button>
            <template v-else>
              <img :src="qrcode" alt="TOTP" style="width: 200px; height: 200px" class="mb-2 bg-white p-2" />
              <p>无法扫码时手动输入密钥：<Typography.Text code copyable>{{ totp.secret }}</Typography.Text></p>
              <Space class="mt-2">
                <Input v-model:value="totp.code" placeholder="6 位验证码" :maxlength="6" style="width: 160px" />
                <Input.Password v-model:value="totp.password" placeholder="当前密码" style="width: 200px" />
                <Button type="primary" :disabled="totp.code.length !== 6 || !totp.password" @click="enableTotp">
                  验证并开启
                </Button>
              </Space>
            </template>
          </template>
        </Tabs.TabPane>
      </Tabs>
    </Card>
  </Page>
</template>
