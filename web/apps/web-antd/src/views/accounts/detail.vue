<script lang="ts" setup>
import type { Dayjs } from 'dayjs';

import type { EchartsUIType } from '@vben/plugins/echarts';

import type { AccountDetail, CorporateAction, Exposure } from '#/api';

import { computed, nextTick, onMounted, reactive, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import { useAccess } from '@vben/access';
import { Page } from '@vben/common-ui';
import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import dayjs from 'dayjs';
import {
  Alert, Button, Card, Col, DatePicker, Empty, Form, Input, InputNumber, message, Modal, Radio, Row, Space, Statistic,
  Table, Tabs, Tag,
} from 'ant-design-vue';

import {
  accountApi, accountEventsApi, accountExposureApi, accountFillsApi, accountNavApi, addFillApi, applyCorporateActionApi,
  corporateActionsApi, holdingsCommitApi, holdingsPreviewApi, reverseEventApi,
} from '#/api';
import { changeColor, dateTime, DOWN_COLOR, EVENT_KIND, pct, price, UP_COLOR, yuan } from '#/utils/format';

const route = useRoute();
const router = useRouter();
const { hasAccessByCodes } = useAccess();
const accountId = computed(() => String(route.params.accountId));
const account = ref<AccountDetail>();
const events = ref<Record<string, any>[]>([]);
const fills = ref<Record<string, any>[]>([]);
const navChart = ref<EchartsUIType>();
const { renderEcharts } = useEcharts(navChart);
const exposureChart = ref<EchartsUIType>();
const { renderEcharts: renderExposure } = useEcharts(exposureChart);
const exposure = ref<Exposure>();
const tab = ref('holdings');
const canEdit = computed(() => hasAccessByCodes(['account:edit']) && account.value?.mode === 'manual');

const fill = reactive<{ commission?: number; open: boolean; price?: number; qty?: number; side: 'buy' | 'sell'; symbol: string;
                        trade_date: Dayjs }>({ open: false, side: 'buy', symbol: '', trade_date: dayjs() });
const entry = reactive<{ as_of: Dayjs; cash: string; preview?: Awaited<ReturnType<typeof holdingsPreviewApi>>; reason: string;
                         text: string }>({ as_of: dayjs(), cash: '', reason: '', text: '' });

type ActionRow = CorporateAction & { cash: string; qty: number };
const actions = ref<ActionRow[]>([]);
const actionColumns = [
  { key: 'symbol', title: '股票' },
  { dataIndex: 'ex_date', title: '除权日' },
  { key: 'plan', title: '方案' },
  { dataIndex: 'old_quantity', title: '除权前', align: 'right' },
  { key: 'qty', title: '调整后数量', align: 'right' },
  { key: 'cash', title: '现金分红（元，税前）', align: 'right' },
  { key: 'action', title: '' },
];
async function loadActions() {
  actions.value = account.value?.mode === 'manual'
    ? (await corporateActionsApi(accountId.value)).rows.map((r) => ({ ...r, cash: (r.cash_fen / 100).toFixed(2), qty: r.new_quantity }))
    : [];
}
async function applyAction(row: ActionRow) {
  await applyCorporateActionApi(accountId.value, { cash: row.cash, event_id: row.event_id, new_quantity: row.qty,
                                                   reason: row.plan ? `除权除息：${row.plan}` : '除权除息' });
  message.success(`${row.symbol} 已按除权调整`);
  await load();
}

async function load() {
  account.value = await accountApi(accountId.value);
  void loadActions();
  const [nav, ev, fl] = await Promise.all([accountNavApi(accountId.value), accountEventsApi(accountId.value),
                                           accountFillsApi(accountId.value)]);
  events.value = ev;
  fills.value = fl;
  if (!entry.cash && account.value) entry.cash = (account.value.cash_fen / 100).toFixed(2);
  await nextTick();
  if (tab.value === 'nav') drawNav(nav);
  navData.value = nav;
}

const navData = ref<Awaited<ReturnType<typeof accountNavApi>>>([]);

/** Drawdown from the running peak of the daily NAV snapshots. */
const drawdown = computed(() => {
  let peak = 0;
  const series = navData.value.map((r) => {
    peak = Math.max(peak, r.nav_fen);
    return peak ? r.nav_fen / peak - 1 : 0;
  });
  return { current: series.at(-1) ?? 0, max: Math.min(0, ...series), series };
});

function drawNav(nav = navData.value) {
  if (!nav.length) return;
  const base = nav[0]!.nav_fen;
  renderEcharts({
    grid: [{ bottom: '34%', left: 60, right: 20, top: 30 }, { bottom: 30, height: '20%', left: 60, right: 20 }],
    legend: { data: ['净值', '回撤'], top: 0 },
    series: [
      { areaStyle: { opacity: 0.08 }, data: nav.map((r) => (r.nav_fen / base).toFixed(4)), itemStyle: { color: UP_COLOR },
        name: '净值', showSymbol: nav.length < 40, smooth: true, type: 'line' },
      { areaStyle: { opacity: 0.2 }, data: drawdown.value.series.map((d) => (d * 100).toFixed(2)),
        itemStyle: { color: DOWN_COLOR }, name: '回撤', showSymbol: false, type: 'line', xAxisIndex: 1, yAxisIndex: 1 },
    ],
    tooltip: { trigger: 'axis' },
    xAxis: [{ data: nav.map((r) => r.trade_date), type: 'category' },
            { data: nav.map((r) => r.trade_date), gridIndex: 1, show: false, type: 'category' }],
    yAxis: [{ scale: true, type: 'value' },
            { axisLabel: { formatter: '{value}%' }, gridIndex: 1, max: 0, min: (v: { min: number }) => Math.min(v.min, -1),
              type: 'value' }],
  });
}

function drawExposure() {
  const rows = [...(exposure.value?.industries ?? [])].reverse();
  if (!rows.length) return;
  renderExposure({
    grid: { bottom: 20, left: 90, right: 30, top: 30 },
    legend: { data: ['持仓', '目标'], top: 0 },
    series: [
      { data: rows.map((r) => (r.weight * 100).toFixed(2)), itemStyle: { color: UP_COLOR }, name: '持仓', type: 'bar' },
      { data: rows.map((r) => (r.target_weight * 100).toFixed(2)), itemStyle: { color: '#94a3b8' }, name: '目标', type: 'bar' },
    ],
    tooltip: { trigger: 'axis', valueFormatter: (v: any) => `${v}%` },
    xAxis: { axisLabel: { formatter: '{value}%' }, type: 'value' },
    yAxis: { data: rows.map((r) => r.name), type: 'category' },
  });
}

async function onTab(key: number | string) {
  tab.value = String(key);
  if (key === 'nav') {
    await nextTick();
    drawNav();
  } else if (key === 'exposure') {
    exposure.value = await accountExposureApi(accountId.value);
    await nextTick();
    drawExposure();
  }
}

async function submitFill() {
  if (!fill.symbol || !fill.qty || !fill.price) {
    message.warning('代码、数量、成交价必填');
    return;
  }
  await addFillApi(accountId.value, { commission: fill.commission === undefined ? undefined : String(fill.commission),
                                      price: String(fill.price), qty: fill.qty, side: fill.side, symbol: fill.symbol,
                                      trade_date: fill.trade_date.format('YYYY-MM-DD') });
  message.success('成交已记录');
  fill.open = false;
  await load();
}

async function preview() {
  entry.preview = await holdingsPreviewApi(accountId.value, { as_of: entry.as_of.format('YYYY-MM-DD'), cash: entry.cash,
                                                              text: entry.text });
}

async function commit() {
  if (!entry.preview) return;
  if (!entry.reason.trim()) {
    message.warning('请填写原因');
    return;
  }
  await holdingsCommitApi(accountId.value, entry.preview.batch_id, entry.reason);
  message.success('持仓已更新');
  entry.preview = undefined;
  entry.text = '';
  await load();
}

const rev = reactive<{ open: boolean; reason: string; row?: Record<string, any> }>({ open: false, reason: '' });

function reverse(row: Record<string, any>) {
  Object.assign(rev, { open: true, reason: '', row });
}

async function submitReverse() {
  if (!rev.row || !rev.reason.trim()) {
    message.warning('请填写冲正原因');
    return;
  }
  await reverseEventApi(rev.row.event_id, rev.reason);
  message.success('已冲正');
  rev.open = false;
  await load();
}

const holdingColumns = [
  { key: 'symbol', title: '代码', width: 110 },
  { dataIndex: 'qty', title: '数量', align: 'right' as const },
  { dataIndex: 'sellable', title: '可卖', align: 'right' as const },
  { key: 'close', title: '最新价', align: 'right' as const },
  { key: 'mv', title: '市值（元）', align: 'right' as const },
  { key: 'cost', title: '成本（元）', align: 'right' as const },
  { key: 'pnl', title: '浮动盈亏（元）', align: 'right' as const },
  { key: 'weight', title: '权重', align: 'right' as const },
];
const fillColumns = [
  { dataIndex: 'trade_date', title: '日期', width: 110 },
  { dataIndex: 'symbol', title: '代码' },
  { key: 'side', title: '方向' },
  { dataIndex: 'qty', title: '数量', align: 'right' as const },
  { key: 'price', title: '价格', align: 'right' as const },
  { key: 'fees', title: '费用（元）', align: 'right' as const },
  { dataIndex: 'source', title: '来源' },
  { dataIndex: 'created_by', title: '录入人' },
];
const eventColumns = [
  { dataIndex: 'trade_date', title: '日期', width: 110 },
  { key: 'kind', title: '类型', width: 100 },
  { dataIndex: 'symbol', title: '代码', width: 90 },
  { key: 'payload', title: '内容' },
  { dataIndex: 'reason', title: '原因' },
  { key: 'by', title: '记录', width: 170 },
  { key: 'actions', title: '', width: 80 },
];

function payloadText(row: Record<string, any>): string {
  const p = row.payload ?? {};
  switch (row.kind) {
    case 'adjustment': return `${p.old_quantity} → ${p.new_quantity} 股`;
    case 'cash':
    case 'deposit': return `${yuan(p.amount_fen)} 元`;
    case 'fill': return `${p.side === 'buy' ? '买' : '卖'} ${p.quantity} 股 @ ${price(p.price_fen)}`;
    case 'reversal': return `冲正 ${p.reverses}`;
    default: return JSON.stringify(p);
  }
}

onMounted(load);
</script>

<template>
  <Page :title="account ? `${account.name}（${account.account_id}）` : '账户'" :description="account?.note ?? ''">
    <template #extra>
      <Space>
        <Button @click="router.push('/accounts')">返回</Button>
        <Button @click="router.push({ path: '/decisions', query: { account_id: accountId } })">查看决策</Button>
        <Button v-if="canEdit" type="primary" @click="fill.open = true">回填成交</Button>
      </Space>
    </template>
    <template v-if="account">
      <Row :gutter="[16, 16]" class="mb-4">
        <Col :xs="12" :md="6"><Card size="small"><Statistic title="净值（元）" :value="yuan(account.nav_fen)" /></Card></Col>
        <Col :xs="12" :md="6"><Card size="small"><Statistic title="现金（元）" :value="yuan(account.cash_fen)" /></Card></Col>
        <Col :xs="12" :md="6"><Card size="small"><Statistic title="持仓市值（元）" :value="yuan(account.market_value_fen)" /></Card></Col>
        <Col :xs="12" :md="6">
          <Card size="small">
            <Statistic title="相对初始资金" :value="pct(account.initial_cash_fen ? account.nav_fen / account.initial_cash_fen - 1 : null, 2, true)"
                       :value-style="{ color: changeColor(account.nav_fen - account.initial_cash_fen) }" />
          </Card>
        </Col>
      </Row>
      <Alert v-if="account.mode === 'manual' && !account.holdings_confirmed_date" type="warning" show-icon class="mb-4"
             message="手工账户还没有确认过持仓；在决策前先到“持仓录入”页录入当前持仓和现金。" />
      <Card v-if="actions.length" size="small" class="mb-4" :title="`待确认的除权除息（${actions.length}）`">
        <Table :columns="actionColumns as any" :data-source="actions" row-key="event_id" size="small" :pagination="false">
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'symbol'">{{ record.symbol }} <span class="text-muted-foreground">{{ record.name }}</span></template>
            <template v-else-if="column.key === 'plan'">{{ record.plan ?? '—' }}</template>
            <template v-else-if="column.key === 'qty'">
              <InputNumber v-if="canEdit" v-model:value="record.qty" :min="0" :precision="0" size="small" style="width: 110px" />
              <span v-else>{{ record.qty }}</span>
            </template>
            <template v-else-if="column.key === 'cash'">
              <Input v-if="canEdit" v-model:value="record.cash" size="small" style="width: 110px" />
              <span v-else>{{ record.cash }}</span>
            </template>
            <template v-else-if="column.key === 'action'">
              <Button v-if="canEdit" size="small" type="primary" @click="applyAction(record as ActionRow)">确认调整</Button>
            </template>
          </template>
        </Table>
        <div class="text-muted-foreground mt-2 text-xs">
          调整后数量按每 10 股送股、转增的股数计算（不足 1 股的部分舍去）；现金分红是税前金额，红利税在卖出时由券商扣除。
          请以券商实际到账为准，可以先修改再确认。已在除权日之后重新录入过持仓的股票不会出现在这里。
        </div>
      </Card>
      <Card size="small">
        <Tabs :active-key="tab" @change="onTab">
          <Tabs.TabPane key="holdings" :tab="`持仓（${account.holdings.length}）`">
            <Table :columns="holdingColumns" :data-source="account.holdings" row-key="symbol" size="small" :pagination="false">
              <template #bodyCell="{ column, record }">
                <template v-if="column.key === 'symbol'">
                  <a @click="router.push(`/instruments/${record.symbol}`)">{{ record.symbol }}</a>
                  <div class="text-muted-foreground text-xs">{{ record.name }}</div>
                </template>
                <template v-else-if="column.key === 'close'">{{ record.close?.toFixed(2) ?? '—' }}</template>
                <template v-else-if="column.key === 'mv'">{{ yuan(record.market_value_fen) }}</template>
                <template v-else-if="column.key === 'cost'">{{ yuan(record.cost_fen) }}</template>
                <template v-else-if="column.key === 'pnl'">
                  <span :style="{ color: changeColor(record.pnl_fen) }">{{ yuan(record.pnl_fen) }}</span>
                </template>
                <template v-else-if="column.key === 'weight'">{{ pct(record.weight) }}</template>
              </template>
            </Table>
          </Tabs.TabPane>
          <Tabs.TabPane key="nav" tab="净值与回撤">
            <Empty v-if="!navData.length" description="还没有日终快照（每日决策运行后生成）" />
            <template v-else>
              <Space :size="32" class="mb-2">
                <Statistic title="最大回撤" :value="pct(drawdown.max)" :value-style="{ color: drawdown.max < 0 ? DOWN_COLOR : undefined }" />
                <Statistic title="当前回撤" :value="pct(drawdown.current)" />
                <Statistic title="快照天数" :value="navData.length" />
              </Space>
              <EchartsUI ref="navChart" height="420px" />
            </template>
          </Tabs.TabPane>
          <Tabs.TabPane key="exposure" tab="行业暴露">
            <Empty v-if="!exposure?.industries.length" description="没有持仓，也没有目标组合" />
            <template v-else>
              <Space :size="32" class="mb-2">
                <Statistic title="前十大权重" :value="pct(exposure.top10_weight)" />
                <Statistic title="有效持股数" :value="exposure.effective_names.toFixed(1)" />
                <Statistic title="现金" :value="pct(exposure.cash_weight)" />
                <Statistic title="对照的目标组合" :value="exposure.target_date ?? '—'" />
              </Space>
              <EchartsUI ref="exposureChart" :height="`${Math.max(260, exposure.industries.length * 26 + 60)}px`" />
              <div class="text-muted-foreground mt-1 text-xs">
                申万一级行业；持仓按最新收盘价计权，目标为最近一次调仓决策的目标权重。有效持股数 = 1 / Σ权重²。
              </div>
            </template>
          </Tabs.TabPane>
          <Tabs.TabPane key="fills" :tab="`成交（${fills.length}）`">
            <Table :columns="fillColumns" :data-source="fills" row-key="fill_id" size="small">
              <template #bodyCell="{ column, record }">
                <template v-if="column.key === 'side'">
                  <Tag :color="record.side === 'buy' ? 'red' : 'green'">{{ record.side === 'buy' ? '买入' : '卖出' }}</Tag>
                </template>
                <template v-else-if="column.key === 'price'">{{ price(record.price_fen) }}</template>
                <template v-else-if="column.key === 'fees'">
                  {{ yuan(record.fees_fen) }}<span v-if="record.fees_estimated" class="text-muted-foreground">（估算）</span>
                </template>
              </template>
            </Table>
          </Tabs.TabPane>
          <Tabs.TabPane v-if="canEdit" key="entry" tab="持仓录入">
            <p class="text-muted-foreground mb-3">
              从券商软件或 Excel 复制持仓粘贴到下面，每行“代码 数量 [成本价]”，分隔符可以是逗号、制表符或空格；
              未列出的股票视为已清仓。先预览差异，确认后生效。
            </p>
            <Form layout="vertical">
              <Row :gutter="16">
                <Col :span="8"><Form.Item label="持仓日期"><DatePicker v-model:value="entry.as_of" /></Form.Item></Col>
                <Col :span="8"><Form.Item label="现金（元）"><Input v-model:value="entry.cash" /></Form.Item></Col>
              </Row>
              <Form.Item label="持仓">
                <Input.TextArea v-model:value="entry.text" :rows="8" placeholder="600519 100 1500.00&#10;000001 2000 11.2" />
              </Form.Item>
              <Button type="primary" :disabled="!entry.text.trim()" @click="preview">预览差异</Button>
            </Form>
            <template v-if="entry.preview">
              <Alert v-if="entry.preview.errors.length" type="error" class="mt-4" show-icon message="有错误，修正后重新预览"
                     :description="entry.preview.errors.join('；')" />
              <Table class="mt-4" size="small" :pagination="false" row-key="symbol" :data-source="entry.preview.diff"
                     :columns="[{ dataIndex: 'symbol', title: '代码' }, { dataIndex: 'current', title: '当前' },
                                { dataIndex: 'new', title: '录入后' }, { dataIndex: 'change', title: '变化' }]" />
              <p class="mt-2">现金：{{ yuan(entry.preview.cash_before_fen) }} → {{ yuan(entry.preview.cash_after_fen) }} 元</p>
              <Space v-if="!entry.preview.errors.length" class="mt-2">
                <Input v-model:value="entry.reason" placeholder="原因，例如：3 月调仓后核对" style="width: 320px" />
                <Button type="primary" danger @click="commit">确认生效</Button>
              </Space>
            </template>
          </Tabs.TabPane>
          <Tabs.TabPane key="events" :tab="`账本流水（${events.length}）`">
            <Table :columns="eventColumns" :data-source="events" row-key="event_id" size="small">
              <template #bodyCell="{ column, record }">
                <template v-if="column.key === 'kind'">{{ EVENT_KIND[record.kind] ?? record.kind }}</template>
                <template v-else-if="column.key === 'payload'">{{ payloadText(record) }}</template>
                <template v-else-if="column.key === 'by'">{{ record.created_by }} · {{ dateTime(record.created_at) }}</template>
                <template v-else-if="column.key === 'actions'">
                  <Button v-if="canEdit && record.kind !== 'reversal' && record.kind !== 'deposit'" size="small" type="link"
                          @click="reverse(record)">冲正</Button>
                </template>
              </template>
            </Table>
          </Tabs.TabPane>
        </Tabs>
      </Card>
    </template>

    <Modal v-model:open="rev.open" title="冲正记录" ok-text="冲正" @ok="submitReverse">
      <p v-if="rev.row" class="mb-3">
        冲正 {{ EVENT_KIND[rev.row.kind] ?? rev.row.kind }} {{ rev.row.symbol ?? '' }}（{{ rev.row.trade_date }}）：
        {{ payloadText(rev.row) }}。冲正后账户按剩余记录重算；原记录保留，不会删除。
      </p>
      <Input.TextArea v-model:value="rev.reason" :rows="2" placeholder="原因，例如：录入错误" />
    </Modal>

    <Modal v-model:open="fill.open" title="回填成交" ok-text="记录" @ok="submitFill">
      <Form layout="vertical">
        <Form.Item label="成交日期" required><DatePicker v-model:value="fill.trade_date" /></Form.Item>
        <Form.Item label="代码" required><Input v-model:value="fill.symbol" :maxlength="6" placeholder="6 位代码" /></Form.Item>
        <Form.Item label="方向" required>
          <Radio.Group v-model:value="fill.side"><Radio value="buy">买入</Radio><Radio value="sell">卖出</Radio></Radio.Group>
        </Form.Item>
        <Form.Item label="数量（股）" required><InputNumber v-model:value="fill.qty" :min="1" :step="100" style="width: 100%" /></Form.Item>
        <Form.Item label="成交均价（元）" required><InputNumber v-model:value="fill.price" :min="0.01" :step="0.01" style="width: 100%" /></Form.Item>
        <Form.Item label="佣金（元）" extra="不填则按账户费率估算"><InputNumber v-model:value="fill.commission" :min="0" style="width: 100%" /></Form.Item>
      </Form>
    </Modal>
  </Page>
</template>
