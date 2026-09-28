<script setup lang="ts">
import type { Chart } from 'klinecharts';

import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import { dispose, init } from 'klinecharts';

import { request } from '../api';
import { big, color, DOWN, UP } from '../format';

const route = useRoute();
const router = useRouter();
const symbol = String(route.params.symbol);
const BOARD: Record<string, string> = { BSE: '北交所', CHINEXT: '创业板', SSE_MAIN: '沪市主板', STAR: '科创板', SZSE_MAIN: '深市主板' };
const info = ref<any>();
const bars = ref<any[]>([]);
const container = ref<HTMLDivElement>();
let chart: Chart | null = null;

function draw() {
  if (!container.value || !bars.value.length) return;
  if (chart) dispose(container.value);
  chart = init(container.value, {
    styles: { candle: { bar: { downBorderColor: DOWN, downColor: DOWN, downWickColor: DOWN, upBorderColor: UP, upColor: UP, upWickColor: UP } } },
  });
  if (!chart) return;
  chart.createIndicator({ calcParams: [5, 10, 20], name: 'MA', paneId: 'candle_pane' }, true);
  chart.createIndicator('VOL');
  chart.setSymbol({ pricePrecision: 2, ticker: symbol, volumePrecision: 0 });
  chart.setPeriod({ span: 1, type: 'day' });
  const data = bars.value.map((b) => ({ close: b.close, high: b.high, low: b.low, open: b.open,
                                        timestamp: new Date(`${b.trade_date}T00:00:00+08:00`).getTime(), volume: b.volume }));
  chart.setDataLoader({ getBars: ({ callback }) => callback(data, false) });
}

onMounted(async () => {
  const [detail, rows] = await Promise.all([request(`/instruments/${symbol}`), request(`/instruments/${symbol}/bars?limit=160`)]);
  info.value = detail;
  bars.value = rows;
  await nextTick();
  draw();
});
onBeforeUnmount(() => container.value && dispose(container.value));
</script>

<template>
  <van-nav-bar :title="info ? `${info.name} ${symbol}` : symbol" left-arrow @click-left="router.back()" />
  <div v-if="bars.length" style="padding: 12px 16px 0">
    <b :style="{ color: color(bars.at(-1).pct_change), fontSize: '22px' }">{{ bars.at(-1).close.toFixed(2) }}</b>
    <span :style="{ color: color(bars.at(-1).pct_change), marginLeft: '8px' }">
      {{ bars.at(-1).pct_change > 0 ? '+' : '' }}{{ bars.at(-1).pct_change?.toFixed(2) }}%
    </span>
    <span class="muted" style="margin-left: 8px">{{ bars.at(-1).trade_date }} · 成交额 {{ big(bars.at(-1).amount) }}</span>
  </div>
  <div ref="container" style="height: 360px; background: #fff; margin: 8px 0"></div>
  <van-cell-group v-if="info" inset title="信息">
    <van-cell title="板块" :value="BOARD[info.board] ?? info.board" />
    <van-cell title="行业" :value="info.industry ? `${info.industry.l1_name} / ${info.industry.l2_name}` : '—'" />
    <van-cell title="风险警示" :value="info.risk_status === 'normal' ? '正常' : info.risk_status" />
    <van-cell title="上市日期" :value="info.list_date" />
  </van-cell-group>
  <p class="muted" style="text-align: center">前复权，仅用于展示</p>
</template>
