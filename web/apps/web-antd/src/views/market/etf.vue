<script lang="ts" setup>
import type { EchartsUIType } from '@vben/plugins/echarts';

import type { Bar, EtfDay, EtfFund, EtfGroupSummary, EtfHolders, EtfOverview, EtfWave, EtfWaves } from '#/api';

import { computed, onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';
import { EchartsUI, useEcharts } from '@vben/plugins/echarts';
import { usePreferences } from '@vben/preferences';

import { Alert, Button, Card, Col, Empty, Row, Space, Spin, Table, Tag, Tooltip } from 'ant-design-vue';

import { etfFundsApi, etfHoldersApi, etfOverviewApi, etfSeriesApi, etfWavesApi, indexBarsApi, indicesApi } from '#/api';
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
const holders = ref<EtfHolders>();
const holdersChartRef = ref<EchartsUIType>();
const { renderEcharts: renderHolders } = useEcharts(holdersChartRef);
const { isDark } = usePreferences();
const waves = ref<EtfWaves>();
const wavesChartRef = ref<EchartsUIType>();
const { renderEcharts: renderWaves } = useEcharts(wavesChartRef);
const WAVE_NAME = { creation: '大额申购波', redemption: '大额赎回波' } as const;
const streakText = (n: number) => (n > 0 ? `净申购 ${n} 天` : n < 0 ? `净赎回 ${-n} 天` : '—');
const waveLabel = (w: { counter: boolean; wave: 'creation' | 'redemption' }) =>
  w.counter ? (w.wave === 'redemption' ? '上涨中大额赎回' : '下跌中大额申购') : WAVE_NAME[w.wave];
/** Average index change after the past waves of one kind (and how often it was up). */
function waveSummary(kind: 'creation' | 'redemption', counter: boolean | null, h: number) {
  const rows = (waves.value?.waves ?? []).filter((w) => w.wave === kind && (counter === null || w.counter === counter))
    .map((w) => w[`after_${h}` as 'after_5'] as null | number).filter((v): v is number => v !== null);
  if (!rows.length) return '—';
  const mean = rows.reduce((a, b) => a + b, 0) / rows.length;
  return `${pct(mean, 1, true)}（${rows.length} 次，${Math.round((rows.filter((v) => v > 0).length / rows.length) * 100)}% 上涨）`;
}
function drawWaves() {
  const w = waves.value;
  if (!w || !w.series.length) return;
  const muted = '#898781';
  const dates = w.series.map((r) => r.trade_date);
  // Waves pinned on the line: 申 above for creation, 赎 below for redemption; 逆 = against the index (ringed).
  const marks = w.waves.map((e: EtfWave) => {
    const inflow = e.wave === 'creation';
    return { coord: [e.trade_date, +(e.wave_pct * 100).toFixed(2)], symbolSize: e.counter ? 22 : 16,
             symbolOffset: [0, inflow ? -12 : 12],
             itemStyle: { color: inflow ? UP_COLOR : DOWN_COLOR, borderColor: isDark.value ? '#fff' : '#111', borderWidth: e.counter ? 1.5 : 0 },
             label: { color: '#fff', fontSize: 10, formatter: e.counter ? '逆' : inflow ? '申' : '赎' } };
  });
  const start = Math.max(0, dates.length - 750);
  void renderWaves({
    animation: false,
    dataZoom: [{ type: 'inside', startValue: start }, { type: 'slider', startValue: start, bottom: 4, height: 16 }],
    grid: { left: 56, right: 24, top: 28, bottom: 52 },
    legend: { data: ['近 10 日净申购占规模', '历史 2% / 98% 分位'], top: 0, textStyle: { fontSize: 12 } },
    tooltip: { trigger: 'axis', formatter: (params: any) => {
      const i = (params as any[])[0]?.dataIndex ?? 0;
      const r = w.series[i];
      if (!r) return '';
      return [`<b>${r.trade_date}</b>`, `近 10 日净申购占规模 ${pct(r.wave_pct, 2, true)}`,
              `历史分位 ${pct(r.rank, 0)}（区间 ${pct(r.low, 1, true)} ~ ${pct(r.high, 1, true)}）`].join('<br/>');
    } },
    xAxis: { type: 'category', data: dates, boundaryGap: false },
    yAxis: { type: 'value', name: '%', splitLine: { lineStyle: { opacity: 0.3 } } },
    series: [
      { type: 'line', name: '近 10 日净申购占规模', showSymbol: false, color: '#2563eb', lineStyle: { width: 1.5 },
        data: w.series.map((r) => (r.wave_pct === null ? '-' : +(r.wave_pct * 100).toFixed(2))),
        markPoint: { symbol: 'circle', data: marks as any[] } },
      { type: 'line', name: '历史 2% / 98% 分位', showSymbol: false, color: muted, lineStyle: { type: 'dashed', width: 1 },
        data: w.series.map((r) => (r.high === null ? '-' : +(r.high * 100).toFixed(2))) },
      { type: 'line', name: '历史 2% / 98% 分位', showSymbol: false, color: muted, lineStyle: { type: 'dashed', width: 1 },
        data: w.series.map((r) => (r.low === null ? '-' : +(r.low * 100).toFixed(2))) },
    ],
  } as any);
}
const waveColumns = [
  { dataIndex: 'trade_date', title: '开始日', width: 104 },
  { key: 'kind', title: '类型', width: 130 },
  { key: 'flow', title: '10 日净申购', align: 'right' },
  { key: 'window', title: '期间指数', align: 'right' },
  { key: 'after_5', title: '之后 5 日', align: 'right' },
  { key: 'after_20', title: '之后 20 日', align: 'right' },
  { key: 'after_60', title: '之后 60 日', align: 'right' },
] as const;
// Categorical slots 1-5 in fixed order (validated for both surfaces); the class keeps its colour.
const CLASS_COLORS = { dark: ['#3987e5', '#d95926', '#199e70', '#c98500', '#d55181'],
                       light: ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4'] };
const latestPeriod = computed(() => holders.value?.periods.at(-1));
const classColor = (id: null | string) => {
  const k = holders.value?.classes.findIndex((c) => c.id === id) ?? -1;
  return k < 0 ? undefined : CLASS_COLORS[isDark.value ? 'dark' : 'light'][k];
};
const className = (id: null | string) => holders.value?.classes.find((c) => c.id === id)?.name;
const holderColumns = [
  { key: 'fund', title: '基金' },
  { dataIndex: 'report_date', title: '报告期' },
  { key: 'national', title: '国家队合计', align: 'right' },
  { key: 'value', title: '持有市值', align: 'right' },
];
function drawHolders() {
  const h = holders.value;
  if (!h || !h.periods.length) return;
  const colors = CLASS_COLORS[isDark.value ? 'dark' : 'light'];
  const surface = isDark.value ? '#151517' : '#ffffff';
  void renderHolders({
    color: colors,
    grid: { bottom: 28, left: 56, right: 16, top: 36 },
    legend: { data: h.classes.map((c) => c.name), top: 0 },
    series: h.classes.map((c) => ({
      barMaxWidth: 26, data: h.periods.map((p) => +((p.by_class[c.id] ?? 0) / 1e8).toFixed(1)),
      emphasis: { focus: 'series' }, itemStyle: { borderColor: surface, borderWidth: 1 }, name: c.name, stack: 'national', type: 'bar',
    })),
    tooltip: {
      trigger: 'axis',
      formatter: (params: any) => {
        const list = params as any[];
        const p = h.periods[list[0]?.dataIndex ?? 0];
        if (!p) return '';
        const lines = [`<b>${p.report_date}</b>（${p.funds} 只基金披露）`];
        for (const item of list) if (item.value) lines.push(`${item.marker}${item.seriesName} ${item.value} 亿元`);
        lines.push(`合计 ${yi(p.national_value, false)}，占该组规模 ${pct(p.national_share, 1)}`);
        return lines.join('<br/>');
      },
    },
    xAxis: { data: h.periods.map((p) => p.report_date.slice(0, 7)), type: 'category' },
    yAxis: { name: '亿元', splitLine: { lineStyle: { opacity: 0.3 } }, type: 'value' },
  } as any);
}
const TILE_TIP = '当日净申购：该组各 ETF 当天的份额变化 × 收盘价之和，即所有投资者在一级市场申购减去赎回的净额（估算，单位元）；'
  + '负数为净赎回。二级市场买卖不改变份额，不计在内；也无法区分是谁申购。近 20 日：最近 20 个交易日的累计。规模：份额 × 收盘价。'
  + '近一年异常日：最近 250 个交易日里异常净申购、异常净赎回的天数，以及其中强异常的天数。';
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
    holders.value = await etfHoldersApi(groupId.value).catch(() => undefined);
    waves.value = await etfWavesApi(groupId.value).catch(() => undefined);
    bars.value = await indexBarsApi(series.group.chart_symbol, 6000);
    selectedDay.value = undefined;
    await loadFunds();
    await draw();
    drawHolders();
    drawWaves();
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
    const size = d.strong ? 22 : 16;
    return b ? { coord: [d.trade_date, inflow ? b.low : b.high], symbolOffset: [0, (inflow ? 1 : -1) * (size / 2 + 4)],
                 symbolSize: size, itemStyle: { borderColor: '#fff', borderWidth: d.strong ? 1.5 : 0, color: inflow ? UP_COLOR : DOWN_COLOR },
                 label: { color: '#fff', fontSize: d.strong ? 12 : 10, fontWeight: d.strong ? 'bold' : 'normal',
                          formatter: inflow ? '申' : '赎' }, value: d.abnormal } : null;
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
      { text: `${indexName} 日 K（标记：申 = 异常净申购，赎 = 异常净赎回；大而带白边的是强异常）`, left: 64, top: 4, textStyle: { fontSize: 12, fontWeight: 'normal' } },
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
        if (d.abnormal) lines.push(`<b style="color:${d.abnormal === 'in' ? UP_COLOR : DOWN_COLOR}">${d.strong ? '强' : ''}${d.abnormal === 'in' ? '异常净申购' : '异常净赎回'}</b>`);
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
          <Tooltip :title="TILE_TIP" placement="bottom" :mouse-enter-delay="0.6">
            <Card size="small" class="h-full cursor-pointer" :class="{ 'etf-selected': g.id === groupId }" @click="selectGroup(g)">
              <div class="font-semibold">{{ g.name }}</div>
              <div class="text-muted-foreground text-xs">{{ g.funds }} 只 · 规模 {{ yi(g.aum, false) }}</div>
              <div class="text-muted-foreground mt-1 text-xs">当日净申购（{{ overview.as_of.slice(5) }}）</div>
              <div class="text-lg leading-6" :style="{ color: changeColor(g.flow_1d) }">{{ yi(g.flow_1d) }}</div>
              <div class="text-xs">近 20 日 <span :style="{ color: changeColor(g.flow_20d) }">{{ yi(g.flow_20d) }}</span></div>
              <div class="text-muted-foreground text-xs">近一年异常日</div>
              <div class="text-xs">申 {{ g.abnormal_in_250d }} · 赎 {{ g.abnormal_out_250d }} · 其中强 {{ g.strong_250d }}</div>
            </Card>
          </Tooltip>
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
          净申购 = 当日份额变化 × 当日 ETF 收盘价，按基金加总，是所有投资者一级市场申购减赎回的净额；二级市场买卖不改变份额，不在其中。
          只算跟踪该指数的普通 ETF（增强型不计），份额按两个交易所每日公布的数据。
          份额折算（拆分、合并）、数据间断和基金首日不计入。
          异常日：当日净申购相对过去 {{ overview.rules.baseline }} 个交易日的稳健 z 值（中位数、四分位距）超过 ±{{ overview.rules.z }}，
          且金额超过前一日规模的 {{ pct(overview.rules.min_share, 1) }}；z 值超过 ±{{ overview.rules.strong_z }} 且超过规模 {{ pct(overview.rules.strong_min_share, 0) }} 的为强异常。
          深交所晚一天公布，最新一天只含上交所的基金（浅色柱）。
          这是按公开份额的估算，不能区分申购方是谁。点击图上某一天，下方显示各基金当天的情况。
        </div>
      </Card>

      <Card v-if="waves?.current" size="small" class="mb-4" :title="`资金波段：${group?.name}近 ${waves.rules.sessions} 个交易日的净申购`">
        <Row :gutter="[12, 12]" class="mb-2">
          <Col :xs="12" :md="6">
            <div class="text-muted-foreground text-xs">近 {{ waves.rules.sessions }} 日净申购（截至 {{ waves.current.trade_date }}）</div>
            <div class="text-lg" :style="{ color: changeColor(waves.current.wave_flow) }">{{ yi(waves.current.wave_flow) }}</div>
            <div class="text-xs">占规模 {{ pct(waves.current.wave_pct, 2, true) }}</div>
          </Col>
          <Col :xs="12" :md="6">
            <div class="text-muted-foreground text-xs">在历史中的分位</div>
            <div class="text-lg">{{ pct(waves.current.rank, 0) }}</div>
            <div class="text-xs">大额区间：低于 {{ pct(waves.current.low, 1, true) }} 或高于 {{ pct(waves.current.high, 1, true) }}</div>
          </Col>
          <Col :xs="12" :md="6">
            <div class="text-muted-foreground text-xs">连续</div>
            <div class="text-lg">{{ streakText(waves.current.streak) }}</div>
            <div class="text-xs">期间指数 {{ pct(waves.current.index_window, 1, true) }}</div>
          </Col>
          <Col :xs="12" :md="6">
            <div class="text-muted-foreground text-xs">状态</div>
            <Tag v-if="waves.current.extreme" :color="waves.current.extreme === 'creation' ? 'red' : 'green'" class="mt-1">
              {{ waveLabel({ counter: waves.current.counter, wave: waves.current.extreme }) }}
            </Tag>
            <Tag v-else class="mt-1">在正常范围内</Tag>
          </Col>
        </Row>
        <EchartsUI ref="wavesChartRef" height="300px" />
        <div class="text-muted-foreground mb-2 mt-1 text-xs leading-5">
          历次波段之后 60 日{{ indexNames[waves.group.chart_symbol] ?? waves.group.chart_symbol }}：赎回波 {{ waveSummary('redemption', null, 60) }}，其中上涨中赎回 {{ waveSummary('redemption', true, 60) }}；
          申购波 {{ waveSummary('creation', null, 60) }}，其中下跌中申购 {{ waveSummary('creation', true, 60) }}；
          任意一天 {{ pct(waves.base['60']?.mean, 1, true) }}（{{ pct(waves.base['60']?.up, 0) }} 上涨）。
        </div>
        <Table size="small" :pagination="waves.waves.length > 10 ? { pageSize: 10 } : false" row-key="trade_date"
               :columns="waveColumns as any" :data-source="[...waves.waves].reverse()">
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'kind'">
              <Tag :color="record.wave === 'creation' ? 'red' : 'green'">{{ waveLabel(record as EtfWave) }}</Tag>
            </template>
            <template v-else-if="column.key === 'flow'">{{ yi(record.wave_flow) }}（{{ pct(record.wave_pct, 1, true) }}）</template>
            <template v-else-if="column.key === 'window'">{{ pct(record.index_window, 1, true) }}</template>
            <template v-else-if="String(column.key).startsWith('after_')">
              <span :style="{ color: changeColor(record[String(column.key)]) }">{{ pct(record[String(column.key)], 1, true) }}</span>
            </template>
          </template>
        </Table>
        <div class="text-muted-foreground mt-2 text-xs leading-5">
          资金波段：该组 ETF 近 {{ waves.rules.sessions }} 个交易日的净申购合计，除以期初规模。超过它自己历史上的
          {{ waves.rules.tail * 100 }}% / {{ 100 - waves.rules.tail * 100 }}% 分位（只用当天以前的数据，至少 {{ waves.rules.min_history }} 个交易日）就算一波，
          同方向两波之间至少隔 {{ waves.rules.gap }} 个交易日。逆势：上涨中出现大额赎回，或下跌中出现大额申购。
          历史上的波段很少，结果只作背景参考，不能用来判断第二天的涨跌。
        </div>
      </Card>

      <Card v-if="holders" size="small" class="mb-4" :title="`机构持有：${group?.name}（年报、半年报披露的前十名持有人）`">
        <template v-if="holders.periods.length && latestPeriod">
          <div class="mb-2">
            最新一期（{{ latestPeriod.report_date }}）：国家队合计持有 <b>{{ yi(latestPeriod.national_value, false) }}</b>，
            占该组规模 <b>{{ pct(latestPeriod.national_share, 1) }}</b>；{{ latestPeriod.funds }} 只基金有披露。
          </div>
          <Row :gutter="[16, 16]">
            <Col :xs="24" :xl="12"><EchartsUI ref="holdersChartRef" height="320px" /></Col>
            <Col :xs="24" :xl="12">
              <Table :columns="holderColumns as any" :data-source="holders.funds" row-key="symbol" size="small"
                     :pagination="{ pageSize: 8, size: 'small' }">
                <template #bodyCell="{ column, record }">
                  <template v-if="column.key === 'fund'">{{ record.symbol }} {{ record.name }}</template>
                  <template v-else-if="column.key === 'national'">
                    {{ record.national_pct ? `${record.national_pct.toFixed(2)}%` : '—' }}
                  </template>
                  <template v-else-if="column.key === 'value'">{{ record.national_value ? yi(record.national_value, false) : '—' }}</template>
                </template>
                <template #expandedRowRender="{ record }">
                  <div v-for="h in record.holders" :key="h.rank + h.holder" class="flex justify-between text-xs leading-6">
                    <span>
                      {{ h.rank }}. {{ h.holder }}
                      <Tag v-if="h.holder_class" :color="classColor(h.holder_class)" class="ml-1">{{ className(h.holder_class) }}</Tag>
                    </span>
                    <span>{{ (h.shares / 1e8).toFixed(2) }} 亿份 · {{ h.pct.toFixed(2) }}%</span>
                  </div>
                </template>
              </Table>
            </Col>
          </Row>
          <div class="text-muted-foreground mt-2 text-xs leading-5">
            持有市值 = 报告期末的持有份额 × 当日 ETF 收盘价，按基金加总。只统计前十名持有人（不含本基金的联接基金），前十名以外的持有不在其中。
            报告期末的数据在年报（约 3 月底）、中报（约 8 月底）公布后才能看到；2026 年起中报不再披露前十名持有人，此后只有年报数据。
            国家队名单：{{ holders.classes.map((c) => c.name).join('、') }}（可在配置中调整）。
          </div>
        </template>
        <Empty v-else description="还没有采集到这组基金的定期报告" />
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
                  <Tag :color="record.abnormal === 'in' ? 'red' : 'green'" :bordered="!record.strong"
                       :style="record.strong ? 'font-weight: 600' : ''">{{ record.strong ? '强' : '' }}{{ record.abnormal === 'in' ? '净申购' : '净赎回' }}</Tag>
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
