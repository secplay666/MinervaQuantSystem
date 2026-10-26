<script setup lang="ts">
import type { EChartsOption } from 'echarts';

import type { Breadth } from './info-api';

import { computed, onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';

import { request } from '../../api';
import EChart from '../../components/EChart.vue';
import { color, pct } from '../../format';
import { INK, SERIES } from './info-api';

const router = useRouter();
const data = ref<Breadth>();
const failed = ref('');
const loading = ref(false);
const range = ref<'1y' | '3y' | 'all'>('3y');
const sortKey = ref('');

const WINDOWS = [20, 60, 120, 250] as const;
const latest = computed(() => data.value?.latest ?? {});
const num = (key: string) => (latest.value[key] as null | number | undefined) ?? null;
/** A change of a share in percentage points: 0.125 -> "+12.5". */
const points = (value?: null | number | string, digits = 1) =>
  typeof value !== 'number' ? '—' : `${value > 0 ? '+' : ''}${(value * 100).toFixed(digits)}`;
const asNumber = (value: unknown) => (typeof value === 'number' ? value : null);

const industries = computed(() => {
  const rows = data.value?.industries ?? [];
  const key = sortKey.value;
  if (!key) return rows;
  return [...rows].sort((a, b) => (asNumber(b[key]) ?? -Infinity) - (asNumber(a[key]) ?? -Infinity));
});

async function load() {
  loading.value = true;
  try {
    data.value = await request<Breadth>('/breadth');
    failed.value = '';
  } catch (error: any) {
    failed.value = error.message;
  } finally {
    loading.value = false;
  }
}

const option = computed<EChartsOption>(() => {
  const d = data.value;
  if (!d) return {};
  const dates = d.series.map((r) => r.trade_date);
  const close = new Map(d.index.map((r) => [r.trade_date, r.close]));
  const days = range.value === '1y' ? 250 : range.value === '3y' ? 750 : dates.length;
  const start = Math.max(0, dates.length - days);
  return {
    animation: false,
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
    color: SERIES,
    dataZoom: [{ bottom: 4, height: 18, startValue: start, type: 'slider', xAxisIndex: [0, 1] }],
    grid: [{ height: 190, left: 40, right: 12, top: 52 }, { height: 64, left: 40, right: 12, top: 274 }],
    legend: { data: WINDOWS.map((n) => `站上 ${n} 日均线`), itemGap: 8, itemWidth: 14, textStyle: { fontSize: 11 }, top: 0,
              selected: { '站上 120 日均线': false, '站上 20 日均线': false } },
    series: [
      ...WINDOWS.map((n, k) => ({
        data: d.series.map((r) => +(r[`above${n}` as 'above20'] * 100).toFixed(1)), lineStyle: { width: 1.5 },
        name: `站上 ${n} 日均线`, showSymbol: false, type: 'line' as const, xAxisIndex: 0, yAxisIndex: 0,
        ...(k === 0 ? { markLine: { data: [{ label: { color: INK, fontSize: 10, formatter: '50%' }, lineStyle: { color: INK, type: 'dashed' as const }, yAxis: 50 }],
                                    silent: true, symbol: 'none' } } : {}),
      })),
      { color: INK, data: dates.map((day) => close.get(day) ?? '-'), lineStyle: { width: 1 }, name: '沪深300',
        showSymbol: false, type: 'line', xAxisIndex: 1, yAxisIndex: 1 },
    ],
    title: [{ left: 4, text: '沪深300', textStyle: { color: INK, fontSize: 11, fontWeight: 'normal' }, top: 254 }],
    tooltip: {
      confine: true,
      formatter: (params: any) => {
        const r = d.series[(params as any[])[0]?.dataIndex ?? 0];
        if (!r) return '';
        return [`<b>${r.trade_date}</b>（${r.stocks} 只股票）`,
                ...WINDOWS.map((n) => `站上 ${n} 日均线 ${pct(r[`above${n}` as 'above20'], 1)}`),
                `创一年新高 ${r.highs} · 新低 ${r.lows}`, `上涨 ${r.ups} · 下跌 ${r.downs}`,
                `沪深300 ${close.get(r.trade_date)?.toFixed(2) ?? '—'}`].join('<br/>');
      },
      textStyle: { fontSize: 12 },
      trigger: 'axis',
    },
    xAxis: [0, 1].map((k) => ({ axisLabel: { fontSize: 10, show: k === 1 }, axisTick: { show: k === 1 }, boundaryGap: false,
                               data: dates, gridIndex: k, type: 'category' as const })),
    yAxis: [
      { axisLabel: { fontSize: 10, formatter: '{value}%' }, gridIndex: 0, max: 100, min: 0, splitLine: { lineStyle: { opacity: 0.3 } } },
      { axisLabel: { fontSize: 10 }, gridIndex: 1, scale: true, splitLine: { lineStyle: { opacity: 0.3 } }, splitNumber: 2 },
    ],
  } as EChartsOption;
});

onMounted(load);
</script>

<template>
  <van-nav-bar title="市场宽度" left-arrow @click-left="router.back()" />
  <van-pull-refresh v-model="loading" @refresh="load">
    <van-empty v-if="failed && !data" :description="failed" />
    <van-skeleton v-else-if="!data" :row="8" style="margin-top: 16px" />
    <template v-else>
      <div class="note" style="margin-top: 12px">
        全市场有多少股票处在上升趋势；数据截至 {{ data.as_of }}。只作为了解市场环境的信息，不作为买卖信号
      </div>
      <div class="br-tiles">
        <div v-for="n in WINDOWS" :key="n" class="br-tile">
          <div class="muted">站上 {{ n }} 日均线</div>
          <div class="br-big">{{ pct(num(`above${n}`), 1) }}</div>
          <div class="br-small">5 日 <span :style="{ color: color(num(`above${n}_5d`)) }">{{ points(num(`above${n}_5d`)) }}</span> 个百分点</div>
          <div class="muted">历史上 {{ pct(num(`above${n}_rank`), 0) }} 的日子比现在低</div>
        </div>
        <div class="br-tile">
          <div class="muted">创一年新高 / 新低（家）</div>
          <div class="br-big">{{ latest.highs }} / {{ latest.lows }}</div>
          <div class="muted">共 {{ latest.stocks }} 只上市满一年的股票</div>
        </div>
        <div class="br-tile">
          <div class="muted">当日上涨 / 下跌（家）</div>
          <div class="br-big">{{ latest.ups }} / {{ latest.downs }}</div>
          <div class="muted">{{ latest.trade_date }}</div>
        </div>
      </div>

      <div class="card">
        <div class="card-title">走势</div>
        <van-tabs v-model:active="range" shrink>
          <van-tab name="1y" title="近 1 年" />
          <van-tab name="3y" title="近 3 年" />
          <van-tab name="all" title="全部" />
        </van-tabs>
        <EChart :option="option" :height="390" style="margin-top: 8px" />
        <div class="muted">点图例可显示或隐藏均线；拖动底部滑块看其他时段。</div>
      </div>

      <div class="card">
        <div class="card-title">各行业（{{ data.industry_days[0] }}）</div>
        <div class="muted" style="margin-bottom: 6px">
          括号里是 {{ data.rules.industry_lookback }} 个交易日以来变化了几个百分点；点表头按该列从高到低排序，再点恢复。
        </div>
        <div class="table-wrap">
          <table class="table">
            <thead>
              <tr>
                <th>申万一级行业</th>
                <th v-for="n in WINDOWS" :key="n" @click="sortKey = sortKey === `above${n}` ? '' : `above${n}`">
                  站上 {{ n }} 日{{ sortKey === `above${n}` ? ' ↓' : '' }}
                </th>
                <th @click="sortKey = sortKey === 'highs' ? '' : 'highs'">新高 / 新低{{ sortKey === 'highs' ? ' ↓' : '' }}</th>
                <th @click="sortKey = sortKey === 'stocks' ? '' : 'stocks'">股票数{{ sortKey === 'stocks' ? ' ↓' : '' }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in industries" :key="String(row.industry)">
                <td>{{ row.industry }}</td>
                <td v-for="n in WINDOWS" :key="n">
                  {{ pct(asNumber(row[`above${n}`]), 0) }}
                  <span :style="{ color: color(asNumber(row[`above${n}_change`])) }">（{{ points(row[`above${n}_change`], 0) }}）</span>
                </td>
                <td>{{ row.highs }} / {{ row.lows }}</td>
                <td>{{ row.stocks }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
      <div class="note">
        算法：上市满 {{ data.rules.min_history }} 个交易日的股票，用后复权收盘价，看是否站上自己的 20、60、120、250 日均线，
        是否创一年（250 个交易日）新高或新低。行业按申万一级的当前分类。
        宽度高说明多数股票在涨，低说明多数股票在跌、选股赚钱难；宽度从低位回升，常是行情开始扩散的迹象。
        常见的用法是宽度低于 50% 时少开新仓，本页只展示数据，不据此发出买卖信号。
      </div>
    </template>
  </van-pull-refresh>
</template>

<style scoped>
.br-tiles { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin: 12px; }
.br-tile { padding: 10px; background: #fff; border-radius: 8px; }
.br-big { margin: 2px 0; font-size: 22px; font-weight: 600; }
.br-small { font-size: 12px; }
</style>
