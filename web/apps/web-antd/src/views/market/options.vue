<script lang="ts" setup>
import type { EchartsUIType } from '@vben/plugins/echarts';

import type { Dayjs } from 'dayjs';

import type { OptionChain, OptionChainRow, OptionContract, OptionMinute, OptionSide, OptionUnderlying } from '#/api';

import { computed, nextTick, onMounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';
import { EchartsUI, useEcharts } from '@vben/plugins/echarts';
import { usePreferences } from '@vben/preferences';

import { Alert, Button, Card, DatePicker, Empty, Radio, Select, Spin, Table, Tag, Tooltip } from 'ant-design-vue';

import { optionChainApi, optionContractApi, optionMinutesApi, optionUnderlyingsApi } from '#/api';
import { DOWN_COLOR, UP_COLOR } from '#/utils/format';

const route = useRoute();
const router = useRouter();
const { isDark } = usePreferences();

const underlyings = ref<OptionUnderlying[]>([]);
const underlying = ref<string>();
const month = ref<string>();
const day = ref<string>();
const chain = ref<OptionChain>();
const selected = ref<string>();
const detail = ref<OptionContract>();
const mode = ref<'daily' | 'minute'>('daily');
const minuteDay = ref<string>();
const minutes = ref<{ prev_close: null | number; rows: OptionMinute[] }>();
const loading = ref(false);
const detailLoading = ref(false);
const failed = ref('');
const detailRef = ref<HTMLElement>();

const dailyRef = ref<EchartsUIType>();
const minuteRef = ref<EchartsUIType>();
const { renderEcharts: renderDaily } = useEcharts(dailyRef);
const { renderEcharts: renderMinute } = useEcharts(minuteRef);

/* Categorical slots 1-2 (validated palette, as on 市场宽度); ink for reference lines. */
const SERIES = { dark: ['#3987e5', '#d95926'], light: ['#2a78d6', '#eb6834'] };
const ink = computed(() => (isDark.value ? '#c3c2b7' : '#52514e'));
const muted = computed(() => (isDark.value ? '#8a8980' : '#8c8b85'));
const itmBackground = computed(() => (isDark.value ? 'rgba(255,255,255,0.08)' : '#f4f1e4'));

const current = computed(() => underlyings.value.find((u) => u.underlying === underlying.value));
const cffex = computed(() => current.value?.exchange === 'CFFEX');
const digits = computed(() => (cffex.value ? 1 : 4));
const spot = computed(() => chain.value?.underlying.close ?? null);
const sessions = computed(() => new Set(chain.value?.sessions ?? []));

const missing = (v?: null | number) => v === null || v === undefined || Number.isNaN(v);
const num = (v?: null | number, d = digits.value) => (missing(v) ? '—' : v!.toFixed(d));
const signedPct = (v?: null | number) => (missing(v) ? '—' : `${v! > 0 ? '+' : ''}${v!.toFixed(2)}%`);
const ivText = (v?: null | number) => (missing(v) ? '—' : `${(v! * 100).toFixed(1)}%`);
const count = (v?: null | number) => (missing(v) ? '—' : Math.round(v!).toLocaleString('zh-CN'));
const color = (v?: null | number) => (missing(v) || v === 0 ? undefined : v! > 0 ? UP_COLOR : DOWN_COLOR);

const monthOptions = computed(() => {
  const months = chain.value?.months ?? [];
  const label = (m: (typeof months)[number]) => `${m.expiry_month.replace('-', ' 年 ')} 月（${m.expiry_date} 到期）`;
  const live = months.filter((m) => !m.expired).map((m) => ({ label: label(m), value: m.expiry_month }));
  const expired = months.filter((m) => m.expired).reverse().map((m) => ({ label: label(m), value: m.expiry_month }));
  return [{ label: '在交易', options: live }, { label: `已到期（${expired.length} 个月）`, options: expired }]
    .filter((g) => g.options.length);
});

/** Calls are in the money below the underlying's close, puts above it. */
const inMoney = (row: OptionChainRow, kind: 'call' | 'put') =>
  spot.value !== null && !row.adjusted && (kind === 'call' ? row.strike < spot.value : row.strike > spot.value);

const SIDE_FIELDS = computed(() => [
  { field: 'iv', title: '隐含波动率', width: 92, hidden: cffex.value },
  { field: 'delta', title: 'Delta', width: 72 },
  { field: 'open_interest', title: '持仓量', width: 80 },
  { field: 'volume', title: '成交量', width: 84 },
  { field: 'change_pct', title: '涨跌幅', width: 84 },
  { field: 'close', title: '收盘', width: 84 },
].filter((f) => !f.hidden));

const columns = computed(() => {
  const side = (kind: 'call' | 'put') => {
    const fields = kind === 'call' ? SIDE_FIELDS.value : [...SIDE_FIELDS.value].reverse();
    return fields.map((f) => ({
      key: `${kind}.${f.field}`, title: f.title, width: f.width, align: 'right',
      customCell: (row: OptionChainRow) => {
        const item = row[kind];
        return {
          class: item ? 'cursor-pointer' : undefined,
          style: { background: inMoney(row, kind) ? itmBackground.value : undefined,
                   fontWeight: item && item.contract === selected.value ? 600 : undefined },
          onClick: () => item && select(item.contract),
        };
      },
    }));
  };
  return [
    { title: '认购', children: side('call') },
    { key: 'strike', title: '行权价', width: 100, align: 'center' },
    { title: '认沽', children: side('put') },
  ];
});

function cell(row: OptionChainRow, key: string): { color?: string; text: string } {
  const [kind, field] = key.split('.') as ['call' | 'put', keyof OptionSide];
  const item = row[kind];
  if (!item) return { text: '' };
  const value = item[field] as null | number;
  switch (field) {
    case 'change_pct': { return { color: color(value), text: signedPct(value) }; }
    case 'close': { return { color: color(item.change), text: num(value) }; }
    case 'delta': { return { text: num(value, 3) }; }
    case 'iv': { return { text: ivText(value) }; }
    default: { return { text: count(value) }; }
  }
}

function syncQuery() {
  void router.replace({ query: { underlying: underlying.value, month: month.value, contract: selected.value } });
}

async function loadChain() {
  if (!underlying.value) return;
  loading.value = true;
  failed.value = '';
  try {
    const result = await optionChainApi({ underlying: underlying.value, month: month.value, day: day.value });
    chain.value = result;
    month.value = result.month;
    day.value = result.day;
    syncQuery();
  } catch (error: any) {
    failed.value = error?.response?.data?.detail?.message ?? error?.message ?? '期权数据加载失败';
    chain.value = undefined;
  } finally {
    loading.value = false;
  }
}

function changeUnderlying() {
  month.value = undefined;
  day.value = undefined;
  void loadChain();
}

function changeMonth() {
  day.value = undefined;
  void loadChain();
}

function stepSession(step: -1 | 1) {
  const list = chain.value?.sessions ?? [];
  const i = list.indexOf(day.value ?? '');
  const next = list[i + step];
  if (next) {
    day.value = next;
    void loadChain();
  }
}

async function select(contract: string) {
  selected.value = contract;
  syncQuery();
  detailLoading.value = true;
  try {
    detail.value = await optionContractApi(contract);
    minuteDay.value = detail.value.minute_days.at(-1);
    if (mode.value === 'minute' && !minuteDay.value) mode.value = 'daily';
    await nextTick();
    detailRef.value?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    await drawDetail();
  } catch (error: any) {
    failed.value = error?.response?.data?.detail?.message ?? '合约数据加载失败';
  } finally {
    detailLoading.value = false;
  }
}

async function drawDetail() {
  if (mode.value === 'daily') {
    drawDaily();
  } else if (selected.value && minuteDay.value) {
    minutes.value = await optionMinutesApi(selected.value, minuteDay.value);
    drawMinutes();
  }
}

const axisCommon = () => ({ axisLine: { lineStyle: { color: muted.value } }, axisLabel: { color: muted.value } });
const splitLine = { lineStyle: { opacity: 0.25 } };
const paneTitle = (text: string, top: string) =>
  ({ text, left: 64, top, textStyle: { color: ink.value, fontSize: 12, fontWeight: 'normal' } });

function drawDaily() {
  const d = detail.value;
  if (!d || d.bars.length === 0) return;
  const colors = SERIES[isDark.value ? 'dark' : 'light'];
  const sse = d.terms.exchange === 'SSE';
  const dates = d.bars.map((b) => b.trade_date);
  const candles = d.bars.map((b) => [b.open ?? b.close, b.close, b.low ?? b.close, b.high ?? b.close]);
  const up = d.bars.map((b, i) => (b.close ?? 0) >= (i > 0 ? d.bars[i - 1]!.close ?? 0 : b.open ?? 0));
  const lower = sse ? '隐含波动率' : '持仓量';
  const fmt = (v?: null | number, n = digits.value) => (missing(v) ? '—' : v!.toFixed(n));
  void renderDaily({
    animation: false,
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
    dataZoom: [{ type: 'inside', xAxisIndex: [0, 1, 2, 3] },
               { type: 'slider', xAxisIndex: [0, 1, 2, 3], bottom: 4, height: 16 }],
    grid: [{ left: 64, right: 24, top: 28, height: '36%' }, { left: 64, right: 24, top: '46%', height: '9%' },
           { left: 64, right: 24, top: '61%', height: '13%' }, { left: 64, right: 24, top: '80%', height: '11%' }],
    legend: { top: 0, left: 64, textStyle: { color: ink.value }, data: [d.terms.kind === 'call' ? '认购' : '认沽', '内在价值'] },
    title: [paneTitle('成交量', '43%'), paneTitle(`${d.terms.underlying_name} 收盘（虚线 = 行权价）`, '58%'),
            paneTitle(lower, '77%')],
    tooltip: { trigger: 'axis', axisPointer: { type: 'cross' }, formatter: (params: any) => {
      const i = (params as any[])[0]?.dataIndex ?? 0;
      const b = d.bars[i];
      if (!b) return '';
      const prev = i > 0 ? d.bars[i - 1]!.close : null;
      const change = !missing(prev) && !missing(b.close) && prev ? (b.close! / prev - 1) * 100 : null;
      return [`<b>${b.trade_date}</b>`,
              `开 ${fmt(b.open)} 高 ${fmt(b.high)} 低 ${fmt(b.low)} 收 ${fmt(b.close)}`
                + (change === null ? '' : `（${signedPct(change)}）`),
              `成交量 ${count(b.volume)} · 持仓量 ${count(b.open_interest)}${sse ? '' : ` · 结算 ${fmt(b.settle)}`}`,
              `${d.terms.underlying_name} ${fmt(b.underlying_close, sse ? 3 : 2)}`,
              `内在价值 ${fmt(b.intrinsic)} · 时间价值 ${fmt(b.time_value)}`,
              ...(sse ? [`隐含波动率 ${ivText(b.iv)} · Delta ${fmt(b.delta, 3)}`] : [])].join('<br/>');
    } },
    xAxis: [0, 1, 2, 3].map((k) => ({ type: 'category', data: dates, gridIndex: k, boundaryGap: true, ...axisCommon(),
                                     axisLabel: { show: k === 3, color: muted.value }, axisTick: { show: k === 3 } })),
    yAxis: [
      { gridIndex: 0, scale: true, splitLine, ...axisCommon() },
      { gridIndex: 1, splitNumber: 2, splitLine, ...axisCommon(), axisLabel: { show: false } },
      { gridIndex: 2, scale: true, splitNumber: 3, splitLine, ...axisCommon() },
      { gridIndex: 3, scale: true, splitNumber: 2, splitLine, ...axisCommon(),
        axisLabel: { color: muted.value, formatter: sse ? (v: number) => `${(v * 100).toFixed(0)}%` : undefined } },
    ],
    series: [
      { type: 'candlestick', name: d.terms.kind === 'call' ? '认购' : '认沽', xAxisIndex: 0, yAxisIndex: 0, data: candles,
        itemStyle: { color: UP_COLOR, color0: DOWN_COLOR, borderColor: UP_COLOR, borderColor0: DOWN_COLOR } },
      { type: 'line', name: '内在价值', xAxisIndex: 0, yAxisIndex: 0, showSymbol: false, color: ink.value,
        lineStyle: { width: 1, type: 'dashed' }, data: d.bars.map((b) => b.intrinsic ?? '-') },
      { type: 'bar', name: '成交量', xAxisIndex: 1, yAxisIndex: 1, barMaxWidth: 8,
        data: d.bars.map((b, i) => ({ value: b.volume ?? 0, itemStyle: { color: up[i] ? UP_COLOR : DOWN_COLOR, opacity: 0.7 } })) },
      { type: 'line', name: d.terms.underlying_name, xAxisIndex: 2, yAxisIndex: 2, showSymbol: false, color: colors[0],
        lineStyle: { width: 1.5 }, data: d.bars.map((b) => b.underlying_close ?? '-'),
        markLine: { silent: true, symbol: 'none', data: [{ yAxis: d.terms.strike }],
                    lineStyle: { color: ink.value, type: 'dashed' }, label: { color: ink.value, formatter: `${d.terms.strike}` } } },
      sse
        ? { type: 'line', name: '隐含波动率', xAxisIndex: 3, yAxisIndex: 3, showSymbol: false, color: colors[1],
            lineStyle: { width: 1.5 }, connectNulls: false, data: d.bars.map((b) => b.iv ?? '-') }
        : { type: 'bar', name: '持仓量', xAxisIndex: 3, yAxisIndex: 3, barMaxWidth: 8, color: colors[1],
            data: d.bars.map((b) => b.open_interest ?? 0) },
    ],
  } as any);
}

function drawMinutes() {
  const m = minutes.value;
  if (!m) return;
  const colors = SERIES[isDark.value ? 'dark' : 'light'];
  const times = m.rows.map((r) => r.time.slice(0, 5));
  const baseline = m.prev_close;
  void renderMinute({
    animation: false,
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
    grid: [{ left: 64, right: 24, top: 28, height: '58%' }, { left: 64, right: 24, top: '74%', height: '18%' }],
    legend: { top: 0, left: 64, textStyle: { color: ink.value }, data: ['价格', '均价'] },
    title: [paneTitle('成交量（张）', '69%')],
    tooltip: { trigger: 'axis', formatter: (params: any) => {
      const r = m.rows[(params as any[])[0]?.dataIndex ?? 0];
      if (!r) return '';
      const change = !missing(r.price) && baseline ? (r.price! / baseline - 1) * 100 : null;
      return [`<b>${minuteDay.value} ${r.time.slice(0, 5)}</b>`,
              `价格 ${num(r.price)}${change === null ? '' : `（${signedPct(change)}）`} · 均价 ${num(r.avg_price)}`,
              `成交 ${count(r.volume)} 张 · 持仓 ${count(r.open_interest)} 张`].join('<br/>');
    } },
    xAxis: [0, 1].map((k) => ({ type: 'category', data: times, gridIndex: k, boundaryGap: false, ...axisCommon(),
                               axisLabel: { show: k === 1, color: muted.value }, axisTick: { show: k === 1 } })),
    yAxis: [{ gridIndex: 0, scale: true, splitLine, ...axisCommon() },
            { gridIndex: 1, splitNumber: 2, splitLine, ...axisCommon() }],
    series: [
      { type: 'line', name: '价格', xAxisIndex: 0, yAxisIndex: 0, showSymbol: false, color: colors[0], lineStyle: { width: 1.5 },
        data: m.rows.map((r) => r.price ?? '-'),
        ...(baseline ? { markLine: { silent: true, symbol: 'none', data: [{ yAxis: baseline }],
                                     lineStyle: { color: ink.value, type: 'dashed' },
                                     label: { color: ink.value, formatter: `昨收 ${num(baseline)}` } } } : {}) },
      { type: 'line', name: '均价', xAxisIndex: 0, yAxisIndex: 0, showSymbol: false, color: colors[1],
        lineStyle: { width: 1, type: 'dashed' }, data: m.rows.map((r) => r.avg_price ?? '-') },
      { type: 'bar', name: '成交量', xAxisIndex: 1, yAxisIndex: 1, barMaxWidth: 4, color: muted.value,
        data: m.rows.map((r) => r.volume ?? 0) },
    ],
  } as any);
}

const termsLine = computed(() => {
  const d = detail.value;
  if (!d) return [];
  const t = d.terms;
  const first = d.bars[0]?.trade_date;
  const last = d.bars.at(-1)?.trade_date;
  return [
    `${t.kind === 'call' ? '认购' : '认沽'} · 行权价 ${t.strike}`,
    `到期日 ${t.expiry_date}`,
    t.multiplier ? `合约单位 ${t.multiplier.toLocaleString('zh-CN')}${t.exchange === 'SSE' ? ' 份' : ' 元/点'}` : '合约单位已调整',
    first ? `交易 ${first} ~ ${last}（${d.bars.length} 个交易日）` : '没有行情',
  ];
});

watch(mode, () => void drawDetail());
watch(minuteDay, () => mode.value === 'minute' && void drawDetail());
watch(isDark, () => void drawDetail());

onMounted(async () => {
  loading.value = true;
  try {
    underlyings.value = (await optionUnderlyingsApi()).underlyings;
  } catch (error: any) {
    failed.value = error?.response?.data?.detail?.message ?? '期权数据不可用';
    loading.value = false;
    return;
  }
  const wanted = route.query.underlying as string | undefined;
  underlying.value = underlyings.value.some((u) => u.underlying === wanted) ? wanted : underlyings.value[0]?.underlying;
  month.value = (route.query.month as string | undefined) || undefined;
  await loadChain();
  const contract = route.query.contract as string | undefined;
  if (contract) void select(contract);
});
</script>

<template>
  <Page title="期权" description="上交所 ETF 期权和中金所股指期权：按到期月份看每个行权价的认购、认沽，点开任一合约看它的日 K 和分时">
    <Alert v-if="failed" type="warning" show-icon :message="failed" class="mb-3" />
    <Card size="small">
      <div class="mb-3 flex flex-wrap items-center gap-2">
        <Select v-model:value="underlying" size="small" class="!w-60" :options="underlyings.map((u) => ({
                  label: `${u.name}（${u.underlying}）`, value: u.underlying }))" @change="changeUnderlying" />
        <Select v-model:value="month" size="small" class="!w-64" :options="monthOptions" @change="changeMonth" />
        <Button size="small" :disabled="!chain || chain.sessions[0] === day" @click="stepSession(-1)">前一日</Button>
        <DatePicker v-model:value="day" size="small" value-format="YYYY-MM-DD" :allow-clear="false"
                    :disabled-date="(d: Dayjs) => !sessions.has(d.format('YYYY-MM-DD'))" @change="loadChain" />
        <Button size="small" :disabled="!chain || chain.sessions.at(-1) === day" @click="stepSession(1)">后一日</Button>
        <template v-if="chain">
          <span class="ml-2">
            {{ chain.underlying.name }} 收盘
            <b>{{ num(chain.underlying.close, cffex ? 2 : 3) }}</b>
            <span :style="{ color: color(chain.underlying.change_pct) }">{{ signedPct(chain.underlying.change_pct) }}</span>
          </span>
          <Tag v-if="chain.expired">已到期</Tag>
          <span v-else class="text-gray-500">距到期 {{ chain.days_left }} 天（{{ chain.expiry_date }}）</span>
        </template>
      </div>
      <Spin :spinning="loading">
        <Table v-if="chain && chain.rows.length" size="small" bordered :pagination="false"
               :columns="columns as any" :data-source="chain.rows" :scroll="{ x: 'max-content' }"
               :row-key="(row: OptionChainRow) => `${row.strike}-${row.adjusted}`">
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'strike'">
              <b>{{ record.strike }}</b>
              <Tooltip v-if="record.adjusted" title="分红后调整过的合约：行权价和合约单位已变，代码末尾为 A、B 等">
                <Tag class="ml-1" color="orange">调</Tag>
              </Tooltip>
              <Tag v-if="record.atm" class="ml-1" color="blue">平值</Tag>
            </template>
            <template v-else-if="column.key">
              <span :style="{ color: cell(record as OptionChainRow, String(column.key)).color }">
                {{ cell(record as OptionChainRow, String(column.key)).text }}
              </span>
            </template>
          </template>
        </Table>
        <Empty v-else-if="!loading" description="没有期权数据" />
      </Spin>
      <div v-if="chain" class="mt-2 text-xs text-gray-500">
        浅色底 = 实值（认购行权价低于标的收盘，认沽高于）。{{ chain.unit }}。点任一合约的数字看它的走势。
        <template v-if="!cffex">隐含波动率、Delta 为交易所当天收盘后公布的数值，到期日当天没有。成交量、持仓量由分钟线汇总，开始抓分钟线以前的日子显示“—”。</template>
        <template v-else>Delta 为中金所公布的数值；成交量、持仓量单位为张。</template>
      </div>
    </Card>

    <div ref="detailRef">
      <Card v-if="selected" size="small" class="mt-3">
        <template #title>
          <span v-if="detail">{{ detail.terms.name }} <span class="text-gray-400">{{ detail.terms.code }}</span></span>
          <Tag v-if="detail?.terms.adjusted" class="ml-2" color="orange">已调整</Tag>
        </template>
        <template #extra>
          <Radio.Group v-model:value="mode" size="small" button-style="solid">
            <Radio.Button value="daily">日 K</Radio.Button>
            <Radio.Button value="minute" :disabled="!detail?.minute_days.length">分时</Radio.Button>
          </Radio.Group>
        </template>
        <Spin :spinning="detailLoading">
          <div class="mb-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
            <span v-for="item in termsLine" :key="item">{{ item }}</span>
            <Select v-if="mode === 'minute' && detail?.minute_days.length" v-model:value="minuteDay" size="small" class="!w-36"
                    :options="[...detail.minute_days].reverse().map((d) => ({ label: d, value: d }))" />
          </div>
          <EchartsUI v-show="mode === 'daily'" ref="dailyRef" height="560px" />
          <EchartsUI v-show="mode === 'minute'" ref="minuteRef" height="420px" />
          <div class="mt-1 text-xs text-gray-500">
            <template v-if="mode === 'daily'">
              虚线是内在价值（认购 = 标的 − 行权价，认沽 = 行权价 − 标的，不小于 0）；K 线高出虚线的部分是时间价值，越临近到期越少。
              <template v-if="detail?.terms.exchange === 'SSE'">上交所合约的成交量由分钟线汇总，开始抓分钟线以前没有。</template>
            </template>
            <template v-else>分钟线只保留下一个交易日开盘前抓到的那一天（每天早上 09:05 抓取），从开始抓取那天起才有；中金所期权没有分钟线。</template>
          </div>
        </Spin>
      </Card>
    </div>
  </Page>
</template>
