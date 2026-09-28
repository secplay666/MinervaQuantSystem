<script lang="ts" setup>
import type { Dayjs } from 'dayjs';

import type { Account, DecisionRun } from '#/api';

import { onMounted, reactive, ref } from 'vue';
import { useRouter } from 'vue-router';

import { useAccess } from '@vben/access';
import { Page } from '@vben/common-ui';

import { Button, Card, Checkbox, DatePicker, Form, Input, message, Modal, Select, Space, Table, Tag, Tooltip } from 'ant-design-vue';

import { accountsApi, decisionsApi, jobApi, triggerDecisionApi } from '#/api';
import { RUN_KIND, RUN_STATUS, yuan } from '#/utils/format';

const router = useRouter();
const { hasAccessByCodes } = useAccess();
const loading = ref(false);
const runs = ref<DecisionRun[]>([]);
const accounts = ref<Account[]>([]);
const filter = reactive<{ account_id?: string; include_superseded: boolean; trade_date?: Dayjs }>({ include_superseded: false });

const trigger = reactive({ account_id: '', open: false, reason: '', rerun: false, running: false });

async function load() {
  loading.value = true;
  try {
    runs.value = await decisionsApi({
      account_id: filter.account_id || undefined,
      include_superseded: filter.include_superseded,
      limit: 100,
      trade_date: filter.trade_date ? filter.trade_date.format('YYYY-MM-DD') : undefined,
    });
  } finally {
    loading.value = false;
  }
}

async function runTrigger() {
  if (!trigger.account_id || trigger.reason.trim().length < 2) {
    message.warning('请选择账户并填写原因');
    return;
  }
  trigger.running = true;
  try {
    const job = await triggerDecisionApi(trigger.account_id, trigger.reason, trigger.rerun);
    message.info('决策任务已开始，通常需要 20–60 秒');
    for (let i = 0; i < 90; i++) {
      await new Promise((r) => setTimeout(r, 2000));
      const state = await jobApi(job.id);
      if (state.status !== 'running') {
        if (state.status === 'done') {
          const outcome = state.result?.[0];
          message.success(`完成：${outcome?.status ?? ''} ${outcome?.message ?? ''}`);
        } else {
          message.error(`失败：${state.result}`);
        }
        break;
      }
    }
    trigger.open = false;
    await load();
  } finally {
    trigger.running = false;
  }
}

const columns = [
  { dataIndex: 'trade_date', title: '交易日', width: 110 },
  { dataIndex: 'account_id', title: '账户', width: 120 },
  { key: 'kind', title: '类型', width: 100 },
  { key: 'status', title: '状态', width: 100 },
  { key: 'intents', title: '交易意图', width: 150 },
  { key: 'nav', title: '账户净值（元）', align: 'right' as const, width: 160 },
  { key: 'gate', title: '说明' },
  { dataIndex: 'next_session', title: '执行日', width: 110 },
];

onMounted(async () => {
  await Promise.all([load(), accountsApi().then((v) => (accounts.value = v)).catch(() => [])]);
});
</script>

<template>
  <Page title="每日决策" description="每个交易日盘后为每个账户生成一次决策；调仓日产生交易清单，需审核后执行">
    <Card size="small">
      <Space wrap class="mb-3">
        <Select v-model:value="filter.account_id" allow-clear placeholder="全部账户" style="width: 180px"
                :options="accounts.map((a) => ({ label: `${a.name}（${a.account_id}）`, value: a.account_id }))" />
        <DatePicker v-model:value="filter.trade_date" placeholder="交易日" />
        <Checkbox v-model:checked="filter.include_superseded">显示已作废</Checkbox>
        <Button type="primary" @click="load">查询</Button>
        <Button v-if="hasAccessByCodes(['decision:trigger'])" danger @click="trigger.open = true">强制调仓…</Button>
      </Space>
      <Table :columns="columns" :data-source="runs" :loading="loading" row-key="run_id" size="middle"
             :pagination="{ pageSize: 20 }" class="cursor-pointer"
             :custom-row="(record: DecisionRun) => ({ onClick: () => router.push(`/decisions/${record.run_id}`) })">
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'kind'">{{ RUN_KIND[record.kind] }}</template>
          <template v-else-if="column.key === 'status'">
            <Tag :color="RUN_STATUS[record.status]?.color">{{ RUN_STATUS[record.status]?.label }}</Tag>
          </template>
          <template v-else-if="column.key === 'intents'">
            <template v-if="record.summary?.buys !== undefined">
              买 {{ record.summary.buys }} · 卖 {{ record.summary.sells }}
              <Tag v-if="record.summary.rejected" color="error">拒 {{ record.summary.rejected }}</Tag>
            </template>
            <span v-else class="text-muted-foreground">—</span>
          </template>
          <template v-else-if="column.key === 'nav'">{{ yuan(record.nav_fen) }}</template>
          <template v-else-if="column.key === 'gate'">
            <template v-for="gate in record.gates.filter((g: any) => !g.passed).slice(0, 1)" :key="gate.gate">
              <Tooltip :title="gate.hint"><Tag color="error">{{ gate.gate }}</Tag>{{ gate.message }}</Tooltip>
            </template>
            <span v-if="record.status === 'failed'">{{ record.reason }}</span>
            <span v-if="record.kind === 'forced' && record.reason" class="text-muted-foreground">原因：{{ record.reason }}</span>
          </template>
        </template>
      </Table>
    </Card>

    <Modal v-model:open="trigger.open" title="强制调仓" :confirm-loading="trigger.running" ok-text="开始" @ok="runTrigger">
      <p class="text-muted-foreground mb-3">
        在非调仓日为账户立即生成目标组合和交易清单（例如新账户建仓）。原因会记入审计日志。
      </p>
      <Form layout="vertical">
        <Form.Item label="账户" required>
          <Select v-model:value="trigger.account_id"
                  :options="accounts.filter((a) => a.is_active).map((a) => ({ label: `${a.name}（${a.account_id}）`, value: a.account_id }))" />
        </Form.Item>
        <Form.Item label="原因" required>
          <Input v-model:value="trigger.reason" placeholder="例如：新账户建仓" :maxlength="200" />
        </Form.Item>
        <Form.Item>
          <Checkbox v-model:checked="trigger.rerun">替换当日已完成的决策（需未审核）</Checkbox>
        </Form.Item>
      </Form>
    </Modal>
  </Page>
</template>
