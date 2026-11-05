<script lang="ts" setup>
import type { EchartsUIType } from '@vben/plugins/echarts';

import type { VolatilityDay, VolatilityIndex } from '#/api';

import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';

import { Page } from '@vben/common-ui';
import { EchartsUI, useEcharts } from '@vben/plugins/echarts';
import { usePreferences } from '@vben/preferences';

import { Alert, Card, Col, Empty, Row, Select, Spin, Table, Tag } from 'ant-design-vue';

import { volatilityApi } from '#/api';

const { isDark } = usePreferences();
const data = ref<VolatilityDay>();
const day = ref<string>();
const loading = ref(false);
const failed = ref('');
const intradayRef = ref<EchartsUIType>();
const historyRef = ref<EchartsUIType>();
const { renderEcharts: renderIntraday } = useEcharts(intradayRef);
const { renderEcharts: renderHistory } = useEcharts(historyRef);
let timer: ReturnType<typeof setInterval> | undefined;

/* Categorical slots 1-4 of the validated palette, fixed per index (colour follows the entity). In light mode slots
   3-4 are below 3:1 against the surface, so every line carries its name and the cards/table give the values. */
const PALETTE = { dark: ['#3987e5', '#d95926', '#199e70', '#c98500'], light: ['#2a78d6', '#eb6834', '#1baf7a', '#eda100'] };
const ORDER = ['etf300', 'io', 'etf50', 'mo'];
const colorOf = (id: string) => PALETTE[isDark.value ? 'dark' : 'light'][Math.max(0, ORDER.indexOf(id)) % 4];
const ink = computed(() => (isDark.value ? '#c3c2b7' : '#52514e'));
const muted = computed(() => (isDark.value ? '#8a8980' : '#8c8b85'));

const missing = (v?: null | number) => v === null || v === undefined || Number.isNaN(v);
const num = (v?: null | number, digits = 1) => (missing(v) ? '—' : v!.toFixed(digits));
const signed = (v?: null | number) => (missing(v) ? '—' : `${v! > 0 ? '+' : ''}${(v! * 100).toFixed(1)}%`);

/** Where the latest value sits in the last years (a reading, with its label, never colour alone). */
function band(p?: null | number): { color: string; label: string } {
  if (missing(p)) return { color: 'default', label: '无历史' };
  if (p! >= 0.9) return { color: 'error', label: '恐慌' };
  if (p! >= 0.7) return { color: 'warning', label: '偏紧张' };
  if (p! >= 0.3) return { color: 'processing', label: '正常' };
  return { color: 'default', label: '平静' };
}

const statusText = computed(() => {
  const d = data.value;
  if (!d) return '';
  if (!d.day) return '还没有盘中数据，下面是每日历史';
  const at = d.updated?.slice(11, 19) ?? '';
  if (d.live) return `盘中，每分钟更新（最后更新 ${at}）`;
  return d.final ? `${d.day} 已收盘` : `${d.day}，监测没有在运行（最后更新 ${at}）`;
});

async function load() {
  loading.value = !data.value;
  failed.value = '';
  try {
    const result = await volatilityApi(day.value);
    data.value = result;
    day.value = result.day ?? undefined;
  } catch (error: any) {
    failed.value = error?.response?.data?.detail?.message ?? error?.message ?? '加载失败';
  } finally {
    loading.value = false;
  }
}

function minutes(): string[] {
  const out: string[] = [];
  for (const [h0, m0, h1, m1] of [[9, 30, 11, 30], [13, 1, 15, 0]] as const) {
    for (let t = h0 * 60 + m0; t <= h1 * 60 + m1; t++) out.push(`${String(Math.floor(t / 60)).padStart(2, '0')}:${String(t % 60).padStart(2, '0')}`);
  }
  return out;
}

function lineSeries(index: VolatilityIndex, points: [string, null | number][]) {
  return {
    color: colorOf(index.id), connectNulls: true, data: points, endLabel: { color: ink.value, formatter: index.name, show: true },
    lineStyle: { width: 2 }, name: index.name, showSymbol: false, symbol: 'circle', symbolSize: 8, type: 'line' as const,
  };
}

function draw() {
  const d = data.value;
  if (!d) return;
  const axis = { axisLabel: { color: muted.value }, axisLine: { lineStyle: { color: muted.value } } };
  const split = { lineStyle: { color: isDark.value ? '#2a2a2c' : '#ececea' } };
  const legend = { textStyle: { color: ink.value }, top: 0 };
  const grid = { bottom: 40, left: 48, right: 150, top: 36 };
  if (d.day) {
    const xs = minutes();
    renderIntraday({
      animation: false, grid, legend,
      series: d.indices.map((index) => {
        const byTime = new Map(index.series.map((p) => [p.time, p.value]));
        return lineSeries(index, xs.map((t) => [t, byTime.get(t) ?? null]));
      }),
      tooltip: { trigger: 'axis', valueFormatter: (v: any) => num(v) },
      xAxis: { ...axis, boundaryGap: false, data: xs, type: 'category', axisLabel: { color: muted.value, interval: 29 } },
      yAxis: { ...axis, name: '年化波动率 %', nameTextStyle: { color: muted.value }, scale: true, splitLine: split, type: 'value' },
    });
  }
  renderHistory({
    animation: false, dataZoom: [{ type: 'inside' }, { bottom: 4, height: 18, type: 'slider' }],
    grid: { ...grid, bottom: 56 }, legend,
    series: d.indices.map((index) => lineSeries(index, index.history.map((h) => [h.trade_date, h.value]))),
    tooltip: { trigger: 'axis', valueFormatter: (v: any) => num(v) },
    xAxis: { ...axis, type: 'time' },
    yAxis: { ...axis, name: '年化波动率 %', nameTextStyle: { color: muted.value }, scale: true, splitLine: split, type: 'value' },
  });
}

const columns = [
  { dataIndex: 'name', key: 'name', title: '指数' },
  { key: 'value', title: '最新', align: 'right' as const },
  { key: 'previous', title: '前一交易日收盘', align: 'right' as const },
  { key: 'change', title: '变化', align: 'right' as const },
  { key: 'percentile', title: '近 3 年分位', align: 'right' as const },
  { key: 'month', title: '对应一个月涨跌 ±', align: 'right' as const },
  { key: 'terms', title: '所用到期月', align: 'right' as const },
  { key: 'since', title: '历史起点', align: 'right' as const },
];

watch(day, (value, previous) => previous !== undefined && value !== previous && load());
watch([data, isDark], draw, { flush: 'post' });
onMounted(async () => {
  await load();
  timer = setInterval(() => data.value?.live && load(), 60_000);
});
onBeforeUnmount(() => timer && clearInterval(timer));
</script>

<template>
  <Page title="恐慌指数" description="A 股期权隐含的未来 30 天波动率（按美股 VIX 的方法计算），数值越高，市场预期的波动越大">
    <template #extra>
      <span class="text-muted-foreground mr-3 text-sm">{{ statusText }}</span>
      <Select v-if="data?.days.length" v-model:value="day" style="width: 140px" :options="data.days.map((d) => ({ label: d, value: d }))" />
    </template>
    <Spin :spinning="loading">
      <Alert v-if="failed" type="info" show-icon :message="failed" class="mb-4" />
      <template v-if="data">
        <Row :gutter="[16, 16]">
          <Col v-for="index in data.indices" :key="index.id" :xs="24" :sm="12" :xl="6">
            <Card size="small">
              <div class="flex items-center justify-between">
                <span class="font-medium"><span class="mr-2 inline-block h-2 w-2 rounded-full" :style="{ background: colorOf(index.id) }"></span>{{ index.name }}</span>
                <Tag :color="band(index.percentile).color">{{ band(index.percentile).label }}</Tag>
              </div>
              <div class="mt-2 text-3xl font-semibold">{{ num(index.value) }}</div>
              <div class="text-muted-foreground mt-1 text-xs">
                较前一交易日 {{ signed(index.change) }} · 近 {{ data.history_years }} 年第 {{ missing(index.percentile) ? '—' : Math.round(index.percentile! * 100) }} 百分位
              </div>
              <div class="text-muted-foreground text-xs">
                对应未来一个月涨跌约 ±{{ missing(index.value) ? '—' : (index.value! / Math.sqrt(12)).toFixed(1) }}%（一倍标准差）
              </div>
              <Tag v-if="index.alert" color="error" class="mt-2">{{ index.alert }} 飙升提醒</Tag>
            </Card>
          </Col>
        </Row>
        <Card v-if="data.day" size="small" :title="`盘中走势（${data.day}）`" class="mt-4">
          <EchartsUI ref="intradayRef" height="320px" />
        </Card>
        <Card size="small" :title="`近 ${data.history_years} 年（每个交易日收盘）`" class="mt-4">
          <EchartsUI ref="historyRef" height="340px" />
        </Card>
        <Card size="small" title="明细" class="mt-4">
          <Table :columns="columns" :data-source="data.indices" row-key="id" size="small" :pagination="false">
            <template #bodyCell="{ column, record }">
              <template v-if="column.key === 'value'">{{ num(record.value, 2) }}</template>
              <template v-else-if="column.key === 'previous'">{{ num(record.previous_close, 2) }}</template>
              <template v-else-if="column.key === 'change'">{{ signed(record.change) }}</template>
              <template v-else-if="column.key === 'percentile'">{{ missing(record.percentile) ? '—' : `${Math.round(record.percentile * 100)}%` }}</template>
              <template v-else-if="column.key === 'month'">{{ missing(record.value) ? '—' : `${(record.value / Math.sqrt(12)).toFixed(1)}%` }}</template>
              <template v-else-if="column.key === 'terms'">{{ record.latest ? `${record.latest.near_expiry ?? '—'} / ${record.latest.next_expiry ?? '—'}` : '—' }}</template>
              <template v-else-if="column.key === 'since'">{{ record.history_from ?? '—' }}</template>
            </template>
          </Table>
        </Card>
        <Card size="small" title="怎么读" class="mt-4">
          <div class="text-muted-foreground space-y-1 text-sm leading-6">
            <div>· 算法和美股 VIX 相同：取最近两个到期月（离到期不足 7 天的月份换到下一个月）的全部虚值期权买卖中间价，算出期权市场隐含的方差，再插值到正好 30 天，开根号乘 100。</div>
            <div>· 数值是年化波动率（%）。除以 √12 约等于未来一个月的一倍标准差涨跌幅，除以 16 约等于每天的。比如 20 表示市场预期一个月内涨跌约 ±5.8%。</div>
            <div>· 股市急跌时大家抢买看跌期权当保险，指数就会跳升，所以叫“恐慌指数”。它不预测方向，只说明市场预期的波动会多大；极端飙升常出现在急跌的末段，但不能单独用来判断见底。</div>
            <div>· 300ETF、50ETF 来自上交所 ETF 期权，沪深300、中证1000 来自中金所股指期权；盘中用新浪实时报价，每分钟一次。历史曲线每天早上用前一交易日的收盘价（中金所用结算价）回算，所以和盘中值略有差异。</div>
            <div>· 某个指数比前一交易日收盘高 20% 以上、且处在近 3 年 80% 分位以上时，通知中心会提醒一次。这只是提示，不是交易信号。</div>
          </div>
        </Card>
      </template>
      <Empty v-else-if="!loading && !failed" />
    </Spin>
  </Page>
</template>
