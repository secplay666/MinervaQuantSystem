<script setup lang="ts">
import type { EChartsOption } from 'echarts';

import type { PmJob, PmSettings } from './pm-api';

import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue';
import { useRouter } from 'vue-router';

import { showFailToast } from 'vant';

import { post, request } from '../../api';
import EChart from '../../components/EChart.vue';
import { color, pct } from '../../format';
import DateField from './DateField.vue';
import './pm.css';

const EDITABLE = [
  { key: 'ma', name: '均线周期', pct: false }, { key: 'slope', name: '斜率回看', pct: false },
  { key: 'flat', name: '个股走平阈值', pct: true }, { key: 'index_flat', name: '指数走平阈值', pct: true },
  { key: 'confirm', name: '确认天数', pct: false },
];
const BOOKS = [
  ['market', '全市场等权（基准）', '基准', '#8c8c8c'], ['stage2', '持有第二阶段（右侧）个股', '右侧', '#2a78d6'],
  ['stage2_gated', '同上，且沪深300 在右侧', '加闸门', '#eb6834'],
] as const;
const TABLES = [['stages', '各阶段之后的表现', '阶段'], ['transitions', '阶段切换之后', '切换'],
  ['gate', '筑底→右侧时的大盘闸门（沪深300 所处阶段）', '沪深300'], ['filters', '筑底→右侧的过滤条件', '条件']] as const;
const STAGE_SHORT: Record<string, string> = { advance: '右', base: '底', decline: '左', top: '顶' };

const router = useRouter();
const settings = ref<PmSettings>();
const form = reactive<{ end?: string; preset: string; start?: string; values: Record<string, string> }>({ preset: 'steady', values: {} });
const job = ref<PmJob>();
const starting = ref(false);
const busy = computed(() => job.value?.status === 'queued' || job.value?.status === 'running');
const result = computed(() => (job.value?.status === 'done' ? job.value.result : null) as null | Record<string, any>);
const presetValues = computed(() => settings.value?.presets.find((p) => p.key === form.preset)?.params ?? {});
let timer: ReturnType<typeof setTimeout> | undefined;

const shown = (f: (typeof EDITABLE)[number], v?: number) => (v === undefined ? '' : String(f.pct ? Math.round(v * 10_000) / 100 : v));
const parsed = (f: (typeof EDITABLE)[number], s?: string) =>
  (s === undefined || s === '' || !Number.isFinite(Number(s)) ? undefined : f.pct ? Number(s) / 100 : Number(s));

/** Growth of 1 compounded from the yearly returns of each book. */
const chart = computed<EChartsOption | null>(() => {
  const yearly = result.value?.yearly as Record<string, any>[] | undefined;
  if (!yearly?.length) return null;
  const years = yearly.map((y) => String(y.year));
  const series = BOOKS.map(([key, , short, hue]) => {
    let wealth = 1;
    return {
      data: yearly.map((y) => (wealth *= 1 + (y[key] ?? 0))).map((v) => Math.round(v * 1000) / 1000),
      endLabel: { color: '#646566', fontSize: 11, formatter: short, show: true },
      itemStyle: { color: hue }, lineStyle: { color: hue, type: (key === 'market' ? 'dashed' : 'solid') as 'dashed' | 'solid', width: 2 },
      name: short, showSymbol: false, type: 'line' as const,
    };
  });
  return {
    grid: { bottom: 24, containLabel: true, left: 8, right: 48, top: 36 },
    legend: { itemHeight: 8, itemWidth: 14, textStyle: { color: '#646566', fontSize: 11 }, top: 0 },
    series,
    tooltip: { trigger: 'axis', valueFormatter: (v: any) => Number(v).toFixed(2) },
    xAxis: { axisLabel: { color: '#969799', fontSize: 10 }, axisLine: { lineStyle: { color: '#dcdee0' } }, boundaryGap: false, data: years, type: 'category' },
    yAxis: { axisLabel: { color: '#969799', fontSize: 10 }, scale: true, splitLine: { lineStyle: { color: '#f2f3f5' } }, type: 'value' },
  };
});

function choosePreset(key: string) {
  form.preset = key;
  const params = settings.value?.presets.find((p) => p.key === key)?.params ?? {};
  form.values = Object.fromEntries(EDITABLE.map((f) => [f.key, shown(f, params[f.key])]));
}

async function poll() {
  if (!job.value) return;
  try {
    job.value = await request<PmJob>(`/pm/jobs/${job.value.id}`);
  } catch (error: any) {
    showFailToast(error.message);
    return;
  }
  if (busy.value) timer = setTimeout(poll, 1500);
}

async function run() {
  const params = Object.fromEntries(EDITABLE.map((f) => [f.key, parsed(f, form.values[f.key])] as const)
    .filter(([key, v]) => v !== undefined && Math.abs(v - (presetValues.value[key] ?? 0)) > 1e-9));
  starting.value = true;
  try {
    job.value = await post<PmJob>('/pm/stage-backtest', { end: form.end, params, preset: form.preset, start: form.start });
  } catch (error: any) {
    showFailToast(error.message);
    return;
  } finally {
    starting.value = false;
  }
  clearTimeout(timer);
  if (busy.value) timer = setTimeout(poll, 1500);
}

onMounted(async () => {
  try {
    settings.value = await request<PmSettings>('/pm/settings');
    choosePreset(settings.value.stage_preset);
  } catch (error: any) {
    showFailToast(error.message);
  }
});
onBeforeUnmount(() => clearTimeout(timer));
</script>

<template>
  <van-nav-bar title="阶段回测" left-arrow @click-left="router.back()" />
  <div class="note" style="margin-bottom: 0">四阶段在全市场（含已退市股票）上的表现：各阶段之后的收益、大盘闸门和筑底判据的过滤效果</div>

  <div class="card" style="padding-bottom: 4px">
    <div class="card-title">方案</div>
    <van-tag v-for="p in settings?.presets ?? []" :key="p.key" :plain="form.preset !== p.key" type="primary" class="pm-chip"
             @click="choosePreset(p.key)">{{ p.name }}</van-tag>
    <van-field v-for="f in EDITABLE" :key="f.key" v-model="form.values[f.key]" type="number" :label="f.name" input-align="right"
               label-width="8em" style="padding-left: 0; padding-right: 0">
      <template v-if="f.pct" #extra><span style="margin-left: 4px; color: #646566">%</span></template>
    </van-field>
    <DateField v-model="form.start" label="开始" placeholder="默认最早" style="padding-left: 0; padding-right: 0" />
    <DateField v-model="form.end" label="结束" placeholder="默认最新" style="padding-left: 0; padding-right: 0" />
    <van-button type="primary" block :loading="busy || starting" :loading-text="job?.status === 'queued' ? '排队中' : '计算中'"
                style="margin: 8px 0" @click="run">运行</van-button>
    <div class="muted" style="margin-bottom: 8px">加载全市场日线需要十几秒到一分钟；相同参数和数据的结果会直接复用。</div>
  </div>

  <van-notice-bar v-if="job?.status === 'failed'" color="#e5484d" background="#fff1f0" wrapable :scrollable="false"
                  :text="`回测失败：${job.error}`" />
  <van-notice-bar v-else-if="busy" wrapable :scrollable="false" :text="job?.status === 'queued' ? '排队中（同一时间只跑一个回测）' : '计算中…'" />

  <template v-if="result">
    <div class="card">
      <div v-for="[key, name] in BOOKS" :key="key" class="bt-book">
        <div style="flex: 1">
          <div>{{ name }}</div>
          <div class="muted">最大回撤 {{ pct(result.overall[key].max_drawdown, 1) }}
            <template v-if="key !== 'market'"> · 平均持有 {{ Math.round(result.overall[key].avg_names) }} 只</template></div>
        </div>
        <div style="text-align: right">
          <div class="muted">区间收益</div>
          <b :style="{ fontSize: '17px', color: key === 'market' ? undefined : color(result.overall[key].total - result.overall.market.total) }">
            {{ pct(result.overall[key].total, 1, true) }}</b>
        </div>
      </div>
    </div>
    <div class="note" style="margin-top: 0">{{ result.start }} 至 {{ result.end }}，{{ result.stocks }} 只股票；超额 = 相对全市场等权的后续收益；只看收盘，不计费用。</div>

    <div v-if="chart" class="card">
      <div class="card-title">分年复利净值（起点 1）</div>
      <EChart :option="chart" :height="240" />
    </div>

    <div v-for="[key, title, first] in TABLES" :key="key" class="card">
      <div class="card-title">{{ title }}</div>
      <div class="table-wrap">
        <table class="table">
          <thead><tr><th>{{ first }}</th><th>样本（股·日）</th><th>20 日超额</th><th>60 日超额</th><th>120 日超额</th><th>60 日收益</th><th>60 日跑赢比例</th></tr></thead>
          <tbody>
            <tr v-for="row in result[key]" :key="row.name">
              <td>{{ row.name }}<span v-if="row.share !== undefined" class="muted" style="margin-left: 4px">占 {{ pct(row.share, 0) }}</span></td>
              <td>{{ row.count?.toLocaleString() }}</td>
              <td v-for="h in [20, 60, 120]" :key="h" :style="{ color: color(row[`excess_${h}`]) }">{{ pct(row[`excess_${h}`], 2, true) }}</td>
              <td>{{ pct(row.return_60, 2, true) }}</td>
              <td>{{ pct(row.beat_60, 0) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <div class="card">
      <div class="card-title">分年</div>
      <div class="table-wrap">
        <table class="table">
          <thead><tr><th>年份</th><th>全市场等权</th><th>第二阶段组合</th><th>加大盘闸门</th><th>沪深300 各阶段占比</th></tr></thead>
          <tbody>
            <tr v-for="y in result.yearly" :key="y.year">
              <td>{{ y.year }}</td>
              <td v-for="k in ['market', 'stage2', 'stage2_gated']" :key="k" :style="{ color: color(y[k]) }">{{ pct(y[k], 1, true) }}</td>
              <td class="muted">{{ Object.entries(y.index_stages ?? {}).map(([s, v]) => `${STAGE_SHORT[s] ?? s} ${pct(v as number, 0)}`).join(' ') }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </template>
</template>

<style>
.bt-book { display: flex; align-items: center; gap: 8px; padding: 8px 0; border-bottom: 1px solid #f5f6f8; font-size: 13px; }
.bt-book:last-child { border-bottom: none; }
</style>
