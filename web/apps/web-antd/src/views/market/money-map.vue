<script lang="ts" setup>
import type { EchartsUIType } from '@vben/plugins/echarts';

import type { MoneyMap, MoneyMapFrame, MoneyMapIndustry, MoneyMapRow, MoneyMapStats } from '#/api';

import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';

import { Page } from '@vben/common-ui';
import { EchartsUI, useEcharts } from '@vben/plugins/echarts';
import { usePreferences } from '@vben/preferences';
import { useFullscreen } from '@vueuse/core';

import { Alert, Button, Card, Col, Empty, Radio, Row, Slider, Space, Spin, Switch, Table, Tag, Tooltip } from 'ant-design-vue';

import { moneyMapApi, moneyMapIndustryApi } from '#/api';
import { pct } from '#/utils/format';

const { isDark } = usePreferences();
const map = ref<MoneyMap>();
const failed = ref('');
const loading = ref(false);
const range = ref<'all' | 'recent'>('recent');
const frameIndex = ref(0);
const playing = ref(false);
const allTrails = ref(true);
const selected = ref<string>();
const detail = ref<MoneyMapIndustry>();
const chartRef = ref<EchartsUIType>();
const { renderEcharts } = useEcharts(chartRef);
const detailRef = ref<EchartsUIType>();
const { renderEcharts: renderDetail } = useEcharts(detailRef);
let chart: any;
let timer: ReturnType<typeof setInterval> | undefined;
const mapBox = ref<HTMLElement>(); // the map card with its controls, shown full screen as a whole
const { isFullscreen, toggle: toggleFullscreen } = useFullscreen(mapBox);
const popupBox = () => mapBox.value ?? document.body; // the slider's tip stays visible in full screen

/* Colour roles (validated with the dataviz validator, both modes; aqua and the red are
 * told apart by shape too: 必须减 is a triangle).  Zones without a signal stay gray. */
const PALETTE = {
  dark: { fight: '#3987e5', starting: '#199e70', crowded: '#d03b3b', quiet: '#898781', ink: '#ffffff',
          ink2: '#c3c2b7', muted: '#898781', grid: '#2c2c2a', surface: '#151517' },
  light: { fight: '#2a78d6', starting: '#1baf7a', crowded: '#d03b3b', quiet: '#898781', ink: '#0b0b0b',
           ink2: '#52514e', muted: '#898781', grid: '#e1e0d9', surface: '#ffffff' },
};
const colors = computed(() => PALETTE[isDark.value ? 'dark' : 'light']);
const zoneColor = (zone: null | string) => {
  const c = colors.value;
  return zone === 'crowded' ? c.crowded : zone === 'fight' ? c.fight : zone === 'starting' ? c.starting : c.quiet;
};

const names = computed(() => Object.fromEntries((map.value?.industries ?? []).map((i) => [i.code, i.name])));
const frames = computed(() => map.value?.frames ?? []);
const frame = computed<MoneyMapFrame | undefined>(() => frames.value[frameIndex.value]);
const rules = computed(() => map.value?.rules);
const TRAIL = 8; // weeks of trail behind each bubble

/** Axes of the shown week and its trail: wide enough for the zones, rounded so they move in steps. */
const extent = computed(() => {
  let lo = -0.2;
  let hi = 0.6;
  let top = 2.2;
  const at = frameIndex.value;
  for (const f of frames.value.slice(Math.max(0, at - TRAIL), at + 1)) {
    for (const r of f.rows) {
      if (r.r12 !== null) {
        lo = Math.min(lo, r.r12);
        hi = Math.max(hi, r.r12);
      }
      top = Math.max(top, r.c);
    }
  }
  const step = hi - lo > 3 ? 1 : hi - lo > 1.2 ? 0.5 : 0.2; // ticks fall on the edges
  const xmin = Math.floor((lo - 0.05) / step) * step;
  const xmax = Math.ceil((hi + 0.05) / step) * step;
  return { step, xmin: Math.round(xmin * 100) / 100, xmax: Math.round(xmax * 100) / 100,
           ymax: Math.ceil((top + 0.15) * 2) / 2 }; // on a 0.5 tick
});

function bubbleSize(share: null | number) {
  return 8 + Math.sqrt(Math.max(share ?? 0, 0) / 0.3) * 38;
}

function statLine(s: MoneyMapStats | undefined, h: string) {
  const v = s?.[h];
  if (!v || !v.n) return `${h} 周后：样本不足`;
  return `${h} 周后：相对行业平均 ${pct(v.x, 1, true)}（${pct(v.x_lose, 0)} 跑输）· 相对沪深300 ${pct(v.h, 1, true)}（${pct(v.h_lose, 0)} 跑输）· ${v.n} 次`;
}

function tooltip(row: MoneyMapRow) {
  const f = frame.value;
  if (!f) return '';
  const lines = [
    `<b>${names.value[row.code] ?? row.code}</b>　${f.week}${row.short ? '（历史不足 3 年，仅供参考）' : ''}`,
    `拥挤度 ${row.c.toFixed(2)}（60 日变化 ${row.dc60 === null ? '—' : (row.dc60 >= 0 ? '+' : '') + row.dc60.toFixed(2)}）`,
    `近 3 个月 ${pct(row.r3, 1, true)}　近 12 个月 ${pct(row.r12, 1, true)}　成交占比 ${pct(row.share, 1)}`,
    `区域：${row.zone ? rules.value?.zones[row.zone] : '—'}`,
  ];
  if (row.cell) {
    lines.push(`<span style="opacity:.75">同一格子（${cellName(row.cell)}）在 ${f.week} 之前的历史：</span>`);
    for (const h of ['13', '26']) lines.push(statLine(f.cells[row.cell], h));
  }
  if (row.zone === 'crowded') {
    lines.push('<span style="opacity:.75">历次首次升破 1.8 之后：</span>');
    for (const h of ['13', '26']) lines.push(statLine(f.entry, h));
  }
  return lines.join('<br/>');
}

function option() {
  const f = frame.value;
  const c = colors.value;
  const e = extent.value;
  const r = rules.value;
  if (!f || !r) return {};
  const at = frameIndex.value;
  const history = frames.value.slice(Math.max(0, at - TRAIL), at + 1);
  const zoneArea = (name: string, x: [number, number], y: [number, number], color: string, opacity: number) => [
    { name, xAxis: x[0], yAxis: y[0], itemStyle: { color, opacity },
      label: { color: c.ink2, fontSize: 12, position: 'insideTopLeft' } },
    { xAxis: x[1], yAxis: y[1] },
  ];
  const areas = [
    zoneArea(`必须减（拥挤度 >${r.crowded}）`, [e.xmin, e.xmax], [r.crowded, e.ymax], c.crowded, 0.08),
    zoneArea('钱在这里打架', [r.fight[2], e.xmax], [r.fight[0], r.fight[1]], c.fight, 0.08),
    zoneArea('刚开始动', [r.starting[2], r.starting[3]], [r.starting[0], r.starting[1]], c.starting, 0.1),
    zoneArea('没到时间', [e.xmin, r.quiet_return], [0, r.fight[0]], c.quiet, 0.07),
  ];
  const trails = (map.value?.industries ?? []).map((industry) => {
    const isSelected = industry.code === selected.value;
    const points = (isSelected ? frames.value.slice(Math.max(0, at - 26), at + 1) : history)
      .map((h) => h.rows.find((row) => row.code === industry.code))
      .filter((row): row is MoneyMapRow => !!row && row.r12 !== null)
      .map((row) => [row.r12, row.c]);
    return {
      type: 'line', name: `trail-${industry.code}`, data: points, silent: true, z: isSelected ? 4 : 1,
      symbol: isSelected ? 'circle' : 'none', symbolSize: 4, animation: false,
      lineStyle: { color: isSelected ? c.ink : c.muted, opacity: isSelected ? 0.9 : allTrails.value ? 0.28 : 0,
                   width: isSelected ? 2 : 1 },
      itemStyle: { color: c.ink },
    };
  });
  const bubbles = f.rows.filter((row) => row.r12 !== null).map((row) => ({
    value: [row.r12, row.c, row.share],
    name: names.value[row.code] ?? row.code,
    code: row.code,
    symbol: row.zone === 'crowded' ? 'triangle' : 'circle',
    symbolSize: bubbleSize(row.share),
    itemStyle: { color: zoneColor(row.zone), opacity: row.short ? 0.45 : selected.value && selected.value !== row.code ? 0.55 : 0.92,
                 borderColor: row.code === selected.value ? c.ink : c.surface, borderWidth: row.code === selected.value ? 2 : 1.5 },
    label: { show: true, formatter: names.value[row.code] ?? row.code, position: 'right', color: c.ink2, fontSize: 11 },
    row,
  }));
  return {
    animation: false,
    backgroundColor: 'transparent',
    grid: { left: 56, right: 24, top: 24, bottom: 48 },
    tooltip: { trigger: 'item', confine: true, formatter: (p: any) => (p.data?.row ? tooltip(p.data.row) : '') },
    xAxis: { type: 'value', min: e.xmin, max: e.xmax, interval: e.step, name: '近 12 个月涨幅', nameLocation: 'middle', nameGap: 28,
             nameTextStyle: { color: c.muted }, axisLabel: { color: c.muted, formatter: (v: number) => `${Math.round(v * 100)}%` },
             splitLine: { lineStyle: { color: c.grid } }, axisLine: { onZero: false, lineStyle: { color: c.grid } } },
    yAxis: { type: 'value', min: 0, max: e.ymax, name: '拥挤度', nameTextStyle: { color: c.muted },
             axisLine: { onZero: false, lineStyle: { color: c.grid } },
             axisLabel: { color: c.muted, formatter: (v: number) => v.toFixed(1) },
             splitLine: { lineStyle: { color: c.grid } } },
    series: [
      { type: 'scatter', name: 'zones', data: [], silent: true, markArea: { silent: true, data: areas } },
      ...trails,
      { type: 'scatter', name: 'industries', data: bubbles, z: 5, labelLayout: { hideOverlap: true },
        emphasis: { scale: 1.15, label: { show: true } } },
    ],
  };
}

async function draw(full = false) {
  if (!frame.value) return;
  if (!chart || full) {
    chart = await renderEcharts(option() as any);
    chart?.off('click');
    chart?.on('click', (event: any) => {
      if (event.seriesName === 'industries' && event.data?.code) void select(event.data.code);
    });
    return;
  }
  chart.setOption(option(), { notMerge: true });
}

async function load() {
  loading.value = true;
  try {
    map.value = await moneyMapApi(range.value === 'all' ? 0 : 156);
    frameIndex.value = frames.value.length - 1;
    await draw(true);
  } catch (error: any) {
    failed.value = error?.response?.data?.detail?.message ?? '钱去哪地图的数据暂不可用';
  } finally {
    loading.value = false;
  }
}

function stop() {
  playing.value = false;
  if (timer) clearInterval(timer);
  timer = undefined;
}

/** One week back or forward (the buttons and, in full screen, the arrow keys). */
function step(delta: number) {
  stop();
  frameIndex.value = Math.min(Math.max(frameIndex.value + delta, 0), Math.max(frames.value.length - 1, 0));
}

function onKey(event: KeyboardEvent) {
  if (!isFullscreen.value) return;
  const target = event.target as HTMLElement | null;
  if (target?.closest('input, textarea, .ant-slider')) return; // the slider moves itself with the arrows
  if (event.key === 'ArrowLeft') step(-1);
  else if (event.key === 'ArrowRight') step(1);
  else if (event.key === ' ') {
    event.preventDefault();
    play();
  }
}

function play() {
  if (playing.value) return stop();
  if (frameIndex.value >= frames.value.length - 1) frameIndex.value = 0;
  playing.value = true;
  timer = setInterval(() => {
    if (frameIndex.value >= frames.value.length - 1) return stop();
    frameIndex.value += 1;
  }, 350);
}

async function select(code: string) {
  selected.value = code;
  detail.value = await moneyMapIndustryApi(code);
  await nextTick(); // the chart's card is shown once there is a detail
  drawDetail();
  void draw();
}

function drawDetail() {
  const d = detail.value;
  const r = rules.value;
  if (!d || !r) return;
  const c = colors.value;
  const axis = (k: number) => ({ type: 'category', data: d.dates, gridIndex: k, boundaryGap: false,
                                  axisLabel: { show: k === 1, color: c.muted }, axisTick: { show: k === 1 },
                                  axisLine: { lineStyle: { color: c.grid } } });
  const week = frame.value?.week;
  void renderDetail({
    animation: false,
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
    grid: [{ left: 56, right: 56, top: 28, height: '36%' }, { left: 56, right: 56, top: '58%', height: '30%' }],
    title: [
      { text: `${d.name}：拥挤度（虚线 ${r.crowded} 为"必须减"，1.0 为常态）`, left: 56, top: 2, textStyle: { color: c.ink2, fontSize: 12, fontWeight: 'normal' } },
      { text: `${d.name} 相对沪深300（上升 = 跑赢）`, left: 56, top: '51%', textStyle: { color: c.ink2, fontSize: 12, fontWeight: 'normal' } },
    ],
    tooltip: { trigger: 'axis', formatter: (params: any) => {
      const i = (params as any[])[0]?.dataIndex ?? 0;
      return `<b>${d.dates[i]}</b><br/>拥挤度 ${d.c[i]?.toFixed(2) ?? '—'}　成交占比 ${pct(d.share[i], 1)}<br/>相对沪深300 ${d.relative[i]?.toFixed(3) ?? '—'}`;
    } },
    dataZoom: [{ type: 'inside', xAxisIndex: [0, 1] }, { type: 'slider', xAxisIndex: [0, 1], bottom: 0, height: 16 }],
    xAxis: [axis(0), axis(1)],
    yAxis: [
      { gridIndex: 0, scale: false, min: 0, splitNumber: 3, axisLabel: { color: c.muted }, splitLine: { lineStyle: { color: c.grid } } },
      { gridIndex: 1, scale: true, splitNumber: 3, axisLabel: { color: c.muted }, splitLine: { lineStyle: { color: c.grid } } },
    ],
    series: [
      { type: 'line', name: '拥挤度', data: d.c, xAxisIndex: 0, yAxisIndex: 0, showSymbol: false, color: c.fight, lineStyle: { width: 2 },
        markLine: { silent: true, symbol: 'none', data: [
          { yAxis: r.crowded, lineStyle: { color: c.crowded, type: 'dashed' }, label: { color: c.ink2, formatter: `${r.crowded}` } },
          { yAxis: 1, lineStyle: { color: c.muted, type: 'dotted' }, label: { color: c.ink2, formatter: '1.0' } },
          ...(week ? [{ xAxis: week, lineStyle: { color: c.muted }, label: { color: c.ink2, formatter: '本周' } }] : []),
        ] } },
      { type: 'line', name: '相对沪深300', data: d.relative, xAxisIndex: 1, yAxisIndex: 1, showSymbol: false, color: c.fight, lineStyle: { width: 2 },
        markLine: { silent: true, symbol: 'none', data: [{ yAxis: 1, lineStyle: { color: c.muted, type: 'dotted' }, label: { color: c.ink2 } }] } },
    ],
  } as any);
}

/** The side lists of the shown week. */
const groups = computed(() => {
  const rows = (frame.value?.rows ?? []).filter((row) => row.r12 !== null);
  const by = (zone: string) => rows.filter((row) => row.zone === zone);
  return [
    { key: 'crowded', title: '该减：拥挤度超过 1.8', rows: by('crowded').sort((a, b) => b.c - a.c),
      note: '历史上首次升破 1.8 之后多数跑输；用来提示减仓、不追，不是清仓指令。', stats: frame.value?.entry },
    { key: 'fight', title: '值得看：钱在这里打架', rows: by('fight').sort((a, b) => (b.r12 ?? 0) - (a.r12 ?? 0)),
      note: '涨幅已起来、交易热度中等的行业，历史上之后平均跑赢；去里面挑股票，不是直接买行业。', stats: frame.value?.zones.fight },
    { key: 'starting', title: '刚开始动', rows: by('starting').sort((a, b) => (b.r12 ?? 0) - (a.r12 ?? 0)),
      note: '交易还冷、涨幅刚起来。', stats: frame.value?.zones.starting },
    { key: 'rest', title: '其余行业', rows: rows.filter((row) => !['crowded', 'fight', 'starting'].includes(row.zone ?? ''))
      .sort((a, b) => b.c - a.c), note: '没到时间（冷、没涨）和其他位置。', stats: undefined },
  ];
});

const cellColumns = [
  { key: 'cell', title: '格子', width: 210 },
  { key: 'now', title: '本周在格内' },
  { key: 'n13', title: '样本', align: 'right', width: 70 },
  { key: 'x13', title: '13 周后 vs 行业平均', align: 'right', width: 170 },
  { key: 'h13', title: '13 周后 vs 沪深300', align: 'right', width: 170 },
  { key: 'x26', title: '26 周后 vs 行业平均', align: 'right', width: 170 },
] as const;
const cellRows = computed(() => {
  const f = frame.value;
  if (!f) return [];
  return Object.entries(f.cells).map(([cell, stats]) => ({
    cell, stats, now: f.rows.filter((row) => row.cell === cell).map((row) => names.value[row.code]).join('、'),
  })).sort((a, b) => (rules.value?.cells.indexOf(a.cell) ?? 0) - (rules.value?.cells.indexOf(b.cell) ?? 0));
});
const statText = (s: MoneyMapStats | undefined, h: string, k: 'h' | 'x') => {
  const v = s?.[h];
  if (!v || !v.n) return '—';
  return `${pct(v[k], 1, true)} · 跑输 ${pct(v[`${k}_lose` as 'h_lose' | 'x_lose'], 0)}`;
};
const zoneTag = (zone: null | string) => (zone ? rules.value?.zones[zone] : '—');
/** "C 0.8–1.4|<15%" as 拥挤度 0.8–1.4 · 涨幅 <15%. */
const cellName = (cell: string) => cell.replace(/^C\s?/, '拥挤度 ').replace('|', ' · 涨幅 ');

watch(frameIndex, () => {
  void draw();
});
watch([isDark, allTrails], () => {
  void draw(true);
  drawDetail();
});
watch(range, () => {
  stop();
  void load();
});
onMounted(() => {
  window.addEventListener('keydown', onKey);
  void load();
});
onBeforeUnmount(() => {
  stop();
  window.removeEventListener('keydown', onKey);
});
</script>

<template>
  <Page title="钱去哪地图" :description="map ? `申万一级 31 个行业的交易拥挤度与近 12 个月涨幅，按周回放；数据截至 ${map.as_of}` : ''">
    <Alert v-if="failed" type="warning" show-icon :message="failed" />
    <template v-if="map">
      <Row :gutter="[12, 12]">
        <Col :xs="24" :xl="17">
          <div ref="mapBox" :class="{ 'bg-background h-full overflow-auto p-3': isFullscreen }">
          <Card size="small">
            <div class="mb-2 flex flex-wrap items-center gap-x-3 gap-y-2">
              <Button type="primary" size="small" @click="play">{{ playing ? '暂停' : '回放' }}</Button>
              <Button size="small" :disabled="frameIndex <= 0" title="上一周（全屏时也可按 ←）" @click="step(-1)">‹ 上一周</Button>
              <Slider v-model:value="frameIndex" class="!mx-1 min-w-[220px] flex-1" :min="0" :max="Math.max(frames.length - 1, 0)"
                      :tip-formatter="(i?: number) => frames[i ?? 0]?.week" :get-tooltip-popup-container="popupBox"
                      @change="stop" />
              <Button size="small" :disabled="frameIndex >= frames.length - 1" title="下一周（全屏时也可按 →）" @click="step(1)">下一周 ›</Button>
              <span class="min-w-[140px] font-semibold">{{ frame?.week }} 这一周</span>
              <Radio.Group v-model:value="range" size="small" button-style="solid">
                <Radio.Button value="recent">近 3 年</Radio.Button>
                <Radio.Button value="all">全部（{{ map.weeks_total }} 周）</Radio.Button>
              </Radio.Group>
              <span class="text-xs">尾迹 <Switch v-model:checked="allTrails" size="small" /></span>
              <Button size="small" @click="toggleFullscreen">{{ isFullscreen ? '退出全屏' : '全屏' }}</Button>
            </div>
            <Spin :spinning="loading">
              <EchartsUI ref="chartRef" :height="isFullscreen ? 'calc(100vh - 120px)' : '620px'" />
            </Spin>
            <div v-if="isFullscreen" class="text-muted-foreground mt-1 text-xs">
              ← → 换一周，空格回放或暂停，Esc 退出全屏；点击气泡会高亮它的轨迹，行业走势图在退出全屏后查看。
            </div>
            <div v-else class="text-muted-foreground mt-1 text-xs leading-5">
              气泡 = 行业，大小按成交占比；三角形是"必须减"区的行业；淡色是拥挤度基准不足 3 年的行业（不计入历史统计）。
              灰线是近 {{ TRAIL }} 周的轨迹，点击气泡或右侧名称看该行业的完整走势。
            </div>
          </Card>
          </div>
          <Card size="small" class="mt-3">
            <template #title>{{ detail ? detail.name : '行业走势' }}</template>
            <template #extra>
              <Space v-if="detail">
                <span class="text-xs">本周：{{ zoneTag(frame?.rows.find((r) => r.code === selected)?.zone ?? null) }}</span>
                <span v-if="detail.entries.length" class="text-xs">历次首次升破 1.8：{{ detail.entries.slice(-6).join('、') }}</span>
              </Space>
            </template>
            <Empty v-if="!detail" :image="Empty.PRESENTED_IMAGE_SIMPLE" description="点击地图上的气泡或右侧的行业名称" />
            <EchartsUI v-show="detail" ref="detailRef" height="360px" />
          </Card>
        </Col>
        <Col :xs="24" :xl="7">
          <Card v-for="g in groups" :key="g.key" size="small" class="mb-3" :title="`${g.title}（${g.rows.length}）`">
            <div class="text-muted-foreground mb-2 text-xs">{{ g.note }}</div>
            <div v-if="g.stats" class="text-muted-foreground mb-2 text-xs leading-5">
              历史：13 周后相对行业平均 {{ statText(g.stats, '13', 'x') }}；26 周后 {{ statText(g.stats, '26', 'x') }}
            </div>
            <Empty v-if="!g.rows.length" :image="Empty.PRESENTED_IMAGE_SIMPLE" description="本周没有" />
            <div :class="{ 'max-h-[300px] overflow-y-auto': g.key === 'rest' }">
            <div v-for="row in g.rows" :key="row.code"
                 class="hover:bg-accent flex cursor-pointer items-center justify-between rounded px-1 py-0.5 text-sm"
                 :class="{ 'font-semibold': row.code === selected }" @click="select(row.code)">
              <span>{{ names[row.code] }}<Tag v-if="row.short" class="ml-1" color="default">历史短</Tag></span>
              <span class="tabular-nums">C {{ row.c.toFixed(2) }} · {{ pct(row.r12, 0, true) }}</span>
            </div>
            </div>
          </Card>
        </Col>
      </Row>


      <Card size="small" class="mt-3" :title="`历史统计（截至 ${frame?.week}，只用当时已能知道的数据）`">
        <Table size="small" :pagination="false" :columns="cellColumns as any" :data-source="cellRows" row-key="cell">
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'cell'">{{ cellName(record.cell) }}</template>
            <template v-else-if="column.key === 'now'">{{ record.now }}</template>
            <template v-else-if="column.key === 'n13'">{{ record.stats['13']?.n ?? 0 }}</template>
            <template v-else-if="column.key === 'x13'">{{ statText(record.stats, '13', 'x') }}</template>
            <template v-else-if="column.key === 'h13'">{{ statText(record.stats, '13', 'h') }}</template>
            <template v-else-if="column.key === 'x26'">{{ statText(record.stats, '26', 'x') }}</template>
          </template>
        </Table>
        <div class="text-muted-foreground mt-2 text-xs leading-5">
          <Tooltip title="成交占比：行业指数成交额占 31 个行业合计的比例。">
            <span class="underline decoration-dotted">拥挤度</span>
          </Tooltip>
          = 行业近 {{ map.rules.short }} 个交易日的成交占比 ÷ 它近 {{ map.rules.long }} 个交易日（约 3 年）的平均。
          高于 1 说明这个行业比平时交易得更热；它衡量交易集中在哪里，不是资金净流入（每笔成交都有买有卖）。
          区域边界照搬自主播的划分，没有为回测结果调过。
          统计：每周每个行业算一个样本，按"拥挤度 × 12 个月涨幅"分格，看之后 13 周、26 周相对 31 个行业平均和相对沪深300 的超额收益；
          相邻几周的样本看的是同一段行情，实际独立的样本比表里的次数少。数据：申万宏源官网的申万一级行业指数，{{ map.start }} 起。
        </div>
      </Card>
    </template>
  </Page>
</template>
