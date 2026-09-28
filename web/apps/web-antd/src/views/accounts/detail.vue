<script lang="ts" setup>
import type { Dayjs } from 'dayjs';

import type { EchartsUIType } from '@vben/plugins/echarts';

import type { AccountDetail } from '#/api';

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
  accountApi, accountEventsApi, accountFillsApi, accountNavApi, addFillApi, holdingsCommitApi, holdingsPreviewApi,
  reverseEventApi,
} from '#/api';
import { changeColor, dateTime, EVENT_KIND, pct, price, UP_COLOR, yuan } from '#/utils/format';

const route = useRoute();
const router = useRouter();
const { hasAccessByCodes } = useAccess();
const accountId = computed(() => String(route.params.accountId));
const account = ref<AccountDetail>();
const events = ref<Record<string, any>[]>([]);
const fills = ref<Record<string, any>[]>([]);
const navChart = ref<EchartsUIType>();
const { renderEcharts } = useEcharts(navChart);
const tab = ref('holdings');
const canEdit = computed(() => hasAccessByCodes(['account:edit']) && account.value?.mode === 'manual');

const fill = reactive<{ commission?: number; open: boolean; price?: number; qty?: number; side: 'buy' | 'sell'; symbol: string;
                        trade_date: Dayjs }>({ open: false, side: 'buy', symbol: '', trade_date: dayjs() });
const entry = reactive<{ as_of: Dayjs; cash: string; preview?: Awaited<ReturnType<typeof holdingsPreviewApi>>; reason: string;
                         text: string }>({ as_of: dayjs(), cash: '', reason: '', text: '' });

async function load() {
  account.value = await accountApi(accountId.value);
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

function drawNav(nav = navData.value) {
  if (!nav.length) return;
  const base = nav[0]!.nav_fen;
  renderEcharts({
    grid: { bottom: 30, left: 60, right: 20, top: 30 },
    series: [{ areaStyle: { opacity: 0.08 }, data: nav.map((r) => (r.nav_fen / base).toFixed(4)), itemStyle: { color: UP_COLOR },
               name: '净值', showSymbol: nav.length < 40, smooth: true, type: 'line' }],
    tooltip: { trigger: 'axis' },
    xAxis: { data: nav.map((r) => r.trade_date), type: 'category' },
    yAxis: { scale: true, type: 'value' },
  });
}

async function onTab(key: number | string) {
  tab.value = String(key);
  if (key === 'nav') {
    await nextTick();
    drawNav();
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
          <Tabs.TabPane key="nav" tab="净值曲线">
            <Empty v-if="!navData.length" description="还没有日终快照（每日决策运行后生成）" />
            <EchartsUI v-else ref="navChart" height="360px" />
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
