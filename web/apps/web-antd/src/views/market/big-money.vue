<script lang="ts" setup>
import type { EchartsUIType } from '@vben/plugins/echarts';

import type { BigMoneyDay, BigMoneyGroup } from '#/api';

import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';

import { Page } from '@vben/common-ui';
import { EchartsUI, useEcharts } from '@vben/plugins/echarts';
import { usePreferences } from '@vben/preferences';

import { Alert, Card, Col, Empty, Row, Select, Spin, Table, Tag, Tooltip } from 'ant-design-vue';

import { bigMoneyApi } from '#/api';
import { DOWN_COLOR, UP_COLOR } from '#/utils/format';

const { isDark } = usePreferences();
const data = ref<BigMoneyDay>();
const day = ref<string>();
const groupId = ref<string>();
const loading = ref(false);
const failed = ref('');
const chartRef = ref<EchartsUIType>();
const { renderEcharts } = useEcharts(chartRef);
let timer: ReturnType<typeof setInterval> | undefined;

/* Categorical slots 1-2 of the validated palette (as on 市场宽度); text in ink, not series colours. */
const SERIES = { dark: ['#3987e5', '#d95926'], light: ['#2a78d6', '#eb6834'] };
const RATIO_ALERT = 2;
const ink = computed(() => (isDark.value ? '#c3c2b7' : '#52514e'));
const muted = computed(() => (isDark.value ? '#8a8980' : '#8c8b85'));

const group = computed<BigMoneyGroup | undefined>(() => data.value?.groups.find((g) => g.id === groupId.value));
const missing = (v?: null | number) => v === null || v === undefined || Number.isNaN(v);
const yi = (v?: null | number, digits = 1) => (missing(v) ? '—' : `${v! > 0 ? '+' : ''}${(v! / 1e8).toFixed(digits)} 亿`);
const yiPlain = (v?: null | number) => (missing(v) ? '—' : `${(v! / 1e8).toFixed(1)} 亿`);
const pct = (v?: null | number, digits = 0) => (missing(v) ? '—' : `${(v! * 100).toFixed(digits)}%`);
const signed = (v?: null | number, digits = 2) => (missing(v) ? '—' : `${v! > 0 ? '+' : ''}${(v! * 100).toFixed(digits)}%`);
const times = (v?: null | number) => (missing(v) ? '—' : `${v!.toFixed(1)} 倍`);
const netColor = (v?: null | number) => (missing(v) || Math.abs(v!) < 1e8 ? undefined : v! > 0 ? UP_COLOR : DOWN_COLOR);

const statusText = computed(() => {
  const d = data.value;
  if (!d) return '';
  const at = d.updated.slice(11, 19);
  if (d.live) return `盘中，每分钟更新（最后更新 ${at}）`;
  return d.final ? `${d.day} 已收盘` : `${d.day}，监测没有在运行（最后更新 ${at}）`;
});

async function load(keepGroup = true) {
  loading.value = !data.value;
  failed.value = '';
  try {
    const result = await bigMoneyApi(day.value);
    data.value = result;
    day.value = result.day;
    if (!keepGroup || !result.groups.some((g) => g.id === groupId.value)) {
      const alerted = result.groups.find((g) => g.alerts.length);
      groupId.value = (alerted ?? result.groups.find((g) => g.id === 'csi300') ?? result.groups[0])?.id;
    }
    draw();
  } catch (error: any) {
    failed.value = error?.response?.data?.detail?.message ?? error?.message ?? '加载失败';
  } finally {
    loading.value = false;
  }
}

function draw() {
  const g = group.value;
  if (!g || g.series.length === 0) return;
  const colors = SERIES[isDark.value ? 'dark' : 'light'];
  const xs = g.series.map((p) => p.time);
  const axis = { axisLine: { lineStyle: { color: muted.value } }, axisLabel: { color: muted.value } };
  const split = { lineStyle: { opacity: 0.25 } };
  const title = (text: string, top: string) => ({ text, left: 64, top, textStyle: { color: ink.value, fontSize: 12, fontWeight: 'normal' } });
  void renderEcharts({
    animation: false,
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
    grid: [{ left: 64, right: 24, top: 28, height: '22%' }, { left: 64, right: 24, top: '38%', height: '24%' },
           { left: 64, right: 24, top: '72%', height: '20%' }],
    title: [title('指数', '0%'), title('单笔 500 万以上大单的累计主动净买入（亿元）', '32%'),
            title(`成交额 / 平常同一时刻（虚线 ${RATIO_ALERT} 倍为提醒门槛之一）`, '66%')],
    tooltip: { trigger: 'axis', formatter: (params: any) => {
      const p = g.series[(params as any[])[0]?.dataIndex ?? 0];
      if (!p) return '';
      return [`<b>${p.time}</b>`, `指数 ${missing(p.index_price) ? '—' : p.index_price!.toFixed(2)}（${signed(p.index_change)}）`,
              `成交 ${yiPlain(p.amount)}，平常同一时刻 ${yiPlain(p.baseline)}，${times(p.ratio)}`,
              `大单净买 ${yi(p.big_net)}（近 30 分钟 ${yi(p.big_net_30m)}），大单占成交 ${pct(p.big_share)}`,
              `全部主动净买 ${yi(p.active_net)}，溢价 ${signed(p.premium, 3)}`].join('<br/>');
    } },
    xAxis: [0, 1, 2].map((k) => ({ type: 'category', data: xs, gridIndex: k, boundaryGap: k === 1, ...axis,
                                  axisLabel: { show: k === 2, color: muted.value }, axisTick: { show: k === 2 } })),
    yAxis: [
      { gridIndex: 0, scale: true, splitNumber: 3, splitLine: split, ...axis },
      { gridIndex: 1, splitLine: split, ...axis, axisLabel: { color: muted.value, formatter: (v: number) => (v / 1e8).toFixed(0) } },
      { gridIndex: 2, min: 0, max: (v: { max: number }) => Math.max(3, Math.ceil(v.max)), splitNumber: 3, splitLine: split, ...axis },
    ],
    series: [
      { type: 'line', xAxisIndex: 0, yAxisIndex: 0, showSymbol: false, color: colors[0], lineStyle: { width: 1.5 },
        data: g.series.map((p) => p.index_price ?? '-') },
      { type: 'bar', xAxisIndex: 1, yAxisIndex: 1, barMaxWidth: 4,
        data: g.series.map((p) => ({ value: p.big_net ?? 0, itemStyle: { color: (p.big_net ?? 0) >= 0 ? UP_COLOR : DOWN_COLOR } })) },
      // the opening auction makes the first minutes' ratio jump: drawn from 09:40
      { type: 'line', xAxisIndex: 2, yAxisIndex: 2, showSymbol: false, color: colors[1], lineStyle: { width: 1.5 },
        data: g.series.map((p) => (p.time < '09:40' ? '-' : p.ratio ?? '-')),
        markLine: { silent: true, symbol: 'none', lineStyle: { color: ink.value, type: 'dashed' },
                    data: [{ yAxis: RATIO_ALERT, label: { position: 'insideStartTop', formatter: `${RATIO_ALERT} 倍` } },
                           { yAxis: 1, label: { position: 'insideStartBottom', formatter: '平常' } }],
                    label: { color: ink.value, fontSize: 11 } } },
    ],
  } as any);
}

const fundColumns = [
  { title: 'ETF', key: 'fund', width: 170 },
  { title: '成交额', key: 'amount', align: 'right', width: 100 },
  { title: '是平常的', key: 'ratio', align: 'right', width: 90 },
  { title: '大单净买', key: 'big_net', align: 'right', width: 110 },
  { title: '大单占比', key: 'big_share', align: 'right', width: 90 },
  { title: '溢价', key: 'premium', align: 'right', width: 90 },
  { title: '价格', key: 'price', align: 'right', width: 80 },
];

watch(groupId, () => draw());
watch(isDark, () => draw());
watch(() => data.value?.live, (live) => {
  if (timer) clearInterval(timer);
  timer = live ? setInterval(() => void load(), 60_000) : undefined;
});

onMounted(() => void load(false));
onBeforeUnmount(() => timer && clearInterval(timer));
</script>

<template>
  <Page title="盘中大资金" :description="statusText || '宽基 ETF 的成交和大单，每分钟和平常同一时刻比较'">
    <Alert v-if="failed" type="info" show-icon :message="failed" class="mb-3" />
    <Spin :spinning="loading">
      <template v-if="data">
        <div class="mb-3 flex flex-wrap items-center gap-2">
          <Select v-model:value="day" size="small" class="!w-40" :options="data.days.map((d) => ({ label: d, value: d }))"
                  @change="load()" />
          <Tag v-if="data.live" color="processing">盘中</Tag>
          <span v-if="data.errors" class="text-xs text-gray-500">本日有 {{ data.errors }} 次请求失败（已自动跳过）</span>
        </div>
        <Row :gutter="[12, 12]" class="mb-3">
          <Col v-for="g in data.groups" :key="g.id" :xs="12" :md="8" :xl="6" :xxl="3">
            <Card size="small" class="h-full cursor-pointer" :class="{ 'bm-selected': g.id === groupId }" @click="groupId = g.id">
              <div class="whitespace-nowrap font-bold">{{ g.name }}</div>
              <div v-if="g.alerts.length" class="mt-1">
                <Tag v-for="a in g.alerts" :key="a.direction" :color="a.direction === 'buy' ? 'red' : 'green'">
                  {{ a.direction === 'buy' ? '大额买入' : '大额卖出' }} {{ a.time }}
                </Tag>
              </div>
              <template v-if="g.latest">
                <div class="mt-1 text-sm">成交 <b>{{ times(g.latest.ratio) }}</b></div>
                <div class="text-sm">大单 <b :style="{ color: netColor(g.latest.big_net) }">{{ yi(g.latest.big_net) }}</b>
                  <span class="text-gray-500">（占 {{ pct(g.latest.big_share) }}）</span></div>
                <div class="text-xs text-gray-500">指数 {{ signed(g.latest.index_change) }}，溢价 {{ signed(g.latest.premium, 3) }}</div>
              </template>
              <div v-else class="mt-1 text-xs text-gray-500">还没有数据</div>
            </Card>
          </Col>
        </Row>
        <Card v-if="group" size="small" :title="`${group.name}：${group.funds.length} 只 ETF 合计，平常每天成交约 ${(group.usual_turnover / 1e8).toFixed(0)} 亿元`">
          <EchartsUI v-if="group.series.length" ref="chartRef" height="460px" />
          <Empty v-else description="这一组还没有数据" />
          <Table class="mt-3" size="small" :pagination="false" :columns="fundColumns as any" :data-source="group.funds" row-key="symbol">
            <template #bodyCell="{ column, record }">
              <template v-if="column.key === 'fund'">{{ record.name }} <span class="text-gray-400">{{ record.symbol }}</span></template>
              <template v-else-if="column.key === 'amount'">{{ yiPlain(record.amount) }}</template>
              <template v-else-if="column.key === 'ratio'">{{ times(record.ratio) }}</template>
              <template v-else-if="column.key === 'big_net'"><span :style="{ color: netColor(record.big_net) }">{{ yi(record.big_net) }}</span></template>
              <template v-else-if="column.key === 'big_share'">{{ pct(record.big_share) }}</template>
              <template v-else-if="column.key === 'premium'">{{ signed(record.premium, 3) }}</template>
              <template v-else-if="column.key === 'price'">{{ missing(record.price) ? '—' : record.price.toFixed(3) }}</template>
            </template>
          </Table>
          <div class="mt-2 text-xs text-gray-500">
            <Tooltip title="2026-10-15、16、20、22、23 五个大额申购日，按这个规则回放都会触发，其余日子不会；只有 13 个交易日的验证，仅供参考">
              <span class="underline decoration-dotted">提醒规则</span>
            </Tooltip>：
            09:45 以后，成交达到平常同一时刻的 2 倍以上、单笔 500 万以上的大单占成交一半以上、
            大单净买（卖）超过这组 ETF 平常一天成交的 15%（至少 2 亿元），每组每天提醒一次，写入通知中心。
            大单主动买入减主动卖出，按腾讯 3 秒逐笔的买卖方向计算；直接在一级市场申购的资金在这里看不到，当天的申购份额收盘后才公布。
            大资金进场是背景信息，不是买卖信号。
          </div>
        </Card>
      </template>
    </Spin>
  </Page>
</template>

<style scoped>
.bm-selected {
  outline: 2px solid #2a78d6;
}
</style>
