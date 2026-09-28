<script lang="ts" setup>
import type { NotifyStatus } from '#/api';

import { onMounted, ref } from 'vue';

import { Page } from '@vben/common-ui';

import { Alert, Button, Card, Descriptions, DescriptionsItem, message, Table, Tag } from 'ant-design-vue';

import { notifyStatusApi, notifyTestApi } from '#/api';
import { dateTime, LEVEL } from '#/utils/format';

const status = ref<NotifyStatus>();
const loading = ref(false);
const testing = ref(false);

const STATUS: Record<string, { color: string; label: string }> = {
  failed: { color: 'error', label: '失败' },
  sending: { color: 'processing', label: '发送中' },
  sent: { color: 'success', label: '已送达' },
};
const CHANNEL: Record<string, string> = { serverchan: 'Server酱', wecom: '企业微信' };

async function load() {
  loading.value = true;
  try {
    status.value = await notifyStatusApi();
  } finally {
    loading.value = false;
  }
}

async function test() {
  testing.value = true;
  try {
    const { results } = await notifyTestApi();
    for (const r of results) {
      if (r.status === 'sent') message.success(`${CHANNEL[r.channel] ?? r.channel}：测试消息已发送`);
      else message.error(`${CHANNEL[r.channel] ?? r.channel}：${r.error}`);
    }
  } finally {
    testing.value = false;
  }
}

const columns = [
  { dataIndex: 'attempted_at', title: '时间', width: 150, customRender: ({ text }: any) => dateTime(text) },
  { key: 'level', title: '级别', width: 70 },
  { dataIndex: 'title', ellipsis: true, title: '事件' },
  { dataIndex: 'channel', title: '渠道', width: 90, customRender: ({ text }: any) => CHANNEL[text] ?? text },
  { key: 'status', title: '状态', width: 90 },
  { dataIndex: 'attempts', title: '尝试', width: 60 },
  { dataIndex: 'error', ellipsis: true, title: '错误', width: 260 },
];

onMounted(load);
</script>

<template>
  <Page title="外部通知" description="新事件在每日作业结束后汇总成一条消息推送；只发送标题，持仓风险只发条数，详情留在系统内">
    <Card :loading="loading" size="small" title="渠道">
      <template #extra>
        <Button :disabled="!status?.channels.length" :loading="testing" size="small" type="primary" @click="test">
          发送测试消息
        </Button>
      </template>
      <Alert v-for="p in status?.problems ?? []" :key="p" :message="p" class="mb-2" show-icon type="warning" />
      <Descriptions :column="1" size="small" bordered>
        <DescriptionsItem label="已配置">
          <template v-if="status?.channels.length">
            <div v-for="c in status.channels" :key="c.name">{{ c.label }}</div>
          </template>
          <span v-else class="text-gray-500">未配置（只在系统内的通知中心显示）</span>
        </DescriptionsItem>
        <DescriptionsItem label="推送级别">{{ LEVEL[status?.min_level ?? 'info']?.label }}及以上</DescriptionsItem>
        <DescriptionsItem label="回溯">只推送最近 {{ status?.lookback_hours }} 小时内的事件</DescriptionsItem>
        <DescriptionsItem label="消息内链接">{{ status?.link ?? '无' }}</DescriptionsItem>
      </Descriptions>
      <div class="mt-3 text-xs text-gray-500">
        渠道写在服务器的 <code>~/.config/minerva/app.env</code>（不经过网页，密钥不会显示）：
        <code>MINERVA_NOTIFY_WECOM</code>（企业微信群机器人的 webhook 地址或 key）、
        <code>MINERVA_NOTIFY_SERVERCHAN</code>（Server酱 SendKey）、
        <code>MINERVA_NOTIFY_MIN_LEVEL</code>（info / warning / critical）。修改后重启 quant-api 服务。
      </div>
    </Card>
    <Card class="mt-4" size="small" title="最近推送">
      <Table :columns="columns" :data-source="status?.deliveries ?? []" :loading="loading" :pagination="false"
             :row-key="(r: any) => `${r.event_id}/${r.channel}`" size="small">
        <template #bodyCell="{ column, record }">
          <Tag v-if="column.key === 'level'" :color="LEVEL[record.level]?.color">{{ LEVEL[record.level]?.label }}</Tag>
          <Tag v-else-if="column.key === 'status'" :color="STATUS[record.status]?.color">
            {{ STATUS[record.status]?.label ?? record.status }}
          </Tag>
        </template>
      </Table>
    </Card>
  </Page>
</template>
