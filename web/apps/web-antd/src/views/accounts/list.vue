<script lang="ts" setup>
import type { Dayjs } from 'dayjs';

import type { Account } from '#/api';

import { onMounted, reactive, ref } from 'vue';
import { useRouter } from 'vue-router';

import { useAccess } from '@vben/access';
import { Page } from '@vben/common-ui';

import dayjs from 'dayjs';
import { Button, Card, DatePicker, Form, Input, message, Modal, Radio, Table, Tag } from 'ant-design-vue';

import { accountsApi, createAccountApi } from '#/api';
import { yuan } from '#/utils/format';

const router = useRouter();
const { hasAccessByCodes } = useAccess();
const accounts = ref<Account[]>([]);
const loading = ref(false);
const form = reactive<{ account_id: string; cash: string; mode: 'manual' | 'paper'; name: string; note: string; open: boolean;
                        start_date: Dayjs; strategy_config: string }>({
  account_id: '', cash: '10000000', mode: 'paper', name: '', note: '', open: false, start_date: dayjs(),
  strategy_config: 'configs/strategies/multifactor_rules.json',
});

async function load() {
  loading.value = true;
  try {
    accounts.value = await accountsApi();
  } finally {
    loading.value = false;
  }
}

async function create() {
  if (!/^[A-Za-z0-9_-]{2,32}$/.test(form.account_id) || !form.name) {
    message.warning('账户编号为 2–32 位字母、数字、下划线或连字符；名称必填');
    return;
  }
  await createAccountApi({ account_id: form.account_id, cash: form.cash, mode: form.mode, name: form.name,
                           note: form.note || undefined, start_date: form.start_date.format('YYYY-MM-DD'),
                           strategy_config: form.strategy_config });
  message.success('账户已创建');
  form.open = false;
  await load();
}

const columns = [
  { dataIndex: 'account_id', title: '编号', width: 120 },
  { dataIndex: 'name', title: '名称' },
  { key: 'mode', title: '类型', width: 90 },
  { key: 'nav', title: '净值（元）', align: 'right' as const },
  { key: 'cash', title: '现金（元）', align: 'right' as const },
  { key: 'positions', title: '持仓', align: 'right' as const, width: 80 },
  { key: 'snapshot', title: '最新快照', width: 110 },
  { dataIndex: 'holdings_confirmed_date', title: '持仓确认日', width: 110 },
  { key: 'active', title: '状态', width: 80 },
];

onMounted(load);
</script>

<template>
  <Page title="账户" description="模拟账户按审核通过的意图模拟成交；手工账户的持仓来自录入与成交回填">
    <template #extra>
      <Button v-if="hasAccessByCodes(['account:manage'])" type="primary" @click="form.open = true">新建账户</Button>
    </template>
    <Card size="small">
      <Table :columns="columns" :data-source="accounts" :loading="loading" row-key="account_id" :pagination="false"
             class="cursor-pointer"
             :custom-row="(record: Account) => ({ onClick: () => router.push(`/accounts/${record.account_id}`) })">
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'mode'">
            <Tag :color="record.mode === 'paper' ? 'purple' : 'blue'">{{ record.mode === 'paper' ? '模拟' : '手工' }}</Tag>
          </template>
          <template v-else-if="column.key === 'nav'">{{ yuan(record.latest?.nav_fen) }}</template>
          <template v-else-if="column.key === 'cash'">{{ yuan(record.latest?.cash_fen) }}</template>
          <template v-else-if="column.key === 'positions'">{{ record.latest?.positions ?? '—' }}</template>
          <template v-else-if="column.key === 'snapshot'">{{ record.latest?.trade_date ?? '—' }}</template>
          <template v-else-if="column.key === 'active'">
            <Tag :color="record.is_active ? 'success' : 'default'">{{ record.is_active ? '启用' : '停用' }}</Tag>
          </template>
        </template>
      </Table>
    </Card>

    <Modal v-model:open="form.open" title="新建账户" ok-text="创建" @ok="create">
      <Form layout="vertical">
        <Form.Item label="类型" required>
          <Radio.Group v-model:value="form.mode">
            <Radio value="paper">模拟账户</Radio>
            <Radio value="manual">手工账户（真实持仓）</Radio>
          </Radio.Group>
        </Form.Item>
        <Form.Item label="编号" required extra="创建后不能修改，例如 paper1、real1">
          <Input v-model:value="form.account_id" :maxlength="32" />
        </Form.Item>
        <Form.Item label="名称" required><Input v-model:value="form.name" :maxlength="64" /></Form.Item>
        <Form.Item label="初始资金（元）" required><Input v-model:value="form.cash" /></Form.Item>
        <Form.Item label="开始日期" required><DatePicker v-model:value="form.start_date" /></Form.Item>
        <Form.Item label="策略配置"><Input v-model:value="form.strategy_config" /></Form.Item>
        <Form.Item label="备注"><Input v-model:value="form.note" /></Form.Item>
      </Form>
    </Modal>
  </Page>
</template>
