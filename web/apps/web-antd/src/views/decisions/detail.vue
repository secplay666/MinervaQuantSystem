<script lang="ts" setup>
import type { DecisionDetail, Intent } from '#/api';

import { computed, onMounted, reactive, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import { useAccess } from '@vben/access';
import { Page } from '@vben/common-ui';
import { useAccessStore } from '@vben/stores';

import {
  Alert, Button, Card, Col, Descriptions, Empty, Form, Input, InputNumber, message, Modal, Popover, Row, Space,
  Statistic, Table, Tabs, Tag, Tooltip,
} from 'ant-design-vue';

import { approveAllApi, approveIntentApi, decisionApi, modifyIntentApi, overrideIntentApi, rejectIntentApi } from '#/api';
import { apiURL } from '#/api/request';
import { bigYuan, dateTime, EXECUTION, INTENT_STATUS, LEVEL, pct, price, RISK, RUN_KIND, RUN_STATUS, yuan } from '#/utils/format';

const route = useRoute();
const router = useRouter();
const { hasAccessByCodes } = useAccess();
const runId = computed(() => String(route.params.runId));
const run = ref<DecisionDetail>();
const loading = ref(false);
const busy = ref<string>('');
const canApprove = computed(() => hasAccessByCodes(['decision:approve']));
const canOverride = computed(() => hasAccessByCodes(['decision:trigger']));

const dialog = reactive<{ intent?: Intent; kind: '' | 'modify' | 'override' | 'reject'; qty: number; reason: string }>({
  kind: '', qty: 0, reason: '',
});

const pendingCount = computed(() => run.value?.intents.filter((i) => i.status === 'pending_approval').length ?? 0);
const cleanPending = computed(
  () => run.value?.intents.filter((i) => i.status === 'pending_approval' && i.risk === 'pass').length ?? 0,
);
const deadline = computed(() => run.value?.intents[0]?.valid_until);

async function load() {
  loading.value = true;
  try {
    run.value = await decisionApi(runId.value);
  } finally {
    loading.value = false;
  }
}

function replace(updated: Intent) {
  if (!run.value) return;
  const index = run.value.intents.findIndex((i) => i.intent_id === updated.intent_id);
  if (index >= 0) run.value.intents[index] = updated;
}

async function approve(row: Record<string, any>) {
  const intent = row as Intent;
  busy.value = intent.intent_id;
  try {
    replace(await approveIntentApi(intent.intent_id));
  } finally {
    busy.value = '';
  }
}

function open(kind: 'modify' | 'override' | 'reject', row: Record<string, any>) {
  const intent = row as Intent;
  Object.assign(dialog, { intent, kind, qty: intent.qty, reason: '' });
}

async function submitDialog() {
  const intent = dialog.intent;
  if (!intent) return;
  if (!dialog.reason.trim()) {
    message.warning('请填写原因');
    return;
  }
  busy.value = intent.intent_id;
  try {
    const call = { modify: () => modifyIntentApi(intent.intent_id, dialog.qty, dialog.reason),
                   override: () => overrideIntentApi(intent.intent_id, dialog.reason),
                   reject: () => rejectIntentApi(intent.intent_id, dialog.reason) }[dialog.kind as 'modify'];
    replace(await call());
    dialog.kind = '';
  } finally {
    busy.value = '';
  }
}

function approveAll(includeWarnings: boolean) {
  const count = includeWarnings ? pendingCount.value : cleanPending.value;
  Modal.confirm({
    content: `将批准 ${count} 条待审核意图${includeWarnings ? '（包括有警告的）' : '（仅风险检查全部通过的）'}。`,
    okText: '确认批准',
    title: '批量批准',
    async onOk() {
      const result = await approveAllApi(runId.value, includeWarnings);
      message.success(`已批准 ${result.approved} 条`);
      await load();
    },
  });
}

async function exportCsv() {
  const response = await fetch(`${apiURL}/decisions/${runId.value}/export.csv`, {
    headers: { Authorization: `Bearer ${useAccessStore().accessToken}` },
  });
  if (!response.ok) {
    message.error('导出失败');
    return;
  }
  const url = URL.createObjectURL(await response.blob());
  const link = Object.assign(document.createElement('a'), { download: `${runId.value}.csv`, href: url });
  link.click();
  URL.revokeObjectURL(url);
}

const intentColumns = [
  { dataIndex: 'seq', title: '#', width: 50 },
  { key: 'symbol', title: '代码', width: 90 },
  { key: 'side', title: '方向', width: 70 },
  { key: 'qty', title: '数量（股）', align: 'right' as const, width: 120 },
  { key: 'ref', title: '参考价', align: 'right' as const, width: 90 },
  { key: 'band', title: '次日涨停 / 跌停', align: 'right' as const, width: 140 },
  { key: 'notional', title: '预计金额（元）', align: 'right' as const, width: 140 },
  { key: 'risk', title: '风险检查', width: 110 },
  { key: 'status', title: '状态', width: 100 },
  { key: 'actions', title: '操作', width: 220 },
];

const targetColumns = [
  { dataIndex: 'rank', title: '排名', width: 70 },
  { key: 'symbol', title: '代码', width: 90 },
  { key: 'weight', title: '目标权重', align: 'right' as const },
  { dataIndex: 'target_qty', title: '目标数量', align: 'right' as const },
  { key: 'score', title: '综合得分', align: 'right' as const },
  { key: 'industry', title: '行业' },
  { key: 'families', title: '各类因子得分' },
];

const FAMILY: Record<string, string> = {
  f_growth: '成长', f_liquidity: '流动性', f_momentum: '动量', f_quality: '质量', f_size: '规模',
  f_technical: '技术', f_value: '价值', f_volatility: '波动',
};

onMounted(load);
</script>

<template>
  <Page :title="`决策 ${run?.trade_date ?? ''}`" :description="runId">
    <template #extra>
      <Space>
        <Button @click="router.push('/decisions')">返回列表</Button>
        <Button :disabled="!run?.intents.length" @click="exportCsv">导出 CSV</Button>
      </Space>
    </template>
    <template v-if="run">
      <Alert v-if="run.status === 'blocked'" type="error" show-icon class="mb-4"
             :message="`决策被闸门阻断：${run.gates.find((g) => !g.passed)?.gate} ${run.gates.find((g) => !g.passed)?.message}`"
             :description="run.gates.find((g) => !g.passed)?.hint ?? ''" />
      <Alert v-else-if="run.status === 'failed'" type="error" show-icon class="mb-4" message="决策运行失败"
             :description="run.reason ?? ''" />
      <Alert v-else-if="pendingCount" type="warning" show-icon class="mb-4"
             :message="`${pendingCount} 条交易意图待审核，审核截止 ${dateTime(deadline)}（执行日 ${run.next_session}）`" />

      <Card size="small" class="mb-4">
        <Descriptions :column="{ xs: 1, sm: 2, lg: 4 }" size="small">
          <Descriptions.Item label="账户">{{ run.account_id }}</Descriptions.Item>
          <Descriptions.Item label="类型">{{ RUN_KIND[run.kind] }}</Descriptions.Item>
          <Descriptions.Item label="状态">
            <Tag :color="RUN_STATUS[run.status]?.color">{{ RUN_STATUS[run.status]?.label }}</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="执行日">{{ run.next_session ?? '—' }}</Descriptions.Item>
          <Descriptions.Item label="账户净值">{{ yuan(run.nav_fen) }} 元</Descriptions.Item>
          <Descriptions.Item label="现金">{{ yuan(run.cash_fen) }} 元</Descriptions.Item>
          <Descriptions.Item label="持仓">{{ run.positions ?? 0 }} 只</Descriptions.Item>
          <Descriptions.Item label="策略">{{ run.strategy_id }}（{{ run.config_hash?.slice(0, 8) }}）</Descriptions.Item>
          <Descriptions.Item v-if="run.reason" label="原因" :span="2">{{ run.reason }}</Descriptions.Item>
          <Descriptions.Item label="数据版本">{{ run.data_version?.slice(0, 12) }}</Descriptions.Item>
          <Descriptions.Item label="生成">{{ dateTime(run.created_at) }} · {{ run.created_by }}</Descriptions.Item>
        </Descriptions>
        <div class="mt-2">
          <span class="text-muted-foreground mr-2">闸门</span>
          <Tooltip v-for="gate in run.gates" :key="gate.gate" :title="`${gate.message}${gate.hint ? '；' + gate.hint : ''}`">
            <Tag :color="gate.passed ? 'success' : 'error'">{{ gate.gate }} {{ gate.passed ? '通过' : '阻断' }}</Tag>
          </Tooltip>
        </div>
      </Card>

      <Row v-if="run.summary?.buys !== undefined" :gutter="[16, 16]" class="mb-4">
        <Col :xs="12" :md="4"><Card size="small"><Statistic title="卖出" :value="run.summary.sells" suffix="笔" /></Card></Col>
        <Col :xs="12" :md="4"><Card size="small"><Statistic title="买入" :value="run.summary.buys" suffix="笔" /></Card></Col>
        <Col :xs="12" :md="4"><Card size="small"><Statistic title="卖出金额" :value="bigYuan(run.summary.sell_notional_fen)" /></Card></Col>
        <Col :xs="12" :md="4"><Card size="small"><Statistic title="买入金额" :value="bigYuan(run.summary.buy_notional_fen)" /></Card></Col>
        <Col :xs="12" :md="4"><Card size="small"><Statistic title="预计费用" :value="yuan(run.summary.fees_fen)" suffix="元" /></Card></Col>
        <Col :xs="12" :md="4"><Card size="small"><Statistic title="换手率" :value="pct(run.summary.turnover, 1)" /></Card></Col>
      </Row>

      <Card size="small">
        <Tabs>
          <Tabs.TabPane key="intents" :tab="`交易清单（${run.intents.length}）`">
            <Space v-if="canApprove && pendingCount" class="mb-3">
              <Button type="primary" :disabled="!cleanPending" @click="approveAll(false)">
                批准全部通过项（{{ cleanPending }}）
              </Button>
              <Button @click="approveAll(true)">批准全部待审核（{{ pendingCount }}）</Button>
            </Space>
            <Table :columns="intentColumns" :data-source="run.intents" row-key="intent_id" size="small"
                   :pagination="{ pageSize: 50 }" :scroll="{ x: 1200 }">
              <template #bodyCell="{ column, record }">
                <template v-if="column.key === 'symbol'">
                  <a @click="router.push(`/instruments/${record.symbol}`)">{{ record.symbol }}</a>
                  <div class="text-muted-foreground text-xs">{{ record.name }}</div>
                </template>
                <template v-else-if="column.key === 'side'">
                  <Tag :color="record.side === 'buy' ? 'red' : 'green'">{{ record.side === 'buy' ? '买入' : '卖出' }}</Tag>
                </template>
                <template v-else-if="column.key === 'qty'">
                  {{ record.qty.toLocaleString() }}
                  <div v-if="record.qty !== record.proposed_qty" class="text-muted-foreground text-xs">
                    原 {{ record.proposed_qty.toLocaleString() }}
                  </div>
                </template>
                <template v-else-if="column.key === 'ref'">{{ price(record.ref_price_fen) }}</template>
                <template v-else-if="column.key === 'band'">{{ price(record.limit_up_fen) }} / {{ price(record.limit_down_fen) }}</template>
                <template v-else-if="column.key === 'notional'">
                  {{ yuan(record.est_notional_fen) }}
                  <div class="text-muted-foreground text-xs">费 {{ yuan(record.est_fees_fen) }}</div>
                </template>
                <template v-else-if="column.key === 'risk'">
                  <Popover v-if="record.checks.length" title="风险检查">
                    <template #content>
                      <div v-for="check in record.checks" :key="check.rule_id" class="mb-1">
                        <Tag :color="RISK[check.decision]?.color">{{ check.rule_id }}</Tag>{{ check.message }}
                        <span v-if="check.actual" class="text-muted-foreground">（{{ check.actual }}{{ check.limit ? ' / ' + check.limit : '' }}）</span>
                      </div>
                    </template>
                    <Tag :color="RISK[record.risk]?.color" class="cursor-help">{{ RISK[record.risk]?.label }}</Tag>
                  </Popover>
                  <Tag v-else color="success">通过</Tag>
                </template>
                <template v-else-if="column.key === 'status'">
                  <Tooltip :title="record.history.map((h: any) => `${dateTime(h.at)} ${h.actor} ${h.action}${h.reason ? '：' + h.reason : ''}`).join('\n')">
                    <Tag :color="INTENT_STATUS[record.status]?.color">{{ INTENT_STATUS[record.status]?.label ?? record.status }}</Tag>
                  </Tooltip>
                  <Tooltip v-if="record.execution" :title="record.execution_note">
                    <Tag :color="EXECUTION[record.execution]?.color" class="mt-1">
                      {{ EXECUTION[record.execution]?.label }}{{ record.filled_qty ? ` ${record.filled_qty.toLocaleString()}` : '' }}
                    </Tag>
                  </Tooltip>
                </template>
                <template v-else-if="column.key === 'actions'">
                  <Space v-if="canApprove" size="small">
                    <Button v-if="record.status === 'pending_approval'" size="small" type="primary"
                            :loading="busy === record.intent_id" @click="approve(record)">批准</Button>
                    <Button v-if="['pending_approval', 'approved', 'modified'].includes(record.status)" size="small"
                            @click="open('modify', record)">改数量</Button>
                    <Button v-if="['pending_approval', 'approved', 'modified'].includes(record.status)" size="small" danger
                            @click="open('reject', record)">拒绝</Button>
                    <Button v-if="record.status === 'rejected_by_risk' && canOverride" size="small" danger
                            @click="open('override', record)">覆盖风控</Button>
                  </Space>
                </template>
              </template>
            </Table>
            <div v-if="run.run_checks.length" class="text-muted-foreground mt-2 text-xs">
              整体检查：<span v-for="check in run.run_checks" :key="check.rule_id">{{ check.rule_id }} {{ check.message }}（{{ check.actual }} / {{ check.limit }}）</span>
            </div>
          </Tabs.TabPane>
          <Tabs.TabPane key="targets" :tab="`目标组合（${run.targets.length}）`">
            <Table :columns="targetColumns" :data-source="run.targets" row-key="symbol" size="small" :pagination="{ pageSize: 50 }">
              <template #bodyCell="{ column, record }">
                <template v-if="column.key === 'symbol'">
                  <a @click="router.push(`/instruments/${record.symbol}`)">{{ record.symbol }}</a>
                  <div class="text-muted-foreground text-xs">{{ record.name }}</div>
                </template>
                <template v-else-if="column.key === 'weight'">{{ pct(record.target_weight) }}</template>
                <template v-else-if="column.key === 'score'">{{ record.score?.toFixed(3) ?? '—' }}</template>
                <template v-else-if="column.key === 'industry'">{{ record.explanation.industry }}</template>
                <template v-else-if="column.key === 'families'">
                  <Space size="small" wrap>
                    <template v-for="(label, key) in FAMILY" :key="key">
                      <Tag v-if="record.explanation[key] !== undefined && record.explanation[key] !== null"
                           :color="record.explanation[key] > 0 ? 'red' : 'green'">{{ label }} {{ record.explanation[key].toFixed(2) }}</Tag>
                    </template>
                  </Space>
                </template>
              </template>
            </Table>
          </Tabs.TabPane>
          <Tabs.TabPane key="events" :tab="`持仓提示（${run.events.length}）`">
            <Empty v-if="!run.events.length" description="无提示" />
            <div v-for="event in run.events" :key="event.event_id" class="mb-2">
              <Tag :color="LEVEL[event.level]?.color">{{ LEVEL[event.level]?.label }}</Tag>
              <b>{{ event.title }}</b> <span class="text-muted-foreground">{{ event.body }}</span>
              <span v-if="event.action_hint" class="text-muted-foreground">（{{ event.action_hint }}）</span>
            </div>
          </Tabs.TabPane>
        </Tabs>
      </Card>
    </template>
    <Card v-else :loading="loading" />

    <Modal :open="!!dialog.kind" :title="{ modify: '修改数量', override: '覆盖风控拒绝', reject: '拒绝交易意图' }[dialog.kind || 'modify']"
           ok-text="确认" :confirm-loading="!!busy" @cancel="dialog.kind = ''" @ok="submitDialog">
      <p v-if="dialog.intent" class="mb-3">
        {{ dialog.intent.side === 'buy' ? '买入' : '卖出' }} {{ dialog.intent.symbol }}，当前数量 {{ dialog.intent.qty.toLocaleString() }} 股
      </p>
      <Alert v-if="dialog.kind === 'override'" type="warning" show-icon class="mb-3"
             message="覆盖风控意味着明知检查未通过仍然执行，会记入审计日志。" />
      <Form layout="vertical">
        <Form.Item v-if="dialog.kind === 'modify'" label="新数量（股）" extra="买入须为整手；卖出不超过持仓，零股只能一次卖完">
          <InputNumber v-model:value="dialog.qty" :min="1" :step="100" style="width: 200px" />
        </Form.Item>
        <Form.Item label="原因" required>
          <Input.TextArea v-model:value="dialog.reason" :rows="2" :maxlength="300" />
        </Form.Item>
      </Form>
    </Modal>
  </Page>
</template>
