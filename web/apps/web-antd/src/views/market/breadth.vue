<script lang="ts" setup>
import type { EchartsUIType } from '@vben/plugins/echarts';

import type { Breadth } from '#/api';

import { computed, onMounted, ref, watch } from 'vue';

import { Page } from '@vben/common-ui';
import { EchartsUI, useEcharts } from '@vben/plugins/echarts';
import { usePreferences } from '@vben/preferences';

import { Alert, Card, Col, Radio, Row, Spin, Table } from 'ant-design-vue';

import { breadthApi } from '#/api';
import { changeColor, pct } from '#/utils/format';

const { isDark } = usePreferences();
const data = ref<Breadth>();
const failed = ref('');
const loading = ref(true);
const range = ref<'1y' | '3y' | 'all'>('3y');
const chartRef = ref<EchartsUIType>();
const { renderEcharts } = useEcharts(chartRef);

/* Categorical slots 1-4 in fixed order (validated adjacent pairs, both themes); one per moving average. */
const SERIES_COLORS = { dark: ['#3987e5', '#d95926', '#199e70', '#c98500'], light: ['#2a78d6', '#eb6834', '#1baf7a', '#eda100'] };
const WINDOWS = [20, 60, 120, 250] as const;
const latest = computed(() => data.value?.latest ?? {});
const num = (key: string) => (latest.value[key] as null | number | undefined) ?? null;
/** A change of a share in percentage points: 0.125 -> "+12.5". */
const points = (value?: null | number, digits = 1) =>
  value === null || value === undefined ? '—' : `${value > 0 ? '+' : ''}${(value * 100).toFixed(digits)}`;

function draw() {
  const d = data.value;
  if (!d) return;
  const colors = SERIES_COLORS[isDark.value ? 'dark' : 'light'];
  const ink = isDark.value ? '#c3c2b7' : '#52514e';
  const dates = d.series.map((r) => r.trade_date);
  const close = new Map(d.index.map((r) => [r.trade_date, r.close]));
  const days = range.value === '1y' ? 250 : range.value === '3y' ? 750 : dates.length;
  const start = Math.max(0, dates.length - days);
  void renderEcharts({
    animation: false,
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
    color: colors,
    dataZoom: [{ type: 'inside', xAxisIndex: [0, 1], startValue: start },
               { type: 'slider', xAxisIndex: [0, 1], startValue: start, bottom: 4, height: 16 }],
    grid: [{ left: 56, right: 24, top: 36, height: '52%' }, { left: 56, right: 24, top: '72%', height: '16%' }],
    legend: { top: 0, data: WINDOWS.map((n) => `站上 ${n} 日均线`),
              selected: { '站上 20 日均线': false, '站上 120 日均线': false } },
    title: [{ text: '沪深300', left: 56, top: '67%', textStyle: { color: ink, fontSize: 12, fontWeight: 'normal' } }],
    tooltip: { trigger: 'axis', formatter: (params: any) => {
      const i = (params as any[])[0]?.dataIndex ?? 0;
      const r = d.series[i];
      if (!r) return '';
      return [`<b>${r.trade_date}</b>（${r.stocks} 只股票）`,
              ...WINDOWS.map((n) => `站上 ${n} 日均线 ${pct(r[`above${n}` as 'above20'], 1)}`),
              `创一年新高 ${r.highs} · 新低 ${r.lows}`, `上涨 ${r.ups} · 下跌 ${r.downs}`,
              `沪深300 ${close.get(r.trade_date)?.toFixed(2) ?? '—'}`].join('<br/>');
    } },
    xAxis: [0, 1].map((k) => ({ type: 'category', data: dates, gridIndex: k, boundaryGap: false,
                               axisLabel: { show: k === 1 }, axisTick: { show: k === 1 } })),
    yAxis: [
      { gridIndex: 0, min: 0, max: 100, axisLabel: { formatter: '{value}%' }, splitLine: { lineStyle: { opacity: 0.3 } } },
      { gridIndex: 1, scale: true, splitNumber: 2, splitLine: { lineStyle: { opacity: 0.3 } } },
    ],
    series: [
      ...WINDOWS.map((n, k) => ({
        type: 'line', name: `站上 ${n} 日均线`, xAxisIndex: 0, yAxisIndex: 0, showSymbol: false, lineStyle: { width: 1.5 },
        data: d.series.map((r) => +(r[`above${n}` as 'above20'] * 100).toFixed(1)),
        ...(k === 0 ? { markLine: { silent: true, symbol: 'none', data: [{ yAxis: 50, lineStyle: { color: ink, type: 'dashed' },
                                                                         label: { color: ink, formatter: '50%' } }] } } : {}),
      })),
      { type: 'line', name: '沪深300', xAxisIndex: 1, yAxisIndex: 1, showSymbol: false, color: ink, lineStyle: { width: 1 },
        data: dates.map((day) => close.get(day) ?? '-') },
    ],
  } as any);
}

const industryColumns = [
  { dataIndex: 'industry', title: '申万一级行业' },
  { dataIndex: 'stocks', title: '股票数', align: 'right' },
  ...WINDOWS.map((n) => ({ key: `above${n}`, title: `站上 ${n} 日均线`, align: 'right' })),
  { key: 'highs', title: '新高 / 新低', align: 'right' },
];

onMounted(async () => {
  try {
    data.value = await breadthApi();
    draw();
  } catch (error: any) {
    failed.value = error?.response?.data?.detail?.message ?? '市场宽度数据暂不可用';
  } finally {
    loading.value = false;
  }
});
watch([range, isDark], draw);
</script>

<template>
  <Page title="市场宽度" :description="data ? `全市场有多少股票处在上升趋势；数据截至 ${data.as_of}。只作为了解市场环境的信息，不作为买卖信号` : ''">
    <Alert v-if="failed" type="warning" show-icon :message="failed" />
    <Spin :spinning="loading">
      <template v-if="data">
        <Row :gutter="[12, 12]" class="mb-3">
          <Col v-for="n in WINDOWS" :key="n" :xs="12" :md="8" :xl="4">
            <Card size="small" class="h-full">
              <div class="text-muted-foreground text-xs">站上 {{ n }} 日均线</div>
              <div class="text-2xl">{{ pct(num(`above${n}`), 1) }}</div>
              <div class="text-xs">5 日 <span :style="{ color: changeColor(num(`above${n}_5d`)) }">{{ points(num(`above${n}_5d`)) }}</span> 个百分点</div>
              <div class="text-muted-foreground text-xs">历史上 {{ pct(num(`above${n}_rank`), 0) }} 的日子比现在低</div>
            </Card>
          </Col>
          <Col :xs="12" :md="8" :xl="4">
            <Card size="small" class="h-full">
              <div class="text-muted-foreground text-xs">创一年新高 / 新低（家）</div>
              <div class="text-2xl">{{ latest.highs }} / {{ latest.lows }}</div>
              <div class="text-muted-foreground text-xs">共 {{ latest.stocks }} 只上市满一年的股票</div>
            </Card>
          </Col>
          <Col :xs="12" :md="8" :xl="4">
            <Card size="small" class="h-full">
              <div class="text-muted-foreground text-xs">当日上涨 / 下跌（家）</div>
              <div class="text-2xl">{{ latest.ups }} / {{ latest.downs }}</div>
              <div class="text-muted-foreground text-xs">{{ latest.trade_date }}</div>
            </Card>
          </Col>
        </Row>

        <Card size="small" class="mb-3" title="走势">
          <template #extra>
            <Radio.Group v-model:value="range" size="small" button-style="solid">
              <Radio.Button value="1y">近 1 年</Radio.Button>
              <Radio.Button value="3y">近 3 年</Radio.Button>
              <Radio.Button value="all">全部</Radio.Button>
            </Radio.Group>
          </template>
          <EchartsUI ref="chartRef" height="460px" />
        </Card>

        <Card size="small" :title="`各行业（${data.industry_days[0]}，括号里是 ${data.rules.industry_lookback} 个交易日以来变化了几个百分点）`">
          <Table size="small" :pagination="false" row-key="industry" :columns="industryColumns as any" :data-source="data.industries">
            <template #bodyCell="{ column, record }">
              <template v-if="String(column.key).startsWith('above')">
                {{ pct(record[String(column.key)], 0) }}
                <span class="text-xs" :style="{ color: changeColor(record[`${String(column.key)}_change`]) }">
                  （{{ points(record[`${String(column.key)}_change`] as number, 0) }}）
                </span>
              </template>
              <template v-else-if="column.key === 'highs'">{{ record.highs }} / {{ record.lows }}</template>
            </template>
          </Table>
          <div class="text-muted-foreground mt-2 text-xs leading-5">
            算法：上市满 {{ data.rules.min_history }} 个交易日的股票，用后复权收盘价，看是否站上自己的 20、60、120、250 日均线，
            是否创一年（250 个交易日）新高或新低。行业按申万一级的当前分类。
            宽度高说明多数股票在涨，低说明多数股票在跌、选股赚钱难；宽度从低位回升，常是行情开始扩散的迹象。
            常见的用法是宽度低于 50% 时少开新仓，本页只展示数据，不据此发出买卖信号。
          </div>
        </Card>
      </template>
    </Spin>
  </Page>
</template>
