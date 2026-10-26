<script setup lang="ts">
import type { EChartsOption } from 'echarts';

import type { Bar } from './market-api';

import { computed, onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';

import { showFailToast } from 'vant';

import { request } from '../../api';
import EChart from '../../components/EChart.vue';
import { color, DOWN, pct, UP } from '../../format';
import { indexBarsApi, yi } from './market-api';

// -- ETF 资金 contracts (app/etf.py, PC api/minerva.ts) --------------------------------------------------------
interface EtfAbnormal { abnormal: 'in' | 'out'; flow: number; flow_pct: number; strong: boolean; trade_date: string; z: number }
interface EtfGroupSummary {
  abnormal_in_250d: number;
  abnormal_out_250d: number;
  aum: null | number;
  chart_symbol: string;
  flow_1d: null | number;
  flow_20d: null | number;
  funds: number;
  id: string;
  last_abnormal: EtfAbnormal | null;
  name: string;
  strong_250d: number;
}
interface EtfOverview {
  as_of: string;
  groups: EtfGroupSummary[];
  rules: { baseline: number; min_share: number; strong_min_share: number; strong_z: number; z: number };
}
interface EtfDay {
  abnormal: 'in' | 'out' | null;
  cumulative: number;
  events: number;
  flow: null | number;
  flow_pct: null | number;
  funds: number;
  partial: boolean;
  strong: boolean;
  trade_date: string;
  z: null | number;
}
interface EtfFund {
  aum: null | number;
  event: null | string;
  flow: null | number;
  flow_20d: null | number;
  flow_5d: null | number;
  name: string;
  recent_events: { event: string; share_change: number; trade_date: string }[];
  symbol: string;
}
interface EtfHolders {
  classes: { id: string; name: string }[];
  funds: { holders: { holder: string; holder_class: null | string; pct: number; rank: number; shares: number }[]; name: string;
           national_pct: number; national_value: number; report_date: string; symbol: string }[];
  periods: { by_class: Record<string, number>; funds: number; national_share: null | number; national_value: number; report_date: string }[];
}
interface EtfWave {
  after_20: null | number;
  after_5: null | number;
  after_60: null | number;
  counter: boolean;
  index_window: null | number;
  trade_date: string;
  wave: 'creation' | 'redemption';
  wave_flow: number;
  wave_pct: number;
}
interface EtfWaves {
  base: Record<string, { mean: null | number; n: number; up: null | number }>;
  current: null | { counter: boolean; extreme: 'creation' | 'redemption' | null; high: null | number; index_window: null | number;
    low: null | number; rank: null | number; streak: number; trade_date: string; wave_flow: null | number; wave_pct: null | number };
  group: { chart_symbol: string };
  rules: { gap: number; min_history: number; sessions: number; tail: number };
  series: { high: null | number; low: null | number; rank: null | number; trade_date: string; wave_pct: null | number }[];
  waves: EtfWave[];
}

const router = useRouter();
const overview = ref<EtfOverview>();
const failed = ref('');
const groupId = ref('csi300');
const section = ref('flow');
const days = ref<EtfDay[]>([]);
const bars = ref<Bar[]>([]);
const indexNames = ref<Record<string, string>>({});
const loading = ref(false);
const selectedDay = ref<string>();
const funds = ref<EtfFund[]>([]);
const fundsDay = ref('');
const holders = ref<EtfHolders>();
const waves = ref<EtfWaves>();
const group = computed(() => overview.value?.groups.find((g) => g.id === groupId.value));
const indexName = computed(() => indexNames.value[group.value?.chart_symbol ?? ''] ?? group.value?.chart_symbol ?? '');

const WAVE_NAME = { creation: '大额申购波', redemption: '大额赎回波' } as const;
const streakText = (n: number) => (n > 0 ? `净申购 ${n} 天` : n < 0 ? `净赎回 ${-n} 天` : '—');
const waveLabel = (w: { counter: boolean; wave: 'creation' | 'redemption' }) =>
  w.counter ? (w.wave === 'redemption' ? '上涨中大额赎回' : '下跌中大额申购') : WAVE_NAME[w.wave];
const EVENT_LABEL: Record<string, string> = { gap: '数据间断', jump: '份额突变（无价格核对）', new: '首日', split: '份额折算' };
const CLASS_COLORS = ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4'];
const classColor = (id: null | string) => {
  const k = holders.value?.classes.findIndex((c) => c.id === id) ?? -1;
  return k < 0 ? undefined : CLASS_COLORS[k];
};
const className = (id: null | string) => holders.value?.classes.find((c) => c.id === id)?.name;
const latestPeriod = computed(() => holders.value?.periods.at(-1));
const openFunds = ref<string[]>([]);

/** Average index change after the past waves of one kind (and how often it was up). */
function waveSummary(kind: 'creation' | 'redemption', counter: boolean | null, h: number) {
  const rows = (waves.value?.waves ?? []).filter((w) => w.wave === kind && (counter === null || w.counter === counter))
    .map((w) => w[`after_${h}` as 'after_5']).filter((v): v is number => v !== null);
  if (!rows.length) return '—';
  const mean = rows.reduce((a, b) => a + b, 0) / rows.length;
  return `${pct(mean, 1, true)}（${rows.length} 次，${Math.round((rows.filter((v) => v > 0).length / rows.length) * 100)}% 上涨）`;
}

const closeByDate = computed(() => new Map(bars.value.map((b) => [b.trade_date, b])));
const barIndex = computed(() => new Map(bars.value.map((b, i) => [b.trade_date, i])));
function indexChange(date: string, ahead: number): null | number {
  const i = barIndex.value.get(date) ?? -1;
  if (i < 1 || i + ahead >= bars.value.length) return null;
  const base = ahead === 0 ? bars.value[i - 1]!.close : bars.value[i]!.close;
  return bars.value[i + ahead]!.close / base - 1;
}
const abnormalDays = computed(() => days.value.filter((d) => d.abnormal).map((d) => ({
  ...d, after20: indexChange(d.trade_date, 20), sameDay: indexChange(d.trade_date, 0),
})).reverse());

// -- loading ------------------------------------------------------------------------------------------------------
async function loadFunds(day?: string) {
  const result = await request<{ date: string; rows: EtfFund[] }>(`/etf/groups/${groupId.value}/funds${day ? `?day=${day}` : ''}`);
  funds.value = result.rows;
  fundsDay.value = result.date;
}

async function loadGroup() {
  const id = groupId.value;
  loading.value = true;
  try {
    const [series, h, w, b] = await Promise.all([
      request<{ rows: EtfDay[] }>(`/etf/groups/${id}/series`),
      request<EtfHolders>(`/etf/groups/${id}/holders`).catch(() => undefined),
      request<EtfWaves>(`/etf/groups/${id}/waves`).catch(() => undefined),
      group.value ? indexBarsApi(group.value.chart_symbol, 6000).catch(() => [] as Bar[]) : Promise.resolve([] as Bar[]),
      loadFunds(),
    ]);
    if (id !== groupId.value) return;
    days.value = series.rows;
    holders.value = h;
    waves.value = w;
    bars.value = b;
    selectedDay.value = undefined;
  } catch (error: any) {
    showFailToast(error.message);
  } finally {
    loading.value = false;
  }
}

function selectGroup(g: EtfGroupSummary) {
  if (g.id === groupId.value) return;
  groupId.value = g.id;
  void loadGroup();
}

async function pickDay(date: string) {
  selectedDay.value = date;
  try {
    await loadFunds(date);
  } catch (error: any) {
    showFailToast(error.message);
  }
}
async function latestFunds() {
  selectedDay.value = undefined;
  await loadFunds().catch((error: any) => showFailToast(error.message));
}

// -- charts --------------------------------------------------------------------------------------------------------
const small = { fontSize: 11, fontWeight: 'normal' as const };
const flowOption = computed<EChartsOption>(() => {
  const dates = days.value.map((d) => d.trade_date);
  const candles = dates.map((d) => {
    const b = closeByDate.value.get(d);
    return b ? [b.open, b.close, b.low, b.high] : '-';
  });
  // Inflows under the low, outflows over the high: round badges, larger with a white ring when strong.
  const marks = days.value.filter((d) => d.abnormal).map((d) => {
    const b = closeByDate.value.get(d.trade_date);
    const inflow = d.abnormal === 'in';
    const size = d.strong ? 18 : 13;
    return b ? { coord: [d.trade_date, inflow ? b.low : b.high], symbolOffset: [0, (inflow ? 1 : -1) * (size / 2 + 3)], symbolSize: size,
                 itemStyle: { borderColor: '#fff', borderWidth: d.strong ? 1.5 : 0, color: inflow ? UP : DOWN },
                 label: { color: '#fff', fontSize: d.strong ? 10 : 8, formatter: inflow ? '申' : '赎' }, value: d.abnormal } : null;
  }).filter(Boolean);
  const start = Math.max(0, dates.length - 120);
  return {
    animation: false,
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
    dataZoom: [{ startValue: start, type: 'inside', xAxisIndex: [0, 1, 2] }],
    grid: [{ height: '46%', left: 48, right: 8, top: 22 }, { height: '15%', left: 48, right: 8, top: '61%' },
           { height: '13%', left: 48, right: 8, top: '81%' }],
    series: [
      { data: candles, itemStyle: { borderColor: UP, borderColor0: DOWN, color: UP, color0: DOWN }, markPoint: { data: marks as any[], symbol: 'circle' },
        name: indexName.value, type: 'candlestick', xAxisIndex: 0, yAxisIndex: 0 },
      { barMaxWidth: 6, data: days.value.map((d) => ({ itemStyle: { color: (d.flow ?? 0) >= 0 ? UP : DOWN, opacity: d.partial ? 0.35 : 1 },
                                                        value: d.flow === null ? '-' : +(d.flow / 1e8).toFixed(2) })),
        name: '当日净申购', type: 'bar', xAxisIndex: 1, yAxisIndex: 1 },
      { color: '#2563eb', data: days.value.map((d) => +(d.cumulative / 1e8).toFixed(2)), lineStyle: { width: 1.5 }, name: '累计净申购',
        showSymbol: false, type: 'line', xAxisIndex: 2, yAxisIndex: 2 },
    ] as any[],
    title: [
      { left: 48, text: `${indexName.value} 日 K（申/赎 = 异常净申购/赎回）`, textStyle: small, top: 2 },
      { left: 48, text: '当日净申购（亿元）', textStyle: small, top: '57%' },
      { left: 48, text: '累计净申购（亿元）', textStyle: small, top: '77%' },
    ],
    tooltip: {
      confine: true,
      formatter: (params: any) => {
        const d = days.value[(params as any[])[0]?.dataIndex ?? 0];
        if (!d) return '';
        const b = closeByDate.value.get(d.trade_date);
        const lines = [`<b>${d.trade_date}</b>${d.partial ? '（深交所数据未出，不完整）' : ''}`];
        if (b) lines.push(`${indexName.value} 收盘 ${b.close.toFixed(2)}　${pct(indexChange(d.trade_date, 0), 2, true)}`);
        lines.push(`净申购 ${yi(d.flow)}（占规模 ${pct(d.flow_pct, 2, true)}）`, `z 值 ${d.z ?? '—'}　基金数 ${d.funds}`);
        if (d.abnormal) lines.push(`<b style="color:${d.abnormal === 'in' ? UP : DOWN}">${d.strong ? '强' : ''}${d.abnormal === 'in' ? '异常净申购' : '异常净赎回'}</b>`);
        if (d.events) lines.push(`${d.events} 只基金当天有份额折算或数据问题，未计入`);
        return lines.join('<br/>');
      },
      textStyle: { fontSize: 12 },
      trigger: 'axis',
    },
    xAxis: [0, 1, 2].map((k) => ({ axisLabel: { show: k === 2 }, axisTick: { show: k === 2 }, boundaryGap: true, data: dates, gridIndex: k, type: 'category' as const })),
    yAxis: [
      { gridIndex: 0, scale: true, splitLine: { lineStyle: { opacity: 0.3 } } },
      { gridIndex: 1, splitLine: { lineStyle: { opacity: 0.3 } }, splitNumber: 2 },
      { gridIndex: 2, scale: true, splitLine: { lineStyle: { opacity: 0.3 } }, splitNumber: 2 },
    ],
  };
});
function onFlowClick(event: any) {
  const d = days.value[event.dataIndex];
  if (d) void pickDay(d.trade_date);
}

const wavesOption = computed<EChartsOption>(() => {
  const w = waves.value;
  if (!w) return {};
  const muted = '#898781';
  const dates = w.series.map((r) => r.trade_date);
  const marks = w.waves.map((e) => {
    const inflow = e.wave === 'creation';
    return { coord: [e.trade_date, +(e.wave_pct * 100).toFixed(2)], symbolOffset: [0, inflow ? -10 : 10], symbolSize: e.counter ? 18 : 14,
             itemStyle: { borderColor: '#111', borderWidth: e.counter ? 1.5 : 0, color: inflow ? UP : DOWN },
             label: { color: '#fff', fontSize: 9, formatter: e.counter ? '逆' : inflow ? '申' : '赎' } };
  });
  return {
    animation: false,
    dataZoom: [{ startValue: Math.max(0, dates.length - 500), type: 'inside' }],
    grid: { bottom: 24, left: 40, right: 8, top: 28 },
    legend: { data: ['近 10 日净申购占规模', '历史 2% / 98% 分位'], textStyle: { fontSize: 11 }, top: 0 },
    series: [
      { color: '#2563eb', data: w.series.map((r) => (r.wave_pct === null ? '-' : +(r.wave_pct * 100).toFixed(2))), lineStyle: { width: 1.5 },
        markPoint: { data: marks as any[], symbol: 'circle' }, name: '近 10 日净申购占规模', showSymbol: false, type: 'line' },
      { color: muted, data: w.series.map((r) => (r.high === null ? '-' : +(r.high * 100).toFixed(2))), lineStyle: { type: 'dashed', width: 1 },
        name: '历史 2% / 98% 分位', showSymbol: false, type: 'line' },
      { color: muted, data: w.series.map((r) => (r.low === null ? '-' : +(r.low * 100).toFixed(2))), lineStyle: { type: 'dashed', width: 1 },
        name: '历史 2% / 98% 分位', showSymbol: false, type: 'line' },
    ] as any[],
    tooltip: {
      confine: true,
      formatter: (params: any) => {
        const r = w.series[(params as any[])[0]?.dataIndex ?? 0];
        if (!r) return '';
        return [`<b>${r.trade_date}</b>`, `近 10 日净申购占规模 ${pct(r.wave_pct, 2, true)}`,
                `历史分位 ${pct(r.rank, 0)}（区间 ${pct(r.low, 1, true)} ~ ${pct(r.high, 1, true)}）`].join('<br/>');
      },
      textStyle: { fontSize: 12 },
      trigger: 'axis',
    },
    xAxis: { boundaryGap: false, data: dates, type: 'category' },
    yAxis: { name: '%', splitLine: { lineStyle: { opacity: 0.3 } }, type: 'value' },
  };
});

const holdersOption = computed<EChartsOption>(() => {
  const h = holders.value;
  if (!h) return {};
  return {
    color: CLASS_COLORS,
    grid: { bottom: 24, left: 44, right: 8, top: 48 },
    legend: { data: h.classes.map((c) => c.name), textStyle: { fontSize: 11 }, top: 0 },
    series: h.classes.map((c) => ({
      barMaxWidth: 22, data: h.periods.map((p) => +((p.by_class[c.id] ?? 0) / 1e8).toFixed(1)), itemStyle: { borderColor: '#fff', borderWidth: 1 },
      name: c.name, stack: 'national', type: 'bar' as const,
    })),
    tooltip: {
      confine: true,
      formatter: (params: any) => {
        const list = params as any[];
        const p = h.periods[list[0]?.dataIndex ?? 0];
        if (!p) return '';
        const lines = [`<b>${p.report_date}</b>（${p.funds} 只基金披露）`];
        for (const item of list) if (item.value) lines.push(`${item.marker}${item.seriesName} ${item.value} 亿元`);
        lines.push(`合计 ${yi(p.national_value, false)}，占该组规模 ${pct(p.national_share, 1)}`);
        return lines.join('<br/>');
      },
      textStyle: { fontSize: 12 },
      trigger: 'axis',
    },
    xAxis: { data: h.periods.map((p) => p.report_date.slice(0, 7)), type: 'category' },
    yAxis: { name: '亿元', splitLine: { lineStyle: { opacity: 0.3 } }, type: 'value' },
  };
});

onMounted(async () => {
  try {
    const [result, indices] = await Promise.all([request<EtfOverview>('/etf/overview'),
                                                  request<{ name: string; symbol: string }[]>('/market/indices').catch(() => [])]);
    overview.value = result;
    indexNames.value = Object.fromEntries(indices.map((i) => [i.symbol, i.name]));
    if (!result.groups.some((g) => g.id === groupId.value) && result.groups[0]) groupId.value = result.groups[0].id;
    await loadGroup();
  } catch (error: any) {
    failed.value = error?.message ?? 'ETF 数据暂不可用';
  }
});
</script>

<template>
  <van-nav-bar title="ETF 资金" left-arrow @click-left="router.back()" />
  <van-notice-bar v-if="failed" color="#ed6a0c" background="#fffbe8" left-icon="warning-o" wrapable :text="failed" />
  <template v-if="overview">
    <p class="note" style="margin-bottom: 0">宽基 ETF 份额变化估算的每日净申购，数据截至 {{ overview.as_of }}（两所均已公布）</p>

    <!-- groups: one row of tiles, scrolled sideways -->
    <div class="tiles">
      <div v-for="g in overview.groups" :key="g.id" class="tile" :class="{ on: g.id === groupId }" @click="selectGroup(g)">
        <div class="tile-name">{{ g.name }}</div>
        <div class="muted">当日净申购（{{ overview.as_of.slice(5) }}）</div>
        <div class="tile-value" :style="{ color: color(g.flow_1d) }">{{ yi(g.flow_1d) }}</div>
        <div class="muted">近 20 日 <span :style="{ color: color(g.flow_20d) }">{{ yi(g.flow_20d) }}</span></div>
      </div>
    </div>

    <van-cell-group v-if="group" inset>
      <van-cell :title="group.name" :label="`${group.funds} 只 · 规模 ${yi(group.aum, false)}`" is-link value="大图看盘"
                @click="router.push(`/chart/${group.chart_symbol}`)" />
      <van-cell title="近一年异常日" :value="`申 ${group.abnormal_in_250d} · 赎 ${group.abnormal_out_250d} · 其中强 ${group.strong_250d}`" />
      <van-cell v-if="group.last_abnormal" title="最近异常"
                :value="`${group.last_abnormal.trade_date} ${group.last_abnormal.abnormal === 'in' ? '申购' : '赎回'} ${yi(group.last_abnormal.flow)}`" />
    </van-cell-group>

    <van-tabs v-model:active="section" sticky shrink style="margin-top: 12px">
      <van-tab name="flow" title="净申购">
        <div class="card">
          <div class="card-title">{{ group?.name }}：指数与净申购</div>
          <van-loading v-if="loading" style="text-align: center; padding: 40px 0" />
          <EChart v-else-if="days.length" :option="flowOption" :height="420" @click="onFlowClick" />
          <div v-if="selectedDay" class="picked">
            已选 {{ selectedDay }}：<a @click="section = 'funds'">看当天的成分 ETF</a>
          </div>
          <div class="muted" style="line-height: 18px; margin-top: 6px">
            双指缩放、左右拖动看更早；点柱子或 K 线选中一天。
            净申购 = 当日份额变化 × 当日 ETF 收盘价，按基金加总，是所有投资者一级市场申购减赎回的净额；二级市场买卖不改变份额，不在其中。
            异常日：当日净申购相对过去 {{ overview.rules.baseline }} 个交易日的稳健 z 值超过 ±{{ overview.rules.z }}，
            且金额超过前一日规模的 {{ pct(overview.rules.min_share, 1) }}；z 值超过 ±{{ overview.rules.strong_z }} 且超过规模
            {{ pct(overview.rules.strong_min_share, 0) }} 的为强异常。深交所晚一天公布，最新一天只含上交所的基金（浅色柱）。
          </div>
        </div>
      </van-tab>

      <van-tab name="waves" title="资金波段">
        <template v-if="waves?.current">
          <van-cell-group inset style="margin-top: 12px">
            <van-cell :title="`近 ${waves.rules.sessions} 日净申购`" :label="`截至 ${waves.current.trade_date} · 占规模 ${pct(waves.current.wave_pct, 2, true)}`">
              <template #value><span :style="{ color: color(waves.current.wave_flow) }">{{ yi(waves.current.wave_flow) }}</span></template>
            </van-cell>
            <van-cell title="在历史中的分位" :value="pct(waves.current.rank, 0)"
                      :label="`大额区间：低于 ${pct(waves.current.low, 1, true)} 或高于 ${pct(waves.current.high, 1, true)}`" />
            <van-cell title="连续" :value="streakText(waves.current.streak)" :label="`期间指数 ${pct(waves.current.index_window, 1, true)}`" />
            <van-cell title="状态">
              <template #value>
                <van-tag v-if="waves.current.extreme" :type="waves.current.extreme === 'creation' ? 'danger' : 'success'">
                  {{ waveLabel({ counter: waves.current.counter, wave: waves.current.extreme }) }}
                </van-tag>
                <van-tag v-else>在正常范围内</van-tag>
              </template>
            </van-cell>
          </van-cell-group>
          <div class="card">
            <EChart :option="wavesOption" :height="260" />
            <div class="muted" style="line-height: 18px">
              历次波段之后 60 日{{ indexNames[waves.group.chart_symbol] ?? waves.group.chart_symbol }}：赎回波 {{ waveSummary('redemption', null, 60) }}，其中上涨中赎回 {{ waveSummary('redemption', true, 60) }}；
              申购波 {{ waveSummary('creation', null, 60) }}，其中下跌中申购 {{ waveSummary('creation', true, 60) }}；
              任意一天 {{ pct(waves.base['60']?.mean, 1, true) }}（{{ pct(waves.base['60']?.up, 0) }} 上涨）。
            </div>
          </div>
          <div v-if="waves.waves.length" class="card">
            <div class="card-title">历次波段</div>
            <div class="table-wrap">
              <table class="table">
                <thead><tr><th>开始日</th><th>类型</th><th>10 日净申购</th><th>之后 20 日</th><th>之后 60 日</th></tr></thead>
                <tbody>
                  <tr v-for="w in [...waves.waves].reverse()" :key="w.trade_date">
                    <td>{{ w.trade_date }}</td>
                    <td><van-tag :type="w.wave === 'creation' ? 'danger' : 'success'" plain>{{ waveLabel(w) }}</van-tag></td>
                    <td>{{ yi(w.wave_flow) }}（{{ pct(w.wave_pct, 1, true) }}）</td>
                    <td :style="{ color: color(w.after_20) }">{{ pct(w.after_20, 1, true) }}</td>
                    <td :style="{ color: color(w.after_60) }">{{ pct(w.after_60, 1, true) }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
          <p class="note">
            资金波段：该组 ETF 近 {{ waves.rules.sessions }} 个交易日的净申购合计，除以期初规模。超过它自己历史上的
            {{ waves.rules.tail * 100 }}% / {{ 100 - waves.rules.tail * 100 }}% 分位（只用当天以前的数据，至少 {{ waves.rules.min_history }} 个交易日）就算一波，
            同方向两波之间至少隔 {{ waves.rules.gap }} 个交易日。逆势：上涨中出现大额赎回，或下跌中出现大额申购。历史上的波段很少，结果只作背景参考。
          </p>
        </template>
        <van-empty v-else description="没有资金波段数据" />
      </van-tab>

      <van-tab name="holders" title="机构持有">
        <template v-if="holders?.periods.length && latestPeriod">
          <div class="card">
            <div style="font-size: 13px; line-height: 20px">
              最新一期（{{ latestPeriod.report_date }}）：国家队合计持有 <b>{{ yi(latestPeriod.national_value, false) }}</b>，
              占该组规模 <b>{{ pct(latestPeriod.national_share, 1) }}</b>；{{ latestPeriod.funds }} 只基金有披露。
            </div>
            <EChart :option="holdersOption" :height="280" />
          </div>
          <van-collapse v-model="openFunds" style="margin: 0 12px; border-radius: 8px; overflow: hidden">
            <van-collapse-item v-for="f in holders.funds" :key="f.symbol" :name="f.symbol" :title="`${f.symbol} ${f.name}`"
                               :value="f.national_pct ? `${f.national_pct.toFixed(2)}% · ${yi(f.national_value, false)}` : '—'" :label="`报告期 ${f.report_date}`">
              <div v-for="h in f.holders" :key="h.rank + h.holder" class="holder-row">
                <span>
                  {{ h.rank }}. {{ h.holder }}
                  <van-tag v-if="h.holder_class" :color="classColor(h.holder_class)" style="margin-left: 4px">{{ className(h.holder_class) }}</van-tag>
                </span>
                <span>{{ (h.shares / 1e8).toFixed(2) }} 亿份 · {{ h.pct.toFixed(2) }}%</span>
              </div>
            </van-collapse-item>
          </van-collapse>
          <p class="note">
            持有市值 = 报告期末的持有份额 × 当日 ETF 收盘价，按基金加总。只统计前十名持有人（不含本基金的联接基金）。
            报告期末的数据在年报（约 3 月底）、中报（约 8 月底）公布后才能看到；2026 年起中报不再披露前十名持有人。
            国家队名单：{{ holders.classes.map((c) => c.name).join('、') }}。
          </p>
        </template>
        <van-empty v-else description="还没有采集到这组基金的定期报告" />
      </van-tab>

      <van-tab name="abnormal" :title="`异常日（${abnormalDays.length}）`">
        <div v-if="abnormalDays.length" class="card">
          <div class="table-wrap">
            <table class="table">
              <thead><tr><th>日期</th><th>方向</th><th>净申购</th><th>指数当日</th><th>之后 20 日</th></tr></thead>
              <tbody>
                <tr v-for="d in abnormalDays" :key="d.trade_date" :class="{ picked: d.trade_date === selectedDay }"
                    @click="pickDay(d.trade_date).then(() => (section = 'funds'))">
                  <td>{{ d.trade_date }}</td>
                  <td>
                    <van-tag :type="d.abnormal === 'in' ? 'danger' : 'success'" :plain="!d.strong">
                      {{ d.strong ? '强' : '' }}{{ d.abnormal === 'in' ? '净申购' : '净赎回' }}
                    </van-tag>
                  </td>
                  <td>{{ yi(d.flow) }}<div class="muted">{{ pct(d.flow_pct, 2, true) }} · z {{ d.z }}</div></td>
                  <td :style="{ color: color(d.sameDay) }">{{ pct(d.sameDay, 2, true) }}</td>
                  <td :style="{ color: color(d.after20) }">{{ pct(d.after20, 2, true) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <div class="muted" style="margin-top: 6px">点一行看当天的成分 ETF。</div>
        </div>
        <van-empty v-else description="没有异常日" />
      </van-tab>

      <van-tab name="funds" title="成分 ETF">
        <div class="card">
          <div class="card-title" style="display: flex; justify-content: space-between; align-items: center">
            <span>{{ fundsDay }}{{ selectedDay ? '' : '（最新）' }}</span>
            <van-button v-if="selectedDay" size="small" @click="latestFunds">回到最新</van-button>
          </div>
          <div v-if="funds.length" class="table-wrap">
            <table class="table">
              <thead><tr><th>基金</th><th>规模</th><th>当日</th><th>5 日</th><th>20 日</th></tr></thead>
              <tbody>
                <tr v-for="f in funds" :key="f.symbol">
                  <td class="fund">
                    {{ f.symbol }}<div class="muted fund-name">{{ f.name }}</div>
                    <van-tag v-if="f.event" plain>{{ EVENT_LABEL[f.event] ?? f.event }}</van-tag>
                    <template v-for="e in f.recent_events" :key="e.trade_date">
                      <van-tag v-if="e.trade_date !== fundsDay" plain style="margin-left: 2px">{{ e.trade_date.slice(2) }} {{ e.event === 'split' ? '折算' : '数据' }}</van-tag>
                    </template>
                  </td>
                  <td>{{ yi(f.aum, false) }}</td>
                  <td :style="{ color: color(f.flow) }">{{ yi(f.flow) }}</td>
                  <td :style="{ color: color(f.flow_5d) }">{{ yi(f.flow_5d) }}</td>
                  <td :style="{ color: color(f.flow_20d) }">{{ yi(f.flow_20d) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <van-empty v-else image-size="60" />
        </div>
      </van-tab>
    </van-tabs>
  </template>
  <van-skeleton v-else-if="!failed" :row="8" style="margin-top: 16px" />
</template>

<style scoped>
.tiles { display: flex; gap: 8px; padding: 12px; overflow-x: auto; -webkit-overflow-scrolling: touch; }
.tile { flex: none; width: 128px; padding: 10px; font-size: 12px; line-height: 18px; background: #fff; border: 1px solid transparent; border-radius: 8px; }
.tile.on { border-color: #1677ff; box-shadow: 0 0 0 1px #1677ff; }
.tile-name { font-size: 14px; font-weight: 600; }
.tile-value { font-size: 16px; font-weight: 600; line-height: 22px; }
.picked { margin-top: 6px; font-size: 13px; }
.picked a { color: #1677ff; }
tr.picked td { background: #e8f3ff; }
.holder-row { display: flex; justify-content: space-between; gap: 8px; font-size: 12px; line-height: 22px; }
.fund { max-width: 120px; white-space: normal; }
.fund-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
</style>
