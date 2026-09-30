<script lang="ts" setup>
import type { EchartsUIType } from '@vben/plugins/echarts';

import type { Bar, EtfDay, EtfFund, EtfGroupSummary, EtfOverview } from '#/api';

import { computed, onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';
import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import { Alert, Button, Card, Col, Empty, Row, Space, Spin, Table, Tag, Tooltip } from 'ant-design-vue';

import { etfFundsApi, etfOverviewApi, etfSeriesApi, indexBarsApi, indicesApi } from '#/api';
import { changeColor, DOWN_COLOR, pct, UP_COLOR } from '#/utils/format';

const router = useRouter();
const overview = ref<EtfOverview>();
const failed = ref('');
const groupId = ref('csi300');
const days = ref<EtfDay[]>([]);
const bars = ref<Bar[]>([]);
const indexNames = ref<Record<string, string>>({});
const loading = ref(false);
const selectedDay = ref<string>();
const funds = ref<EtfFund[]>([]);
const fundsDay = ref('');
const chartRef = ref<EchartsUIType>();
const { renderEcharts } = useEcharts(chartRef);

const group = computed(() => overview.value?.groups.find((g) => g.id === groupId.value));
const EVENT_LABEL: Record<string, string> = { gap: '数据间断', jump: '份额突变（无价格核对）', new: '首日', split: '份额折算' };

/** 亿元 with a sign, e.g. +12.35 亿. */
function yi(value?: null | number, signed = true): string {
  if (value === null || value === undefined) return '—';
  const text = (value / 1e8).toFixed(2);
  return `${signed && value > 0 ? '+' : ''}${text} 亿`;
}

const closeByDate = computed(() => new Map(bars.value.map((b) => [b.trade_date, b])));
function indexChange(date: string, ahead: number): null | number {
  const i = bars.value.findIndex((b) => b.trade_date === date);
  if (i < 1 || i + ahead >= bars.value.length) return null;
  const base = ahead === 0 ? bars.value[i - 1]!.close : bars.value[i]!.close;
  return bars.value[i + ahead]!.close / base - 1;
}

const abnormalDays = computed(() => days.value.filter((d) => d.abnormal).map((d) => ({
  ...d, after20: indexChange(d.trade_date, 20), sameDay: indexChange(d.trade_date, 0),
})).reverse());

async function loadGroup() {
  loading.value = true;
  try {
    const series = await etfSeriesApi(groupId.value);
    days.value = series.rows;
    bars.value = await indexBarsApi(series.group.chart_symbol, 6000);
    selectedDay.value = undefined;
    await loadFunds();
    await draw();
  } finally {
    loading.value = false;
  }
}

async function loadFunds(day?: string) {
  const result = await etfFundsApi(groupId.value, day);
  funds.value = result.rows;
  fundsDay.value = result.date;
}

function selectGroup(g: EtfGroupSummary) {
  if (g.id === groupId.value) return;
  groupId.value = g.id;
  void loadGroup();
}

let chart: any;
async function draw() {
  const dates = days.value.map((d) => d.trade_date);
  const candles = dates.map((d) => {
    const b = closeByDate.value.get(d);
    return b ? [b.open, b.close, b.low, b.high] : '-';
  });
  // Inflows under the low, outflows over the high: small round badges, like the chart workstation's marks.
  const marks = days.value.filter((d) => d.abnormal).map((d) => {
    const b = closeByDate.value.get(d.trade_date);
    const inflow = d.abnormal === 'in';
    return b ? { coord: [d.trade_date, inflow ? b.low : b.high], symbolOffset: [0, inflow ? 12 : -12],
                 itemStyle: { color: inflow ? UP_COLOR : DOWN_COLOR },
                 label: { color: '#fff', fontSize: 10, formatter: inflow ? '申' : '赎' }, value: d.abnormal } : null;
  }).filter(Boolean);
  const start = Math.max(0, dates.length - 500);
  const symbol = group.value?.chart_symbol ?? '';
  const indexName = indexNames.value[symbol] ?? symbol;
  chart = await renderEcharts({
    animation: false,
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
    dataZoom: [
      { type: 'inside', xAxisIndex: [0, 1, 2], startValue: start },
      { type: 'slider', xAxisIndex: [0, 1, 2], startValue: start, bottom: 4, height: 18 },
    ],
    grid: [
      { left: 64, right: 24, top: 28, height: '44%' },
      { left: 64, right: 24, top: '56%', height: '17%' },
      { left: 64, right: 24, top: '77%', height: '13%' },
    ],
    series: ([
      { type: 'candlestick', name: indexName, data: candles, xAxisIndex: 0, yAxisIndex: 0,
        itemStyle: { borderColor: UP_COLOR, borderColor0: DOWN_COLOR, color: UP_COLOR, color0: DOWN_COLOR },
        markPoint: { data: marks as any[], symbol: 'circle', symbolSize: 16 } },
      { type: 'bar', name: '当日净申购', xAxisIndex: 1, yAxisIndex: 1, barMaxWidth: 6,
        data: days.value.map((d) => ({ value: d.flow === null ? '-' : +(d.flow / 1e8).toFixed(2),
                                       itemStyle: { color: (d.flow ?? 0) >= 0 ? UP_COLOR : DOWN_COLOR,
                                                    opacity: d.partial ? 0.35 : 1 } })) },
      { type: 'line', name: '累计净申购', xAxisIndex: 2, yAxisIndex: 2, showSymbol: false,
        lineStyle: { width: 2 }, color: '#2563eb', data: days.value.map((d) => +(d.cumulative / 1e8).toFixed(2)) },
    ] as any[]),
    title: [
      { text: `${indexName} 日 K（标记：申 = 异常净申购，赎 = 异常净赎回）`, left: 64, top: 4, textStyle: { fontSize: 12, fontWeight: 'normal' } },
      { text: '当日净申购（亿元）', left: 64, top: '53%', textStyle: { fontSize: 12, fontWeight: 'normal' } },
      { text: '累计净申购（亿元，自所示起点）', left: 64, top: '74%', textStyle: { fontSize: 12, fontWeight: 'normal' } },
    ],
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross' },
      formatter: (params: any) => {
        const i = (params as any[])[0]?.dataIndex ?? 0;
        const d = days.value[i];
        if (!d) return '';
        const b = closeByDate.value.get(d.trade_date);
        const change = indexChange(d.trade_date, 0);
        const lines = [`<b>${d.trade_date}</b>${d.partial ? '（深交所数据未出，不完整）' : ''}`];
        if (b) lines.push(`${indexName} 收盘 ${b.close.toFixed(2)}　${pct(change, 2, true)}`);
        lines.push(`净申购 ${yi(d.flow)}（占规模 ${pct(d.flow_pct, 2, true)}）`, `z 值 ${d.z ?? '—'}　基金数 ${d.funds}`);
        if (d.abnormal) lines.push(`<b style="color:${d.abnormal === 'in' ? UP_COLOR : DOWN_COLOR}">${d.abnormal === 'in' ? '异常净申购' : '异常净赎回'}</b>`);
        if (d.events) lines.push(`${d.events} 只基金当天有份额折算或数据问题，未计入`);
        return lines.join('<br/>');
      },
    },
    xAxis: [0, 1, 2].map((k) => ({ type: 'category', data: dates, gridIndex: k, boundaryGap: true,
                                     axisLabel: { show: k === 2 }, axisTick: { show: k === 2 } })),
    yAxis: [
      { scale: true, gridIndex: 0, splitLine: { lineStyle: { opacity: 0.3 } } },
      { gridIndex: 1, splitNumber: 2, splitLine: { lineStyle: { opacity: 0.3 } } },
      { gridIndex: 2, splitNumber: 2, scale: true, splitLine: { lineStyle: { opacity: 0.3 } } },
    ],
  });
  chart?.off('click');
  chart?.on('click', (event: any) => {
    const d = days.value[event.dataIndex];
    if (d) void pickDay(d.trade_date, false);
  });
}

async function pickDay(date: string, zoom = true) {
  selectedDay.value = date;
  await loadFunds(date);
  if (zoom && chart) {
    const i = days.value.findIndex((d) => d.trade_date === date);
    chart.dispatchAction({ type: 'dataZoom', startValue: Math.max(0, i - 60), endValue: Math.min(days.value.length - 1, i + 60) });
  }
}

const abnormalColumns = [
  { dataIndex: 'trade_date', title: '日期', width: 104 },
  { key: 'direction', title: '方向', width: 90 },
  { key: 'flow', title: '净申购', align: 'right' },
  { key: 'share', title: '占规模', align: 'right' },
  { dataIndex: 'z', title: 'z 值', align: 'right' },
  { key: 'sameDay', title: '指数当日', align: 'right' },
  { key: 'after20', title: '之后 20 日', align: 'right' },
] as const;
const fundColumns = [
  { key: 'fund', title: '基金' },
  { key: 'aum', title: '规模', align: 'right' },
  { key: 'flow', title: '当日', align: 'right' },
  { key: 'flow_5d', title: '5 日', align: 'right' },
  { key: 'flow_20d', title: '20 日', align: 'right' },
  { key: 'flow_60d', title: '60 日', align: 'right' },
  { key: 'event', title: '备注' },
] as const;

onMounted(async () => {
  try {
    const [result, indices] = await Promise.all([etfOverviewApi(), indicesApi()]);
    overview.value = result;
    indexNames.value = Object.fromEntries(indices.map((i) => [i.symbol, i.name]));
    await loadGroup();
  } catch (error: any) {
    failed.value = error?.response?.data?.detail?.message ?? 'ETF 数据暂不可用';
  }
});
</script>

<template>
  <Page title="ETF 资金" :description="overview ? `宽基 ETF 份额变化估算的每日净申购，数据截至 ${overview.as_of}（两所均已公布）` : ''">
    <Alert v-if="failed" type="warning" show-icon :message="failed" />
    <template v-if="overview">
      <Row :gutter="[12, 12]" class="mb-4">
        <Col v-for="g in overview.groups" :key="g.id" :xs="12" :md="6" :xl="3">
          <Card size="small" class="h-full cursor-pointer" :class="{ 'etf-selected': g.id === groupId }" @click="selectGroup(g)">
            <div class="font-semibold">{{ g.name }}</div>
            <div class="text-muted-foreground text-xs">{{ g.funds }} 只 · 规模 {{ yi(g.aum, false) }}</div>
            <div class="mt-1 text-lg" :style="{ color: changeColor(g.flow_1d) }">{{ yi(g.flow_1d) }}</div>
            <div class="text-xs">20 日 <span :style="{ color: changeColor(g.flow_20d) }">{{ yi(g.flow_20d) }}</span></div>
            <div class="text-muted-foreground text-xs">近一年异常 申 {{ g.abnormal_in_250d }} · 赎 {{ g.abnormal_out_250d }}</div>
          </Card>
        </Col>
      </Row>

      <Card size="small" class="mb-4">
        <template #title>{{ group?.name }}：指数与净申购</template>
        <template #extra>
          <Space>
            <span v-if="group?.last_abnormal" class="text-xs">
              最近异常：{{ group.last_abnormal.trade_date }} {{ group.last_abnormal.abnormal === 'in' ? '申购' : '赎回' }} {{ yi(group.last_abnormal.flow) }}
            </span>
            <Button size="small" type="primary" ghost @click="router.push({ path: '/chart', query: { symbol: group?.chart_symbol } })">大图看盘</Button>
          </Space>
        </template>
        <Spin :spinning="loading">
          <EchartsUI ref="chartRef" height="600px" />
        </Spin>
        <div class="text-muted-foreground mt-2 text-xs leading-5">
          净申购 = 当日份额变化 × 当日 ETF 收盘价，按基金加总；只算跟踪该指数的普通 ETF（增强型不计），份额按两个交易所每日公布的数据。
          份额折算（拆分、合并）、数据间断和基金首日不计入。
          异常日：当日净申购相对过去 {{ overview.rules.baseline }} 个交易日的稳健 z 值（中位数、四分位距）超过 ±{{ overview.rules.z }}，
          且金额超过前一日规模的 {{ pct(overview.rules.min_share, 1) }}。深交所晚一天公布，最新一天只含上交所的基金（浅色柱）。
          这是按公开份额的估算，不能区分申购方是谁。点击图上某一天，下方显示各基金当天的情况。
        </div>
      </Card>

      <Row :gutter="[16, 16]">
        <Col :xs="24" :xl="11">
          <Card size="small" :title="`异常申购 / 赎回日（${abnormalDays.length}）`">
            <Table :columns="abnormalColumns as any" :data-source="abnormalDays" row-key="trade_date" size="small"
                   :pagination="{ pageSize: 12, size: 'small' }"
                   :custom-row="(row: any) => ({ onClick: () => pickDay(row.trade_date), style: 'cursor: pointer' })"
                   :row-class-name="(row: any) => (row.trade_date === selectedDay ? 'etf-row-selected' : '')">
              <template #bodyCell="{ column, record }">
                <template v-if="column.key === 'direction'">
                  <Tag :color="record.abnormal === 'in' ? 'red' : 'green'">{{ record.abnormal === 'in' ? '净申购' : '净赎回' }}</Tag>
                </template>
                <template v-else-if="column.key === 'flow'">{{ yi(record.flow) }}</template>
                <template v-else-if="column.key === 'share'">{{ pct(record.flow_pct, 2, true) }}</template>
                <template v-else-if="column.key === 'sameDay'">
                  <span :style="{ color: changeColor(record.sameDay) }">{{ pct(record.sameDay, 2, true) }}</span>
                </template>
                <template v-else-if="column.key === 'after20'">
                  <span :style="{ color: changeColor(record.after20) }">{{ pct(record.after20, 2, true) }}</span>
                </template>
              </template>
            </Table>
          </Card>
        </Col>
        <Col :xs="24" :xl="13">
          <Card size="small" :title="`成分 ETF：${fundsDay}${selectedDay ? '' : '（最新）'}`">
            <template #extra>
              <Button v-if="selectedDay" size="small" @click="selectedDay = undefined; loadFunds()">回到最新</Button>
            </template>
            <Table v-if="funds.length" :columns="fundColumns as any" :data-source="funds" row-key="symbol" size="small"
                   :pagination="{ pageSize: 12, size: 'small' }">
              <template #bodyCell="{ column, record }">
                <template v-if="column.key === 'fund'">{{ record.symbol }} {{ record.name }}</template>
                <template v-else-if="column.key === 'aum'">{{ yi(record.aum, false) }}</template>
                <template v-else-if="['flow', 'flow_5d', 'flow_20d', 'flow_60d'].includes(String(column.key))">
                  <span :style="{ color: changeColor(record[column.key as string]) }">{{ yi(record[column.key as string]) }}</span>
                </template>
                <template v-else-if="column.key === 'event'">
                  <Tag v-if="record.event">{{ EVENT_LABEL[record.event] ?? record.event }}</Tag>
                  <Tooltip v-for="e in record.recent_events" :key="e.trade_date" :title="`${e.trade_date} ${EVENT_LABEL[e.event] ?? e.event}`">
                    <Tag v-if="e.trade_date !== fundsDay" color="default">{{ e.trade_date.slice(2) }} {{ e.event === 'split' ? '折算' : '数据' }}</Tag>
                  </Tooltip>
                </template>
              </template>
            </Table>
            <Empty v-else />
          </Card>
        </Col>
      </Row>
    </template>
  </Page>
</template>

<style scoped>
.etf-selected {
  border-color: hsl(var(--primary));
  box-shadow: 0 0 0 1px hsl(var(--primary));
}

:deep(.etf-row-selected) td {
  background: hsl(var(--accent));
}
</style>
