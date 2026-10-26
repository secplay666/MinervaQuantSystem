<script setup lang="ts">
import type { EChartsOption } from 'echarts';

import type { MoneyMap, MoneyMapFrame, MoneyMapIndustry, MoneyMapRow, MoneyMapStats } from './info-api';

import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { useRouter } from 'vue-router';

import { showFailToast } from 'vant';

import { request } from '../../api';
import EChart from '../../components/EChart.vue';
import { pct } from '../../format';
import { useQueryPopup } from './info-api';

const router = useRouter();
const map = ref<MoneyMap>();
const failed = ref('');
const loading = ref(false);
const range = ref<'all' | 'recent'>('recent');
const view = ref<'list' | 'map'>('map');
const frameIndex = ref(0);
const playing = ref(false);
const allTrails = ref(true);
const highlight = ref<string>(); // the last opened industry: its trail stays highlighted on the map
const details = ref<Record<string, MoneyMapIndustry>>({});
const detailLoading = ref(false);
const popup = useQueryPopup('industry');
let timer: ReturnType<typeof setInterval> | undefined;

/* Colour roles of the PC page (validated; 必须减 is also a triangle).  Zones without a signal stay gray. */
const C = { crowded: '#d03b3b', fight: '#2a78d6', grid: '#e1e0d9', ink: '#0b0b0b', ink2: '#52514e', muted: '#898781',
            quiet: '#898781', starting: '#1baf7a', surface: '#ffffff' };
const zoneColor = (zone: null | string) =>
  zone === 'crowded' ? C.crowded : zone === 'fight' ? C.fight : zone === 'starting' ? C.starting : C.quiet;
const LEGEND = [['必须减', C.crowded, true], ['钱在这里打架', C.fight, false], ['刚开始动', C.starting, false], ['其他', C.quiet, false]] as const;
const TRAIL = 8; // weeks of trail behind each bubble

const names = computed<Record<string, string>>(() => Object.fromEntries((map.value?.industries ?? []).map((i) => [i.code, i.name])));
const frames = computed(() => map.value?.frames ?? []);
const frame = computed<MoneyMapFrame | undefined>(() => frames.value[frameIndex.value]);
const rules = computed(() => map.value?.rules);
const selected = computed(() => popup.value.value);
const selectedRow = computed(() => frame.value?.rows.find((r) => r.code === selected.value));
const detail = computed(() => (selected.value ? details.value[selected.value] : undefined));

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
  const step = hi - lo > 3 ? 1 : hi - lo > 1.2 ? 0.5 : 0.2;
  const xmin = Math.floor((lo - 0.05) / step) * step;
  const xmax = Math.ceil((hi + 0.05) / step) * step;
  return { step, xmax: Math.round(xmax * 100) / 100, xmin: Math.round(xmin * 100) / 100, ymax: Math.ceil((top + 0.15) * 2) / 2 };
});

function statLine(s: MoneyMapStats | undefined, h: string) {
  const v = s?.[h];
  if (!v || !v.n) return `${h} 周后：样本不足`;
  return `${h} 周后：相对行业平均 ${pct(v.x, 1, true)}（${pct(v.x_lose, 0)} 跑输）· 相对沪深300 ${pct(v.h, 1, true)}（${pct(v.h_lose, 0)} 跑输）· ${v.n} 次`;
}
const statText = (s: MoneyMapStats | undefined, h: string, k: 'h' | 'x') => {
  const v = s?.[h];
  if (!v || !v.n) return '—';
  return `${pct(v[k], 1, true)} · 跑输 ${pct(v[`${k}_lose` as 'h_lose' | 'x_lose'], 0)}`;
};
const zoneTag = (zone: null | string | undefined) => (zone ? rules.value?.zones[zone] : '—');
/** "C 0.8–1.4|<15%" as 拥挤度 0.8–1.4 · 涨幅 <15%. */
const cellName = (cell: string) => cell.replace(/^C\s?/, '拥挤度 ').replace('|', ' · 涨幅 ');
const signed2 = (v: null | number) => (v === null ? '—' : `${v >= 0 ? '+' : ''}${v.toFixed(2)}`);

const option = computed<EChartsOption>(() => {
  const f = frame.value;
  const e = extent.value;
  const r = rules.value;
  if (!f || !r) return {};
  const at = frameIndex.value;
  const history = frames.value.slice(Math.max(0, at - TRAIL), at + 1);
  const zoneArea = (name: string, x: [number, number], y: [number, number], color: string, opacity: number) => [
    { itemStyle: { color, opacity }, label: { color: C.ink2, fontSize: 10, position: 'insideTopLeft' }, name, xAxis: x[0], yAxis: y[0] },
    { xAxis: x[1], yAxis: y[1] },
  ];
  const areas = [
    zoneArea(`必须减（拥挤度 >${r.crowded}）`, [e.xmin, e.xmax], [r.crowded, e.ymax], C.crowded, 0.08),
    zoneArea('钱在这里打架', [r.fight[2], e.xmax], [r.fight[0], r.fight[1]], C.fight, 0.08),
    zoneArea('刚开始动', [r.starting[2], r.starting[3]], [r.starting[0], r.starting[1]], C.starting, 0.1),
    zoneArea('没到时间', [e.xmin, r.quiet_return], [0, r.fight[0]], C.quiet, 0.07),
  ];
  const trails = (map.value?.industries ?? []).map((industry) => {
    const isSelected = industry.code === highlight.value;
    const points = (isSelected ? frames.value.slice(Math.max(0, at - 26), at + 1) : history)
      .map((h) => h.rows.find((row) => row.code === industry.code))
      .filter((row): row is MoneyMapRow => !!row && row.r12 !== null)
      .map((row) => [row.r12, row.c]);
    return {
      animation: false, data: points, itemStyle: { color: C.ink }, name: `trail-${industry.code}`, silent: true,
      lineStyle: { color: isSelected ? C.ink : C.muted, opacity: isSelected ? 0.9 : allTrails.value ? 0.28 : 0, width: isSelected ? 2 : 1 },
      symbol: isSelected ? 'circle' : 'none', symbolSize: 4, type: 'line' as const, z: isSelected ? 4 : 1,
    };
  });
  const bubbles = f.rows.filter((row) => row.r12 !== null).map((row) => ({
    code: row.code,
    itemStyle: { borderColor: row.code === highlight.value ? C.ink : C.surface, borderWidth: row.code === highlight.value ? 2 : 1.5,
                 color: zoneColor(row.zone), opacity: row.short ? 0.45 : highlight.value && highlight.value !== row.code ? 0.6 : 0.92 },
    label: { color: C.ink2, fontSize: 10, formatter: names.value[row.code] ?? row.code, position: 'right' as const, show: true },
    name: names.value[row.code] ?? row.code,
    symbol: row.zone === 'crowded' ? 'triangle' : 'circle',
    symbolSize: 6 + Math.sqrt(Math.max(row.share ?? 0, 0) / 0.3) * 26,
    value: [row.r12, row.c, row.share],
  }));
  return {
    animation: false,
    grid: { bottom: 40, left: 40, right: 12, top: 24 },
    series: [
      { data: [], markArea: { data: areas, silent: true }, name: 'zones', silent: true, type: 'scatter' },
      ...trails,
      { data: bubbles, emphasis: { scale: 1.15 }, labelLayout: { hideOverlap: true }, name: 'industries', type: 'scatter', z: 5 },
    ],
    tooltip: { show: false }, // a tap opens the industry instead
    xAxis: { axisLabel: { color: C.muted, fontSize: 10, formatter: (v: number) => `${Math.round(v * 100)}%` },
             axisLine: { lineStyle: { color: C.grid }, onZero: false }, interval: e.step, max: e.xmax, min: e.xmin,
             name: '近 12 个月涨幅', nameGap: 24, nameLocation: 'middle', nameTextStyle: { color: C.muted, fontSize: 11 },
             splitLine: { lineStyle: { color: C.grid } }, type: 'value' },
    yAxis: { axisLabel: { color: C.muted, fontSize: 10, formatter: (v: number) => v.toFixed(1) },
             axisLine: { lineStyle: { color: C.grid }, onZero: false }, max: e.ymax, min: 0, name: '拥挤度',
             nameTextStyle: { color: C.muted, fontSize: 11 }, splitLine: { lineStyle: { color: C.grid } }, type: 'value' },
  } as EChartsOption;
});

const detailOption = computed<EChartsOption>(() => {
  const d = detail.value;
  const r = rules.value;
  if (!d || !r) return {};
  const axis = (k: number) => ({ axisLabel: { color: C.muted, fontSize: 10, show: k === 1 }, axisLine: { lineStyle: { color: C.grid } },
                                  axisTick: { show: k === 1 }, boundaryGap: false, data: d.dates, gridIndex: k, type: 'category' as const });
  const week = frame.value?.week;
  const title = (text: string, top: number) => ({ left: 4, text, textStyle: { color: C.ink2, fontSize: 11, fontWeight: 'normal' as const }, top });
  return {
    animation: false,
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
    dataZoom: [{ bottom: 4, height: 18, type: 'slider', xAxisIndex: [0, 1] }],
    grid: [{ height: 120, left: 40, right: 12, top: 24 }, { height: 96, left: 40, right: 12, top: 186 }],
    series: [
      { data: d.c, itemStyle: { color: C.fight }, lineStyle: { width: 2 }, name: '拥挤度', showSymbol: false, type: 'line', xAxisIndex: 0, yAxisIndex: 0,
        markLine: { silent: true, symbol: 'none', data: [
          { label: { color: C.ink2, fontSize: 10, formatter: `${r.crowded}` }, lineStyle: { color: C.crowded, type: 'dashed' }, yAxis: r.crowded },
          { label: { color: C.ink2, fontSize: 10, formatter: '1.0' }, lineStyle: { color: C.muted, type: 'dotted' }, yAxis: 1 },
          ...(week ? [{ label: { color: C.ink2, fontSize: 10, formatter: '本周' }, lineStyle: { color: C.muted }, xAxis: week }] : []),
        ] } },
      { data: d.relative, itemStyle: { color: C.fight }, lineStyle: { width: 2 }, name: '相对沪深300', showSymbol: false, type: 'line', xAxisIndex: 1, yAxisIndex: 1,
        markLine: { data: [{ label: { color: C.ink2, fontSize: 10 }, lineStyle: { color: C.muted, type: 'dotted' }, yAxis: 1 }], silent: true, symbol: 'none' } },
    ],
    title: [title(`拥挤度（虚线 ${r.crowded} 为"必须减"，1.0 为常态）`, 0), title('相对沪深300（上升 = 跑赢）', 162)],
    tooltip: {
      confine: true,
      formatter: (params: any) => {
        const i = (params as any[])[0]?.dataIndex ?? 0;
        return `<b>${d.dates[i]}</b><br/>拥挤度 ${d.c[i]?.toFixed(2) ?? '—'}　成交占比 ${pct(d.share[i], 1)}<br/>相对沪深300 ${d.relative[i]?.toFixed(3) ?? '—'}`;
      },
      textStyle: { fontSize: 12 },
      trigger: 'axis',
    },
    xAxis: [axis(0), axis(1)],
    yAxis: [
      { axisLabel: { color: C.muted, fontSize: 10 }, gridIndex: 0, min: 0, splitLine: { lineStyle: { color: C.grid } }, splitNumber: 3 },
      { axisLabel: { color: C.muted, fontSize: 10 }, gridIndex: 1, scale: true, splitLine: { lineStyle: { color: C.grid } }, splitNumber: 3 },
    ],
  } as EChartsOption;
});

/** The zone lists of the shown week. */
const groups = computed(() => {
  const rows = (frame.value?.rows ?? []).filter((row) => row.r12 !== null);
  const by = (zone: string) => rows.filter((row) => row.zone === zone);
  return [
    { key: 'crowded', note: '历史上首次升破 1.8 之后多数跑输；用来提示减仓、不追，不是清仓指令。',
      rows: by('crowded').sort((a, b) => b.c - a.c), stats: frame.value?.entry, title: '该减：拥挤度超过 1.8' },
    { key: 'fight', note: '涨幅已起来、交易热度中等的行业，历史上之后平均跑赢；去里面挑股票，不是直接买行业。',
      rows: by('fight').sort((a, b) => (b.r12 ?? 0) - (a.r12 ?? 0)), stats: frame.value?.zones.fight, title: '值得看：钱在这里打架' },
    { key: 'starting', note: '交易还冷、涨幅刚起来。', rows: by('starting').sort((a, b) => (b.r12 ?? 0) - (a.r12 ?? 0)),
      stats: frame.value?.zones.starting, title: '刚开始动' },
    { key: 'rest', note: '没到时间（冷、没涨）和其他位置。', stats: undefined, title: '其余行业',
      rows: rows.filter((row) => !['crowded', 'fight', 'starting'].includes(row.zone ?? '')).sort((a, b) => b.c - a.c) },
  ];
});

const cellRows = computed(() => {
  const f = frame.value;
  if (!f) return [];
  return Object.entries(f.cells).map(([cell, stats]) => ({
    cell, now: f.rows.filter((row) => row.cell === cell).map((row) => names.value[row.code]).join('、'), stats,
  })).sort((a, b) => (rules.value?.cells.indexOf(a.cell) ?? 0) - (rules.value?.cells.indexOf(b.cell) ?? 0));
});

async function load() {
  loading.value = true;
  try {
    map.value = await request<MoneyMap>(`/moneymap?weeks=${range.value === 'all' ? 0 : 156}`);
    frameIndex.value = Math.max(frames.value.length - 1, 0);
    failed.value = '';
  } catch (error: any) {
    failed.value = error.message;
  } finally {
    loading.value = false;
  }
}

function stop() {
  playing.value = false;
  if (timer) clearInterval(timer);
  timer = undefined;
}

function step(delta: number) {
  stop();
  frameIndex.value = Math.min(Math.max(frameIndex.value + delta, 0), Math.max(frames.value.length - 1, 0));
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

function select(code: string) {
  stop();
  highlight.value = code;
  popup.open(code);
}

function onChartClick(event: any) {
  if (event.seriesName === 'industries' && event.data?.code) select(event.data.code);
}

watch(selected, async (code) => {
  if (!code) return;
  highlight.value = code;
  if (details.value[code]) return;
  detailLoading.value = true;
  try {
    details.value = { ...details.value, [code]: await request<MoneyMapIndustry>(`/moneymap/industries/${code}`) };
  } catch (error: any) {
    showFailToast(error.message);
  } finally {
    detailLoading.value = false;
  }
}, { immediate: true });
watch(range, () => {
  stop();
  void load();
});
onMounted(load);
onBeforeUnmount(stop);
</script>

<template>
  <van-nav-bar title="钱去哪地图" left-arrow @click-left="router.back()" />
  <van-empty v-if="failed && !map" :description="failed" />
  <van-skeleton v-else-if="!map" :row="10" style="margin-top: 16px" />
  <template v-else>
    <div class="note" style="margin-top: 12px">申万一级 31 个行业的交易拥挤度与近 12 个月涨幅，按周回放；数据截至 {{ map.as_of }}</div>
    <div class="card">
      <div class="mm-week">
        <van-button size="small" icon="arrow-left" :disabled="frameIndex <= 0" class="mm-btn" @click="step(-1)" />
        <div class="mm-week-title">{{ frame?.week }} 这一周</div>
        <van-button size="small" icon="arrow" :disabled="frameIndex >= frames.length - 1" class="mm-btn" @click="step(1)" />
        <van-button size="small" type="primary" class="mm-btn" @click="play">{{ playing ? '暂停' : '回放' }}</van-button>
      </div>
      <van-slider v-model="frameIndex" :min="0" :max="Math.max(frames.length - 1, 0)" style="margin: 18px 10px" @drag-start="stop" @change="stop" />
      <div class="mm-row">
        <span>
          <van-button size="small" class="mm-btn" :type="range === 'recent' ? 'primary' : 'default'" :loading="loading && range === 'recent'"
                      @click="range = 'recent'">近 3 年</van-button>
          <van-button size="small" class="mm-btn" :type="range === 'all' ? 'primary' : 'default'" :loading="loading && range === 'all'"
                      style="margin-left: 6px" @click="range = 'all'">全部（{{ map.weeks_total }} 周）</van-button>
        </span>
        <span v-if="view === 'map'" class="muted mm-switch">尾迹 <van-switch v-model="allTrails" size="18px" /></span>
      </div>
    </div>

    <van-tabs v-model:active="view" style="margin: 0 12px">
      <van-tab name="map" title="地图" />
      <van-tab name="list" title="按区域列表" />
    </van-tabs>

    <div v-if="view === 'map'" class="card">
      <EChart :option="option" :height="380" @click="onChartClick" />
      <div class="mm-legend">
        <span v-for="[label, colour, triangle] in LEGEND" :key="label">
          <i :class="triangle ? 'mm-triangle' : 'mm-dot'" :style="triangle ? { borderBottomColor: colour } : { background: colour }"></i>{{ label }}
        </span>
      </div>
      <div class="muted" style="line-height: 18px">
        气泡 = 行业，大小按成交占比；三角形是"必须减"区的行业；淡色是拥挤度基准不足 3 年的行业（不计入历史统计）。
        灰线是近 {{ TRAIL }} 周的轨迹。点气泡看该行业的数值、历史统计和完整走势；气泡太挤时切到"按区域列表"。
      </div>
    </div>

    <template v-else>
      <van-cell-group v-for="g in groups" :key="g.key" inset :title="`${g.title}（${g.rows.length}）`">
        <div class="mm-gnote">
          {{ g.note }}
          <div v-if="g.stats">历史：13 周后相对行业平均 {{ statText(g.stats, '13', 'x') }}；26 周后 {{ statText(g.stats, '26', 'x') }}</div>
        </div>
        <van-cell v-for="row in g.rows" :key="row.code" is-link :value="`C ${row.c.toFixed(2)} · ${pct(row.r12, 0, true)}`"
                  @click="select(row.code)">
          <template #title>
            <span :style="{ fontWeight: row.code === highlight ? 600 : undefined }">{{ names[row.code] }}</span>
            <van-tag v-if="row.short" plain style="margin-left: 4px">历史短</van-tag>
          </template>
        </van-cell>
        <van-cell v-if="!g.rows.length" title="本周没有" />
      </van-cell-group>
    </template>

    <div class="card">
      <div class="card-title">历史统计（截至 {{ frame?.week }}，只用当时已能知道的数据）</div>
      <div class="table-wrap">
        <table class="table">
          <thead>
            <tr><th>格子 / 本周在格内</th><th>样本</th><th>13 周后 vs 行业平均</th><th>13 周后 vs 沪深300</th><th>26 周后 vs 行业平均</th></tr>
          </thead>
          <tbody>
            <tr v-for="row in cellRows" :key="row.cell">
              <td class="mm-cell">{{ cellName(row.cell) }}<div v-if="row.now" class="muted">{{ row.now }}</div></td>
              <td>{{ row.stats['13']?.n ?? 0 }}</td>
              <td>{{ statText(row.stats, '13', 'x') }}</td>
              <td>{{ statText(row.stats, '13', 'h') }}</td>
              <td>{{ statText(row.stats, '26', 'x') }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
    <div class="note">
      拥挤度 = 行业近 {{ map.rules.short }} 个交易日的成交占比 ÷ 它近 {{ map.rules.long }} 个交易日（约 3 年）的平均（成交占比：行业指数成交额占 31 个行业合计的比例）。
      高于 1 说明这个行业比平时交易得更热；它衡量交易集中在哪里，不是资金净流入（每笔成交都有买有卖）。
      区域边界照搬自主播的划分，没有为回测结果调过。
      统计：每周每个行业算一个样本，按"拥挤度 × 12 个月涨幅"分格，看之后 13 周、26 周相对 31 个行业平均和相对沪深300 的超额收益；
      相邻几周的样本看的是同一段行情，实际独立的样本比表里的次数少。数据：申万宏源官网的申万一级行业指数，{{ map.start }} 起。
    </div>
  </template>

  <van-popup :show="!!selected" position="bottom" round closeable :style="{ height: '88%' }"
             @update:show="(show: boolean) => !show && popup.close()">
    <div v-if="selected" class="mm-popup">
      <div class="mm-popup-title">{{ names[selected] ?? selected }}</div>
      <template v-if="selectedRow && frame">
        <div class="muted">{{ frame.week }} 这一周{{ selectedRow.short ? '（历史不足 3 年，仅供参考）' : '' }}</div>
        <van-cell-group :border="false" style="margin: 4px -16px 0">
          <van-cell title="区域" :value="zoneTag(selectedRow.zone)" />
          <van-cell title="拥挤度" :value="`${selectedRow.c.toFixed(2)}（60 日变化 ${signed2(selectedRow.dc60)}）`" />
          <van-cell title="近 3 个月 / 近 12 个月" :value="`${pct(selectedRow.r3, 1, true)} / ${pct(selectedRow.r12, 1, true)}`" />
          <van-cell title="成交占比" :value="pct(selectedRow.share, 1)" />
        </van-cell-group>
        <div v-if="selectedRow.cell" class="mm-stats">
          <div class="muted">同一格子（{{ cellName(selectedRow.cell) }}）在 {{ frame.week }} 之前的历史：</div>
          <div v-for="h in ['13', '26']" :key="h">{{ statLine(frame.cells[selectedRow.cell], h) }}</div>
        </div>
        <div v-if="selectedRow.zone === 'crowded'" class="mm-stats">
          <div class="muted">历次首次升破 1.8 之后：</div>
          <div v-for="h in ['13', '26']" :key="h">{{ statLine(frame.entry, h) }}</div>
        </div>
      </template>
      <div v-else class="muted">这一周没有它的数据</div>
      <div class="card-title" style="margin-top: 12px">行业走势</div>
      <van-loading v-if="detailLoading && !detail" style="padding: 32px 0; text-align: center" />
      <template v-else-if="detail">
        <EChart :option="detailOption" :height="320" />
        <div v-if="detail.entries.length" class="muted" style="margin-top: 6px">历次首次升破 1.8：{{ detail.entries.slice(-6).join('、') }}</div>
      </template>
    </div>
  </van-popup>
</template>

<style scoped>
.mm-week { display: flex; align-items: center; gap: 6px; }
.mm-week-title { flex: 1; font-weight: 600; text-align: center; }
.mm-btn { height: 36px; min-width: 40px; }
.mm-row { display: flex; align-items: center; justify-content: space-between; }
.mm-switch { display: flex; align-items: center; gap: 4px; }
.mm-legend { display: flex; flex-wrap: wrap; gap: 4px 12px; margin: 6px 0; font-size: 12px; color: #52514e; }
.mm-dot { display: inline-block; width: 9px; height: 9px; margin-right: 4px; border-radius: 50%; }
.mm-triangle { display: inline-block; width: 0; height: 0; margin-right: 4px; border: 5px solid transparent; border-top: 0; border-bottom-width: 9px; border-bottom-style: solid; }
.mm-gnote { padding: 8px 16px; color: #969799; font-size: 12px; line-height: 18px; }
.mm-cell { min-width: 120px; max-width: 150px; white-space: normal; }
.mm-popup { padding: 16px; }
.mm-popup-title { margin-bottom: 4px; padding-right: 32px; font-size: 16px; font-weight: 600; }
.mm-stats { margin-top: 8px; font-size: 13px; line-height: 20px; }
</style>
