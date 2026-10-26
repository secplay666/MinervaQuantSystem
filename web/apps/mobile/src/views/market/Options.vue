<script setup lang="ts">
import type { EChartsOption } from 'echarts';

import type { OptionChain, OptionChainRow, OptionContract, OptionMinute, OptionSide, OptionUnderlying } from './info-api';

import { computed, onMounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import dayjs from 'dayjs';
import { showDialog, showFailToast } from 'vant';

import { qs, request } from '../../api';
import EChart from '../../components/EChart.vue';
import { color, DOWN, UP } from '../../format';
import { INK, missing, MUTED, SERIES, signedPercent as signedPct, useQueryPopup } from './info-api';

const route = useRoute();
const router = useRouter();
const underlyings = ref<OptionUnderlying[]>([]);
const underlying = ref<string>();
const month = ref<string>();
const day = ref<string>();
const chain = ref<OptionChain>();
const board = ref<'call' | 'put' | 't'>('t');
const loading = ref(false);
const failed = ref('');
const picking = ref<'month' | 'underlying'>('underlying'); // what the picker chooses (kept while it closes)
const pickerShow = ref(false);
const calendar = ref(false);
const popup = useQueryPopup('contract');
const highlight = ref<string>(); // the last opened contract, bold on the board
const detail = ref<OptionContract>();
const detailLoading = ref(false);
const mode = ref<'daily' | 'minute'>('daily');
const minuteDay = ref<string>();
const minuteSheet = ref(false);
const minutes = ref<{ prev_close: null | number; rows: OptionMinute[] }>();

const ITM = '#f4f1e4';
const ADJUSTED = '分红后调整过的合约：行权价和合约单位已变，代码末尾为 A、B 等';
const current = computed(() => underlyings.value.find((u) => u.underlying === underlying.value));
const cffex = computed(() => current.value?.exchange === 'CFFEX');
const digits = computed(() => (cffex.value ? 1 : 4));
const spot = computed(() => chain.value?.underlying.close ?? null);
const selected = computed(() => popup.value.value);

const num = (v?: null | number, d = digits.value) => (missing(v) ? '—' : v.toFixed(d));
const ivText = (v?: null | number) => (missing(v) ? '—' : `${(v * 100).toFixed(1)}%`);
const count = (v?: null | number) => (missing(v) ? '—' : Math.round(v).toLocaleString('zh-CN'));
/** Calls are in the money below the underlying's close, puts above it. */
const inMoney = (row: OptionChainRow, kind: 'call' | 'put') =>
  spot.value !== null && !row.adjusted && (kind === 'call' ? row.strike < spot.value : row.strike > spot.value);

const monthLabel = (m: OptionChain['months'][number]) => `${m.expiry_month.replace('-', ' 年 ')} 月（${m.expiry_date} 到期）`;
const monthText = computed(() => {
  const m = chain.value?.months.find((x) => x.expiry_month === month.value);
  return m ? monthLabel(m) : month.value ?? '—';
});
const pickerColumns = computed(() => {
  if (picking.value === 'underlying') return underlyings.value.map((u) => ({ text: `${u.name}（${u.underlying}）`, value: u.underlying }));
  const months = chain.value?.months ?? [];
  return [...months.filter((m) => !m.expired).map((m) => ({ text: monthLabel(m), value: m.expiry_month })),
          ...months.filter((m) => m.expired).reverse().map((m) => ({ text: `${monthLabel(m)} 已到期`, value: m.expiry_month }))];
});
const pickerValue = computed(() => [(picking.value === 'underlying' ? underlying.value : month.value) ?? '']);

const sessions = computed(() => new Set(chain.value?.sessions ?? []));
const toDate = (s: string) => dayjs(s).toDate();
const minDate = computed(() => toDate(chain.value?.sessions[0] ?? dayjs().format('YYYY-MM-DD')));
const maxDate = computed(() => toDate(chain.value?.sessions.at(-1) ?? dayjs().format('YYYY-MM-DD')));
const formatDay = (d: any) => (sessions.value.has(dayjs(d.date).format('YYYY-MM-DD')) ? d : { ...d, type: 'disabled' });

function syncQuery() {
  void router.replace({ query: { ...route.query, month: month.value, underlying: underlying.value } });
}

async function loadChain() {
  if (!underlying.value) return;
  loading.value = true;
  failed.value = '';
  try {
    const result = await request<OptionChain>(`/options/chain${qs({ day: day.value, month: month.value, underlying: underlying.value })}`);
    chain.value = result;
    month.value = result.month;
    day.value = result.day;
    syncQuery();
  } catch (error: any) {
    failed.value = error.message;
    chain.value = undefined;
  } finally {
    loading.value = false;
  }
}

function pick({ selectedValues }: { selectedValues: (number | string)[] }) {
  const value = String(selectedValues[0]);
  if (picking.value === 'underlying') {
    underlying.value = value;
    month.value = undefined;
  } else {
    month.value = value;
  }
  pickerShow.value = false;
  day.value = undefined;
  void loadChain();
}

function pickDay(date: Date) {
  calendar.value = false;
  day.value = dayjs(date).format('YYYY-MM-DD');
  void loadChain();
}

function stepSession(step: -1 | 1) {
  const list = chain.value?.sessions ?? [];
  const next = list[list.indexOf(day.value ?? '') + step];
  if (next) {
    day.value = next;
    void loadChain();
  }
}

function open(item: null | OptionSide) {
  if (item) popup.open(item.contract);
}

/* ---- contract detail ---- */
const termsLine = computed(() => {
  const d = detail.value;
  if (!d) return [];
  const t = d.terms;
  const first = d.bars[0]?.trade_date;
  const last = d.bars.at(-1)?.trade_date;
  return [
    `${t.kind === 'call' ? '认购' : '认沽'} · 行权价 ${t.strike} · 到期日 ${t.expiry_date}`,
    t.multiplier ? `合约单位 ${t.multiplier.toLocaleString('zh-CN')}${t.exchange === 'SSE' ? ' 份' : ' 元/点'}` : '合约单位已调整',
    first ? `交易 ${first} ~ ${last}（${d.bars.length} 个交易日）` : '没有行情',
  ];
});

watch(selected, async (contract) => {
  if (!contract) return;
  highlight.value = contract;
  if (detail.value?.terms.contract === contract) return;
  detailLoading.value = true;
  detail.value = undefined;
  minutes.value = undefined;
  try {
    const result = await request<OptionContract>(`/options/contracts/${encodeURIComponent(contract)}`);
    if (selected.value !== contract) return;
    detail.value = result;
    minuteDay.value = result.minute_days.at(-1);
    if (!minuteDay.value) mode.value = 'daily';
  } catch (error: any) {
    showFailToast(error.message);
  } finally {
    detailLoading.value = false;
  }
}, { immediate: true });

watch([mode, minuteDay, detail], async () => {
  const contract = selected.value;
  if (mode.value !== 'minute' || !contract || !minuteDay.value || !detail.value) return;
  try {
    minutes.value = await request(`/options/contracts/${encodeURIComponent(contract)}/minutes${qs({ day: minuteDay.value })}`);
  } catch (error: any) {
    showFailToast(error.message);
  }
});

const axisCommon = { axisLabel: { color: MUTED, fontSize: 10 }, axisLine: { lineStyle: { color: MUTED } } };
const splitLine = { lineStyle: { opacity: 0.25 } };
const paneTitle = (text: string, top: number) => ({ left: 4, text, textStyle: { color: INK, fontSize: 11, fontWeight: 'normal' as const }, top });
const grid = (top: number, height: number) => ({ height, left: 44, right: 12, top });

const dailyOption = computed<EChartsOption>(() => {
  const d = detail.value;
  if (!d || d.bars.length === 0) return {};
  const sse = d.terms.exchange === 'SSE';
  const dates = d.bars.map((b) => b.trade_date);
  const candles = d.bars.map((b) => [b.open ?? b.close, b.close, b.low ?? b.close, b.high ?? b.close]);
  const up = d.bars.map((b, i) => (b.close ?? 0) >= (i > 0 ? d.bars[i - 1]!.close ?? 0 : b.open ?? 0));
  const fmt = (v?: null | number, n = digits.value) => (missing(v) ? '—' : v.toFixed(n));
  const name = d.terms.kind === 'call' ? '认购' : '认沽';
  return {
    animation: false,
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
    dataZoom: [{ bottom: 4, height: 18, startValue: Math.max(0, dates.length - 120), type: 'slider', xAxisIndex: [0, 1, 2, 3] }],
    grid: [grid(26, 160), grid(212, 40), grid(278, 66), grid(368, 48)],
    legend: { data: [name, '内在价值'], left: 4, textStyle: { color: INK, fontSize: 11 }, top: 0 },
    series: [
      { data: candles, itemStyle: { borderColor: UP, borderColor0: DOWN, color: UP, color0: DOWN }, name, type: 'candlestick', xAxisIndex: 0, yAxisIndex: 0 },
      { color: INK, data: d.bars.map((b) => b.intrinsic ?? '-'), lineStyle: { type: 'dashed', width: 1 }, name: '内在价值',
        showSymbol: false, type: 'line', xAxisIndex: 0, yAxisIndex: 0 },
      { barMaxWidth: 6, name: '成交量', type: 'bar', xAxisIndex: 1, yAxisIndex: 1,
        data: d.bars.map((b, i) => ({ itemStyle: { color: up[i] ? UP : DOWN, opacity: 0.7 }, value: b.volume ?? 0 })) },
      { color: SERIES[0], data: d.bars.map((b) => b.underlying_close ?? '-'), lineStyle: { width: 1.5 }, name: d.terms.underlying_name,
        showSymbol: false, type: 'line', xAxisIndex: 2, yAxisIndex: 2,
        markLine: { data: [{ yAxis: d.terms.strike }], label: { color: INK, fontSize: 10, formatter: `${d.terms.strike}` },
                    lineStyle: { color: INK, type: 'dashed' }, silent: true, symbol: 'none' } },
      sse
        ? { color: SERIES[1], connectNulls: false, data: d.bars.map((b) => b.iv ?? '-'), lineStyle: { width: 1.5 }, name: '隐含波动率',
            showSymbol: false, type: 'line', xAxisIndex: 3, yAxisIndex: 3 }
        : { barMaxWidth: 6, color: SERIES[1], data: d.bars.map((b) => b.open_interest ?? 0), name: '持仓量', type: 'bar', xAxisIndex: 3, yAxisIndex: 3 },
    ],
    title: [paneTitle('成交量', 194), paneTitle(`${d.terms.underlying_name} 收盘（虚线 = 行权价）`, 260), paneTitle(sse ? '隐含波动率' : '持仓量', 350)],
    tooltip: {
      confine: true,
      formatter: (params: any) => {
        const i = (params as any[])[0]?.dataIndex ?? 0;
        const b = d.bars[i];
        if (!b) return '';
        const prev = i > 0 ? d.bars[i - 1]!.close : null;
        const change = !missing(prev) && !missing(b.close) && prev ? (b.close / prev - 1) * 100 : null;
        return [`<b>${b.trade_date}</b>`,
                `开 ${fmt(b.open)} 高 ${fmt(b.high)} 低 ${fmt(b.low)} 收 ${fmt(b.close)}` + (change === null ? '' : `（${signedPct(change)}）`),
                `成交量 ${count(b.volume)} · 持仓量 ${count(b.open_interest)}${sse ? '' : ` · 结算 ${fmt(b.settle)}`}`,
                `${d.terms.underlying_name} ${fmt(b.underlying_close, sse ? 3 : 2)}`,
                `内在价值 ${fmt(b.intrinsic)} · 时间价值 ${fmt(b.time_value)}`,
                ...(sse ? [`隐含波动率 ${ivText(b.iv)} · Delta ${fmt(b.delta, 3)}`] : [])].join('<br/>');
      },
      textStyle: { fontSize: 12 },
      trigger: 'axis',
    },
    xAxis: [0, 1, 2, 3].map((k) => ({ boundaryGap: true, data: dates, gridIndex: k, type: 'category' as const, ...axisCommon,
                                     axisLabel: { color: MUTED, fontSize: 10, show: k === 3 }, axisTick: { show: k === 3 } })),
    yAxis: [
      { gridIndex: 0, scale: true, splitLine, splitNumber: 4, ...axisCommon },
      { gridIndex: 1, splitLine, splitNumber: 2, ...axisCommon, axisLabel: { show: false } },
      { gridIndex: 2, scale: true, splitLine, splitNumber: 2, ...axisCommon },
      { gridIndex: 3, scale: true, splitLine, splitNumber: 2, ...axisCommon,
        axisLabel: { color: MUTED, fontSize: 10, formatter: sse ? (v: number) => `${(v * 100).toFixed(0)}%` : undefined } },
    ],
  } as EChartsOption;
});

const minuteOption = computed<EChartsOption>(() => {
  const m = minutes.value;
  if (!m) return {};
  const times = m.rows.map((r) => r.time.slice(0, 5));
  const baseline = m.prev_close;
  return {
    animation: false,
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
    grid: [grid(26, 180), grid(232, 56)],
    legend: { data: ['价格', '均价'], left: 4, textStyle: { color: INK, fontSize: 11 }, top: 0 },
    series: [
      { color: SERIES[0], data: m.rows.map((r) => r.price ?? '-'), lineStyle: { width: 1.5 }, name: '价格', showSymbol: false,
        type: 'line', xAxisIndex: 0, yAxisIndex: 0,
        ...(baseline ? { markLine: { data: [{ yAxis: baseline }], label: { color: INK, fontSize: 10, formatter: `昨收 ${num(baseline)}` },
                                     lineStyle: { color: INK, type: 'dashed' as const }, silent: true, symbol: 'none' } } : {}) },
      { color: SERIES[1], data: m.rows.map((r) => r.avg_price ?? '-'), lineStyle: { type: 'dashed', width: 1 }, name: '均价',
        showSymbol: false, type: 'line', xAxisIndex: 0, yAxisIndex: 0 },
      { barMaxWidth: 3, color: MUTED, data: m.rows.map((r) => r.volume ?? 0), name: '成交量', type: 'bar', xAxisIndex: 1, yAxisIndex: 1 },
    ],
    title: [paneTitle('成交量（张）', 214)],
    tooltip: {
      confine: true,
      formatter: (params: any) => {
        const r = m.rows[(params as any[])[0]?.dataIndex ?? 0];
        if (!r) return '';
        const change = !missing(r.price) && baseline ? (r.price / baseline - 1) * 100 : null;
        return [`<b>${minuteDay.value} ${r.time.slice(0, 5)}</b>`,
                `价格 ${num(r.price)}${change === null ? '' : `（${signedPct(change)}）`} · 均价 ${num(r.avg_price)}`,
                `成交 ${count(r.volume)} 张 · 持仓 ${count(r.open_interest)} 张`].join('<br/>');
      },
      textStyle: { fontSize: 12 },
      trigger: 'axis',
    },
    xAxis: [0, 1].map((k) => ({ boundaryGap: false, data: times, gridIndex: k, type: 'category' as const, ...axisCommon,
                               axisLabel: { color: MUTED, fontSize: 10, show: k === 1 }, axisTick: { show: k === 1 } })),
    yAxis: [{ gridIndex: 0, scale: true, splitLine, ...axisCommon }, { gridIndex: 1, splitLine, splitNumber: 2, ...axisCommon }],
  } as EChartsOption;
});

onMounted(async () => {
  loading.value = true;
  try {
    underlyings.value = (await request<{ underlyings: OptionUnderlying[] }>('/options/underlyings')).underlyings;
  } catch (error: any) {
    failed.value = error.message;
    loading.value = false;
    return;
  }
  const wanted = route.query.underlying as string | undefined;
  underlying.value = underlyings.value.some((u) => u.underlying === wanted) ? wanted : underlyings.value[0]?.underlying;
  month.value = (route.query.month as string | undefined) || undefined;
  await loadChain();
});
</script>

<template>
  <van-nav-bar title="期权" left-arrow @click-left="router.back()" />
  <div class="note" style="margin-top: 12px">上交所 ETF 期权和中金所股指期权：按到期月份看每个行权价的认购、认沽，点开任一合约看它的日 K 和分时</div>
  <van-cell-group inset>
    <van-cell title="标的" is-link :value="current ? `${current.name}（${current.underlying}）` : '—'" @click="underlyings.length && ((picking = 'underlying'), (pickerShow = true))" />
    <van-cell title="到期月" is-link :value="monthText" @click="chain && ((picking = 'month'), (pickerShow = true))" />
    <van-cell title="交易日" center>
      <template #value>
        <div class="op-days">
          <van-button size="small" icon="arrow-left" class="op-btn" :disabled="!chain || chain.sessions[0] === day" @click="stepSession(-1)" />
          <span class="op-day" @click="chain && (calendar = true)">{{ day ?? '—' }}</span>
          <van-button size="small" icon="arrow" class="op-btn" :disabled="!chain || chain.sessions.at(-1) === day" @click="stepSession(1)" />
        </div>
      </template>
    </van-cell>
  </van-cell-group>

  <van-empty v-if="failed" :description="failed" />
  <van-loading v-else-if="loading && !chain" style="padding: 48px 0; text-align: center" />
  <template v-else-if="chain">
    <div class="op-summary">
      {{ chain.underlying.name }} 收盘 <b>{{ num(chain.underlying.close, cffex ? 2 : 3) }}</b>
      <span :style="{ color: color(chain.underlying.change_pct) }">{{ signedPct(chain.underlying.change_pct) }}</span>
      <van-tag v-if="chain.expired" style="margin-left: 6px">已到期</van-tag>
      <span v-else class="muted" style="margin-left: 6px">距到期 {{ chain.days_left }} 天（{{ chain.expiry_date }}）</span>
    </div>
    <van-tabs v-model:active="board" style="margin: 0 12px">
      <van-tab name="t" title="T 型报价" />
      <van-tab name="call" title="认购" />
      <van-tab name="put" title="认沽" />
    </van-tabs>
    <van-empty v-if="!chain.rows.length" description="没有期权数据" />
    <div v-else class="card" :style="{ opacity: loading ? 0.5 : 1 }">
      <template v-if="board === 't'">
        <div class="op-t op-head"><div>认购（收盘 / 涨跌幅）</div><div>行权价</div><div>认沽（收盘 / 涨跌幅）</div></div>
        <div v-for="row in chain.rows" :key="`${row.strike}-${row.adjusted}`" class="op-t">
          <div class="op-side" :style="{ background: inMoney(row, 'call') ? ITM : undefined }" @click="open(row.call)">
            <template v-if="row.call">
              <div :style="{ color: color(row.call.change), fontWeight: row.call.contract === highlight ? 700 : 500 }">{{ num(row.call.close) }}</div>
              <div class="op-sub" :style="{ color: color(row.call.change_pct) }">{{ signedPct(row.call.change_pct) }}</div>
            </template>
          </div>
          <div class="op-strike">
            <b>{{ row.strike }}</b>
            <div v-if="row.atm || row.adjusted">
              <van-tag v-if="row.atm" type="primary">平值</van-tag>
              <van-tag v-if="row.adjusted" color="#ed6a0c" style="margin-left: 2px" @click="showDialog({ message: ADJUSTED })">调</van-tag>
            </div>
          </div>
          <div class="op-side" :style="{ background: inMoney(row, 'put') ? ITM : undefined }" @click="open(row.put)">
            <template v-if="row.put">
              <div :style="{ color: color(row.put.change), fontWeight: row.put.contract === highlight ? 700 : 500 }">{{ num(row.put.close) }}</div>
              <div class="op-sub" :style="{ color: color(row.put.change_pct) }">{{ signedPct(row.put.change_pct) }}</div>
            </template>
          </div>
        </div>
      </template>
      <div v-else class="table-wrap">
        <table class="table">
          <thead>
            <tr><th>行权价</th><th>收盘</th><th>涨跌幅</th><th>成交量</th><th>持仓量</th><th>Delta</th><th v-if="!cffex">隐含波动率</th></tr>
          </thead>
          <tbody>
            <template v-for="row in chain.rows" :key="`${row.strike}-${row.adjusted}`">
              <tr v-if="row[board]" :class="{ 'op-itm': inMoney(row, board) }" @click="open(row[board])">
                <td :style="{ fontWeight: row[board]!.contract === highlight ? 700 : 600 }">
                  {{ row.strike }}<span v-if="row.atm" class="op-mark"> 平值</span><span v-if="row.adjusted" class="op-mark" style="color: #ed6a0c"> 调</span>
                </td>
                <td :style="{ color: color(row[board]!.change) }">{{ num(row[board]!.close) }}</td>
                <td :style="{ color: color(row[board]!.change_pct) }">{{ signedPct(row[board]!.change_pct) }}</td>
                <td>{{ count(row[board]!.volume) }}</td>
                <td>{{ count(row[board]!.open_interest) }}</td>
                <td>{{ num(row[board]!.delta, 3) }}</td>
                <td v-if="!cffex">{{ ivText(row[board]!.iv) }}</td>
              </tr>
            </template>
          </tbody>
        </table>
      </div>
    </div>
    <div class="note">
      浅色底 = 实值（认购行权价低于标的收盘，认沽高于）。{{ chain.unit }}。点任一合约看它的走势。
      “调”是{{ ADJUSTED }}。
      <template v-if="!cffex">隐含波动率、Delta 为交易所当天收盘后公布的数值，到期日当天没有。成交量、持仓量由分钟线汇总，开始抓分钟线以前的日子显示“—”。</template>
      <template v-else>Delta 为中金所公布的数值；成交量、持仓量单位为张。</template>
    </div>
  </template>

  <van-popup v-model:show="pickerShow" position="bottom" round>
    <van-picker :title="picking === 'underlying' ? '选择标的' : '选择到期月'" :columns="pickerColumns" :model-value="pickerValue"
                @confirm="pick" @cancel="pickerShow = false" />
  </van-popup>
  <van-calendar v-if="chain" v-model:show="calendar" title="选择交易日" :min-date="minDate" :max-date="maxDate"
                :default-date="day ? toDate(day) : maxDate" :formatter="formatDay" :show-confirm="false" :first-day-of-week="1"
                @confirm="pickDay" />

  <van-popup :show="!!selected" position="bottom" round closeable :style="{ height: '90%' }"
             @update:show="(show: boolean) => !show && popup.close()">
    <div v-if="selected" class="op-popup">
      <van-loading v-if="detailLoading" style="padding: 48px 0; text-align: center" />
      <template v-else-if="detail">
        <div class="op-popup-title">
          {{ detail.terms.name }} <span class="muted">{{ detail.terms.code }}</span>
          <van-tag v-if="detail.terms.adjusted" color="#ed6a0c" style="margin-left: 4px" @click="showDialog({ message: ADJUSTED })">已调整</van-tag>
        </div>
        <div v-for="item in termsLine" :key="item" class="op-term">{{ item }}</div>
        <van-tabs v-model:active="mode" style="margin-top: 4px">
          <van-tab name="daily" title="日 K" />
          <van-tab name="minute" title="分时" :disabled="!detail.minute_days.length" />
        </van-tabs>
        <template v-if="mode === 'daily'">
          <EChart v-if="detail.bars.length" :option="dailyOption" :height="460" style="margin-top: 8px" />
          <van-empty v-else image-size="80" description="没有行情" />
          <div class="muted op-help">
            虚线是内在价值（认购 = 标的 − 行权价，认沽 = 行权价 − 标的，不小于 0）；K 线高出虚线的部分是时间价值，越临近到期越少。
            <template v-if="detail.terms.exchange === 'SSE'">上交所合约的成交量由分钟线汇总，开始抓分钟线以前没有。</template>
          </div>
        </template>
        <template v-else>
          <van-cell title="分钟线日期" is-link :value="minuteDay" style="margin: 4px -16px 0" @click="minuteSheet = true" />
          <EChart v-if="minutes" :option="minuteOption" :height="310" style="margin-top: 8px" />
          <van-loading v-else style="padding: 32px 0; text-align: center" />
          <div class="muted op-help">分钟线只保留下一个交易日开盘前抓到的那一天（每天早上 09:05 抓取），从开始抓取那天起才有；中金所期权没有分钟线。</div>
        </template>
      </template>
    </div>
  </van-popup>
  <van-action-sheet v-model:show="minuteSheet" title="分钟线日期" cancel-text="取消" close-on-click-action
                    :actions="[...(detail?.minute_days ?? [])].reverse().map((d) => ({ name: d, color: d === minuteDay ? '#1989fa' : undefined }))"
                    @select="(action: { name: string }) => (minuteDay = action.name)" />
</template>

<style scoped>
.op-days { display: flex; align-items: center; justify-content: flex-end; gap: 6px; }
.op-btn { height: 36px; min-width: 40px; }
.op-day { padding: 8px 4px; color: #323233; font-weight: 600; white-space: nowrap; }
.op-summary { margin: 12px 16px 4px; font-size: 14px; }
.op-t { display: grid; grid-template-columns: 1fr 88px 1fr; border-bottom: 1px solid #f5f6f8; }
.op-head { color: #969799; font-size: 11px; text-align: center; }
.op-head > div { padding: 4px 0; }
.op-side { min-height: 44px; padding: 4px 8px; text-align: right; font-size: 14px; font-variant-numeric: tabular-nums; }
.op-t .op-side:last-child { text-align: left; }
.op-sub { font-size: 11px; }
.op-strike { display: flex; flex-direction: column; align-items: center; justify-content: center; font-size: 14px; }
.op-mark { color: #1989fa; font-size: 11px; font-weight: 400; }
.table .op-itm td { background: #f4f1e4; }
.op-popup { padding: 16px; }
.op-popup-title { margin-bottom: 6px; padding-right: 32px; font-size: 16px; font-weight: 600; }
.op-term { color: #646566; font-size: 13px; line-height: 20px; }
.op-help { margin-top: 6px; line-height: 18px; }
</style>
