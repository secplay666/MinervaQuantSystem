<script lang="ts" setup>
import type { Dayjs } from 'dayjs';

import type { EchartsUIType } from '@vben/plugins/echarts';

import type { Account, AccountDetail } from '#/api';

import { computed, nextTick, onMounted, reactive, ref } from 'vue';
import { useRouter } from 'vue-router';

import { useAccess } from '@vben/access';
import { Page } from '@vben/common-ui';
import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import dayjs from 'dayjs';
import { Button, Card, DatePicker, Form, Input, message, Modal, Radio, Select, Space, Table, Tag } from 'ant-design-vue';

import { accountApi, accountNavApi, accountsApi, createAccountApi } from '#/api';
import { changeColor, pct, yuan } from '#/utils/format';

const router = useRouter();
const { hasAccessByCodes } = useAccess();
const accounts = ref<Account[]>([]);
const loading = ref(false);
const form = reactive<{ account_id: string; cash: string; mode: 'manual' | 'paper'; name: string; note: string; open: boolean;
                        start_date: Dayjs; strategy_config: string }>({
  account_id: '', cash: '10000000', mode: 'paper', name: '', note: '', open: false, start_date: dayjs(),
  strategy_config: 'configs/strategies/multifactor_rules.json',
});

const navChart = ref<EchartsUIType>();
const { renderEcharts } = useEcharts(navChart);
const hasNav = ref(false);

/** Every account's NAV relative to its first snapshot (daily comparison, plan P6). */
async function drawNav() {
  const series = await Promise.all(accounts.value.map(async (a) => ({ account: a, rows: await accountNavApi(a.account_id) })));
  const withData = series.filter((x) => x.rows.length);
  hasNav.value = withData.length > 0;
  if (!hasNav.value) return;
  const dates = [...new Set(withData.flatMap((x) => x.rows.map((r) => r.trade_date)))].sort();
  await nextTick();
  renderEcharts({
    grid: { bottom: 30, left: 60, right: 20, top: 40 },
    legend: { top: 0 },
    series: withData.map(({ account, rows }) => {
      const base = rows[0]!.nav_fen;
      const byDate = new Map(rows.map((r) => [r.trade_date, (r.nav_fen / base).toFixed(4)]));
      return { connectNulls: true, data: dates.map((d) => byDate.get(d) ?? null), name: `${account.name}（${account.account_id}）`,
               showSymbol: dates.length < 40, type: 'line' };
    }),
    tooltip: { trigger: 'axis' },
    xAxis: { data: dates, type: 'category' },
    yAxis: { scale: true, type: 'value' },
  });
}

// -- holdings comparison of two accounts (paper against real, plan P6) ------------------------
const compare = reactive<{ a?: string; b?: string; left?: AccountDetail; right?: AccountDetail }>({});
const accountOptions = computed(() => accounts.value.map((a) => ({ label: `${a.name}（${a.account_id}）`, value: a.account_id })));
const compareRows = computed(() => {
  const { left, right } = compare;
  if (!left || !right) return [];
  const map = new Map<string, { a: number; b: number; name?: null | string; qa: number; qb: number; symbol: string }>();
  for (const [side, detail] of [['a', left], ['b', right]] as const) {
    for (const h of detail.holdings) {
      const row = map.get(h.symbol) ?? { a: 0, b: 0, name: h.name, qa: 0, qb: 0, symbol: h.symbol };
      row[side] = h.weight ?? 0;
      row[side === 'a' ? 'qa' : 'qb'] = h.qty;
      map.set(h.symbol, row);
    }
  }
  return [...map.values()].sort((x, y) => Math.abs(y.a - y.b) - Math.abs(x.a - x.b) || x.symbol.localeCompare(y.symbol));
});
const overlap = computed(() => compareRows.value.reduce((sum, r) => sum + Math.min(r.a, r.b), 0));

async function loadCompare() {
  if (!compare.a || !compare.b) return;
  [compare.left, compare.right] = await Promise.all([accountApi(compare.a), accountApi(compare.b)]);
}

async function load() {
  loading.value = true;
  try {
    accounts.value = await accountsApi();
  } finally {
    loading.value = false;
  }
  if (accounts.value.length >= 2 && !compare.a) {
    const paper = accounts.value.find((a) => a.mode === 'paper') ?? accounts.value[0]!;
    const other = accounts.value.find((a) => a.account_id !== paper.account_id)!;
    compare.a = paper.account_id;
    compare.b = other.account_id;
    await loadCompare();
  }
  await drawNav();
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
const compareColumns = [
  { key: 'symbol', title: '代码', width: 130 },
  { key: 'a', title: '账户 A 权重', align: 'right' as const },
  { key: 'b', title: '账户 B 权重', align: 'right' as const },
  { key: 'diff', title: 'A − B', align: 'right' as const },
  { key: 'qty', title: '数量（A / B）', align: 'right' as const },
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

    <Card v-if="hasNav" class="mt-4" size="small" title="净值对比（各自首个快照 = 1）">
      <EchartsUI ref="navChart" height="320px" />
    </Card>

    <Card v-if="accounts.length >= 2" class="mt-4" size="small" title="持仓对比">
      <template #extra>
        <Space>
          <span>A</span>
          <Select v-model:value="compare.a" :options="accountOptions" size="small" style="width: 200px" @change="loadCompare" />
          <span>B</span>
          <Select v-model:value="compare.b" :options="accountOptions" size="small" style="width: 200px" @change="loadCompare" />
        </Space>
      </template>
      <div class="text-muted-foreground mb-2 text-xs">
        按最新收盘价计权；重合度 = Σ min(A, B)，为 {{ pct(overlap) }}。用于对照模拟账户与真实持仓的差异。
      </div>
      <Table :columns="compareColumns" :data-source="compareRows" row-key="symbol" size="small" :pagination="{ pageSize: 20 }">
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'symbol'">
            <a @click="router.push(`/instruments/${record.symbol}`)">{{ record.symbol }}</a>
            <span class="text-muted-foreground ml-1 text-xs">{{ record.name }}</span>
          </template>
          <template v-else-if="column.key === 'a'">{{ pct(record.a) }}</template>
          <template v-else-if="column.key === 'b'">{{ pct(record.b) }}</template>
          <template v-else-if="column.key === 'diff'">
            <span :style="{ color: changeColor(record.a - record.b) }">{{ pct(record.a - record.b, 2, true) }}</span>
          </template>
          <template v-else-if="column.key === 'qty'">{{ record.qa }} / {{ record.qb }}</template>
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
