<script setup lang="ts">
import type { EChartsOption } from 'echarts';

import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';

import { showFailToast } from 'vant';

import { qs, request } from '../../api';
import EChart from '../../components/EChart.vue';

interface Point { near_vol: null | number; next_vol: null | number; time: string; value: null | number }
interface VolIndex {
  alert: null | string; change: null | number; history: { trade_date: string; value: number }[]; history_from: null | string;
  id: string; latest: null | (Point & { near_expiry: null | string; next_expiry: null | string }); name: string;
  percentile: null | number; previous_close: null | number; series: Point[]; value: null | number;
}
interface VolDay { day: null | string; days: string[]; final: boolean; history_years: number; indices: VolIndex[]; live: boolean; updated: null | string }

const router = useRouter();
const data = ref<VolDay>();
const day = ref<string>();
const loading = ref(false);
const empty = ref('');
const view = ref<'history' | 'intraday'>('intraday');
let timer: ReturnType<typeof setInterval> | undefined;

/* Validated categorical slots 1-4 (light), fixed per index; each line also carries its name. */
const COLORS: Record<string, string> = { etf300: '#2a78d6', io: '#eb6834', etf50: '#1baf7a', mo: '#eda100' };
const missing = (v?: null | number) => v === null || v === undefined || Number.isNaN(v);
const num = (v?: null | number, d = 1) => (missing(v) ? '—' : v!.toFixed(d));
const signed = (v?: null | number) => (missing(v) ? '—' : `${v! > 0 ? '+' : ''}${(v! * 100).toFixed(1)}%`);

function band(p?: null | number): { label: string; type: 'danger' | 'default' | 'primary' | 'warning' } {
  if (missing(p)) return { label: '无历史', type: 'default' };
  if (p! >= 0.9) return { label: '恐慌', type: 'danger' };
  if (p! >= 0.7) return { label: '偏紧张', type: 'warning' };
  if (p! >= 0.3) return { label: '正常', type: 'primary' };
  return { label: '平静', type: 'default' };
}

const status = computed(() => {
  const d = data.value;
  if (!d) return '';
  if (!d.day) return '还没有盘中数据，下面是每日历史';
  if (d.live) return `盘中，每分钟更新（${d.updated?.slice(11, 16)}）`;
  return d.final ? `${d.day} 已收盘` : `${d.day}，监测没有在运行`;
});

async function load() {
  loading.value = true;
  try {
    data.value = await request<VolDay>(`/monitor/volatility${qs({ day: day.value })}`);
    day.value = data.value.day ?? undefined;
    empty.value = '';
    if (!data.value.day) view.value = 'history';
  } catch (error: any) {
    if (error.status === 404) empty.value = error.message;
    else showFailToast(error.message);
  } finally {
    loading.value = false;
  }
}

function line(index: VolIndex, points: [string, null | number][]) {
  return { color: COLORS[index.id], connectNulls: true, data: points, endLabel: { formatter: index.name.replace(' 波动率', '').replace(' 股指期权波动率', ''), show: true, fontSize: 10 },
           lineStyle: { width: 2 }, name: index.name, showSymbol: false, type: 'line' as const };
}

const option = computed<EChartsOption>(() => {
  const d = data.value;
  if (!d) return {};
  const base = { grid: { bottom: 28, left: 36, right: 64, top: 40 }, legend: { itemHeight: 8, textStyle: { fontSize: 10 }, top: 0 },
                 tooltip: { trigger: 'axis' as const, valueFormatter: (v: any) => num(v) } };
  if (view.value === 'intraday' && d.day) {
    const times = [...new Set(d.indices.flatMap((i) => i.series.map((p) => p.time)))].sort();
    return { ...base, series: d.indices.map((i) => { const m = new Map(i.series.map((p) => [p.time, p.value])); return line(i, times.map((t) => [t, m.get(t) ?? null])); }),
             xAxis: { boundaryGap: false, data: times, type: 'category' }, yAxis: { scale: true, type: 'value' } };
  }
  return { ...base, dataZoom: [{ type: 'inside' }], series: d.indices.map((i) => line(i, i.history.map((h) => [h.trade_date, h.value]))),
           xAxis: { type: 'time' }, yAxis: { scale: true, type: 'value' } };
});

onMounted(async () => {
  await load();
  timer = setInterval(() => data.value?.live && load(), 60_000);
});
onBeforeUnmount(() => timer && clearInterval(timer));
</script>

<template>
  <van-nav-bar title="恐慌指数" left-arrow @click-left="router.back()" />
  <van-pull-refresh v-model="loading" @refresh="load">
    <div class="note" style="margin-top: 8px">A 股期权隐含的未来 30 天波动率（按 VIX 方法），越高表示市场预期波动越大。{{ status }}</div>
    <van-empty v-if="empty" :description="empty" />
    <template v-else-if="data">
      <van-grid :column-num="2" :border="false" style="margin: 0 4px">
        <van-grid-item v-for="index in data.indices" :key="index.id">
          <div style="width: 100%; text-align: left">
            <div class="muted"><span :style="{ display: 'inline-block', width: '8px', height: '8px', borderRadius: '4px', background: COLORS[index.id], marginRight: '4px' }"></span>{{ index.name }}</div>
            <div style="display: flex; align-items: baseline; gap: 6px">
              <b style="font-size: 22px">{{ num(index.value) }}</b>
              <van-tag :type="band(index.percentile).type">{{ band(index.percentile).label }}</van-tag>
            </div>
            <div class="muted">较前日 {{ signed(index.change) }} · 第 {{ missing(index.percentile) ? '—' : Math.round(index.percentile! * 100) }} 百分位</div>
            <div class="muted">一个月涨跌约 ±{{ missing(index.value) ? '—' : (index.value! / Math.sqrt(12)).toFixed(1) }}%</div>
            <van-tag v-if="index.alert" type="danger" plain style="margin-top: 4px">{{ index.alert }} 飙升提醒</van-tag>
          </div>
        </van-grid-item>
      </van-grid>
      <div class="card">
        <van-tabs v-model:active="view" shrink>
          <van-tab v-if="data.day" title="盘中" name="intraday" />
          <van-tab :title="`近 ${data.history_years} 年`" name="history" />
        </van-tabs>
        <EChart :option="option" :height="280" />
      </div>
      <van-cell-group v-if="data.days.length" inset title="盘中记录的日期">
        <van-cell v-for="d in data.days.slice(0, 5)" :key="d" :title="d" is-link :value="d === data.day ? '当前' : ''"
                  @click="day = d; view = 'intraday'; load()" />
      </van-cell-group>
      <div class="note">
        怎么读：数值是年化波动率（%），除以 √12 约等于未来一个月的一倍标准差涨跌幅。急跌时大家抢买看跌期权，指数就会跳升；它不预测方向，极端飙升常出现在急跌末段，但不能单独判断见底。
        300ETF、50ETF 来自上交所 ETF 期权，沪深300、中证1000 来自中金所股指期权；盘中每分钟一次，历史每天早上用收盘价回算。比前一交易日高 20% 以上且处在近 3 年 80% 分位以上时，通知中心提醒一次。
      </div>
    </template>
  </van-pull-refresh>
</template>
