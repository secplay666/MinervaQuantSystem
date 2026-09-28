<script lang="ts" setup>
import type { EventItem } from '#/api';

import { onMounted, reactive, ref } from 'vue';
import { useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';

import { Button, Card, Checkbox, Select, Space, Table, Tag } from 'ant-design-vue';

import { eventsApi, readAllEventsApi, readEventApi } from '#/api';
import { dateTime, LEVEL } from '#/utils/format';

const router = useRouter();
const items = ref<EventItem[]>([]);
const unread = ref(0);
const loading = ref(false);
const filter = reactive<{ category?: string; level?: string; unread: boolean }>({ unread: false });
const CATEGORY: Record<string, string> = { account: '账户', data: '数据', decision: '决策', risk: '风险', system: '系统' };

async function load() {
  loading.value = true;
  try {
    const data = await eventsApi({ category: filter.category, level: filter.level, limit: 200, unread: filter.unread });
    items.value = data.items;
    unread.value = data.unread;
  } finally {
    loading.value = false;
  }
}

async function open(row: Record<string, any>) {
  const event = row as EventItem;
  if (!event.read) {
    await readEventApi(event.event_id);
    event.read = true;
    unread.value = Math.max(0, unread.value - 1);
  }
  if (event.run_id) router.push(`/decisions/${event.run_id}`);
  else if (event.account_id) router.push(`/accounts/${event.account_id}`);
}

async function readAll() {
  await readAllEventsApi();
  await load();
}

const columns = [
  { key: 'level', title: '级别', width: 80 },
  { key: 'category', title: '类别', width: 80 },
  { key: 'title', title: '内容' },
  { dataIndex: 'account_id', title: '账户', width: 100 },
  { dataIndex: 'trade_date', title: '交易日', width: 110 },
  { key: 'at', title: '时间', width: 150 },
];

onMounted(load);
</script>

<template>
  <Page title="通知中心" :description="`未读 ${unread} 条`">
    <template #extra><Button :disabled="!unread" @click="readAll">全部标为已读</Button></template>
    <Card size="small">
      <Space wrap class="mb-3">
        <Select v-model:value="filter.level" allow-clear placeholder="全部级别" style="width: 120px"
                :options="Object.entries(LEVEL).map(([value, v]) => ({ label: v.label, value }))" />
        <Select v-model:value="filter.category" allow-clear placeholder="全部类别" style="width: 120px"
                :options="Object.entries(CATEGORY).map(([value, label]) => ({ label, value }))" />
        <Checkbox v-model:checked="filter.unread">只看未读</Checkbox>
        <Button type="primary" @click="load">查询</Button>
      </Space>
      <Table :columns="columns" :data-source="items" :loading="loading" row-key="event_id" size="small"
             :pagination="{ pageSize: 30 }" class="cursor-pointer" :custom-row="(record: EventItem) => ({ onClick: () => open(record) })">
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'level'"><Tag :color="LEVEL[record.level]?.color">{{ LEVEL[record.level]?.label }}</Tag></template>
          <template v-else-if="column.key === 'category'">{{ CATEGORY[record.category] ?? record.category }}</template>
          <template v-else-if="column.key === 'title'">
            <span :class="record.read ? 'text-muted-foreground' : 'font-semibold'">{{ record.title }}</span>
            <div class="text-muted-foreground text-xs">{{ record.body }}<template v-if="record.action_hint"> · {{ record.action_hint }}</template></div>
          </template>
          <template v-else-if="column.key === 'at'">{{ dateTime(record.at) }}</template>
        </template>
      </Table>
    </Card>
  </Page>
</template>
