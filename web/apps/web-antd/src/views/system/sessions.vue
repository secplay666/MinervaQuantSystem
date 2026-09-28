<script lang="ts" setup>
import { onMounted, ref } from 'vue';

import { Page } from '@vben/common-ui';

import { Button, Card, message, Popconfirm, Table } from 'ant-design-vue';

import { revokeSessionApi, sessionsApi } from '#/api';
import { dateTime } from '#/utils/format';

const sessions = ref<Record<string, any>[]>([]);
const loading = ref(false);

async function load() {
  loading.value = true;
  try {
    sessions.value = await sessionsApi();
  } finally {
    loading.value = false;
  }
}

async function revoke(id: number) {
  await revokeSessionApi(id);
  message.success('会话已注销；该设备的访问令牌最迟 15 分钟后失效');
  await load();
}

const columns = [
  { dataIndex: 'username', title: '用户', width: 120 },
  { dataIndex: 'ip', title: 'IP', width: 140 },
  { dataIndex: 'user_agent', ellipsis: true, title: '客户端' },
  { dataIndex: 'issued_at', title: '签发', width: 150, customRender: ({ text }: any) => dateTime(text) },
  { dataIndex: 'expires_at', title: '到期', width: 150, customRender: ({ text }: any) => dateTime(text) },
  { key: 'actions', title: '', width: 90 },
];

onMounted(load);
</script>

<template>
  <Page title="在线会话" description="每次登录产生一个会话（刷新令牌，7 天有效，每次使用后轮换）">
    <Card size="small">
      <Table :columns="columns" :data-source="sessions" :loading="loading" row-key="id" size="small" :pagination="false">
        <template #bodyCell="{ column, record }">
          <Popconfirm v-if="column.key === 'actions'" title="注销这个会话？" @confirm="revoke(record.id)">
            <Button danger size="small">注销</Button>
          </Popconfirm>
        </template>
      </Table>
    </Card>
  </Page>
</template>
