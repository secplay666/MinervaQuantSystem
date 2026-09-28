<script lang="ts" setup>
import type { Chart, KLineData } from 'klinecharts';

import type { Bar } from '#/api';

import { onBeforeUnmount, onMounted, ref, watch } from 'vue';

import { dispose, init } from 'klinecharts';

import { DOWN_COLOR, UP_COLOR } from '#/utils/format';

const props = withDefaults(defineProps<{ bars: Bar[]; height?: number; indicators?: string[]; ticker: string }>(), {
  height: 460,
  indicators: () => ['VOL', 'MACD'],
});

const container = ref<HTMLDivElement>();
let chart: Chart | null = null;

function toData(bars: Bar[]): KLineData[] {
  return bars.map((b) => ({
    close: b.close, high: b.high, low: b.low, open: b.open,
    timestamp: new Date(`${b.trade_date}T00:00:00+08:00`).getTime(),
    turnover: b.amount ?? undefined, volume: b.volume ?? undefined,
  }));
}

function render() {
  if (!container.value) return;
  if (chart) dispose(container.value);
  chart = init(container.value, {
    styles: {
      candle: { bar: { downBorderColor: DOWN_COLOR, downColor: DOWN_COLOR, downWickColor: DOWN_COLOR,
                       upBorderColor: UP_COLOR, upColor: UP_COLOR, upWickColor: UP_COLOR } },
    },
  });
  if (!chart) return;
  chart.createIndicator({ calcParams: [5, 10, 20, 60], name: 'MA', paneId: 'candle_pane' }, true);
  for (const name of props.indicators) chart.createIndicator(name);
  chart.setSymbol({ pricePrecision: 2, ticker: props.ticker, volumePrecision: 0 });
  chart.setPeriod({ span: 1, type: 'day' });
  const data = toData(props.bars);
  chart.setDataLoader({ getBars: ({ callback }) => callback(data, false) });
}

onMounted(render);
watch(() => [props.bars, props.ticker], render);
onBeforeUnmount(() => container.value && dispose(container.value));
</script>

<template>
  <div ref="container" :style="{ height: `${height}px`, width: '100%' }"></div>
</template>
