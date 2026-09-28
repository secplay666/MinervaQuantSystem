<script lang="ts" setup>
import { onMounted, reactive, ref } from 'vue';

import { Page } from '@vben/common-ui';

import { Button, Card, Input, Popover, Space, Table } from 'ant-design-vue';

import { auditApi } from '#/api';
import { dateTime } from '#/utils/format';

const rows = ref<Record<string, any>[]>([]);
const loading = ref(false);
const filter = reactive({ action: '', actor: '' });
const more = ref(true);

async function load(append = false) {
  loading.value = true;
  try {
    const page = await auditApi({ action: filter.action || undefined, actor: filter.actor || undefined,
                                  before_id: append ? rows.value.at(-1)?.id : undefined, limit: 100 });
    rows.value = append ? [...rows.value, ...page] : page;
    more.value = page.length === 100;
  } finally {
    loading.value = false;
  }
}

const columns = [
  { dataIndex: 'at', title: '时间', width: 150, customRender: ({ text }: any) => dateTime(text) },
  { dataIndex: 'actor', title: '操作者', width: 110 },
  { dataIndex: 'action', title: '动作', width: 170 },
  { key: 'subject', title: '对象' },
  { dataIndex: 'reason', title: '原因' },
  { dataIndex: 'ip', title: 'IP', width: 130 },
  { key: 'diff', title: '内容', width: 80 },
];

onMounted(() => load());
</script>

<template>
  <Page title="审计日志" description="审核、修改、回填、导入、账户、用户与权限的所有操作，只追加不修改">
    <Card size="small">
      <Space class="mb-3" wrap>
        <Input v-model:value="filter.actor" placeholder="操作者" style="width: 140px" allow-clear />
        <Input v-model:value="filter.action" placeholder="动作前缀，如 intent." style="width: 200px" allow-clear />
        <Button type="primary" @click="load()">查询</Button>
      </Space>
      <Table :columns="columns" :data-source="rows" :loading="loading" row-key="id" size="small" :pagination="false">
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'subject'">{{ record.subject_type }} · {{ record.subject_id }}</template>
          <template v-else-if="column.key === 'diff'">
            <Popover v-if="record.before || record.after" trigger="click" :overlay-style="{ maxWidth: '640px' }">
              <template #content>
                <pre class="max-h-96 overflow-auto text-xs">{{ JSON.stringify({ before: record.before, after: record.after }, null, 2) }}</pre>
              </template>
              <Button size="small" type="link">查看</Button>
            </Popover>
          </template>
        </template>
      </Table>
      <div class="mt-3 text-center">
        <Button v-if="more && rows.length" :loading="loading" @click="load(true)">加载更多</Button>
      </div>
    </Card>
  </Page>
</template>
