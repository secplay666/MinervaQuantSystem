<script setup lang="ts">
/**
 * Phone K-line chart on KLineChart 10 — the PC stock-chart without drawings,
 * range statistics, keyboard or screenshots: day/week/month bars paged to
 * the left, adjust (stocks), main and sub indicators chosen in a bottom sheet,
 * the shareholder panes, event badges, an index overlay, read-only automatic
 * levels and the 仓位管家 structure.  Tapping a bar shows its data in the
 * strip above the chart; tapping the strip opens the details.
 */
import type { Chart, DataLoader, KLineData, OverlayCreate } from 'klinecharts';

import type { Adjust, BarPeriod, ChartAnalysis, ChartMarks, ChartPoint, HolderCount, PmChart, PmLevelKind } from '../views/market/market-api';
import type { AutoLineData, HolderPoint, MarkData } from './kchart-extensions';

import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue';

import { dispose, init } from 'klinecharts';

import { big, DOWN, UP } from '../format';
import {
  barsApi, chartAnalysisApi, etfMarksApi, holdersApi, indexBarsApi, isIndexSymbol, LEVEL_NAMES, marksApi, PM_PHASE,
} from '../views/market/market-api';
import { registerChartExtensions } from './kchart-extensions';

const props = withDefaults(defineProps<{ compact?: boolean; height?: number | string; structure?: null | PmChart; symbol: string }>(),
                           { compact: false, height: 420, structure: null });

// -- settings (kept on the phone; the compact chart uses fixed ones) -------------------------------
interface ChartSettings {
  adjust: Adjust;
  auto: boolean;
  compare?: string;
  main: string[];
  marks: boolean;
  period: BarPeriod;
  subs: string[];
}
const STORAGE_KEY = 'minerva.mobile.chart.v1';
const DEFAULTS: ChartSettings = { adjust: 'qfq', auto: false, compare: undefined, main: ['MA'], marks: true, period: 'day', subs: ['VOL', 'MACD'] };
function loadSettings(): ChartSettings {
  if (props.compact) return { ...DEFAULTS, subs: ['VOL'] };
  try {
    return { ...DEFAULTS, ...JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '{}') };
  } catch {
    return { ...DEFAULTS };
  }
}
const settings = reactive<ChartSettings>(loadSettings());
watch(settings, () => !props.compact && localStorage.setItem(STORAGE_KEY, JSON.stringify(settings)), { deep: true });

const MAIN_INDICATORS: Record<string, string> = { BBI: 'BBI 多空', BOLL: 'BOLL 布林', EMA: 'EMA 指数均线', MA: 'MA 均线', SAR: 'SAR 抛物线' };
const SUB_INDICATORS: Record<string, string> = {
  BIAS: 'BIAS 乖离率', BRAR: 'BRAR 人气意愿', CCI: 'CCI 顺势', CR: 'CR 能量', DMA: 'DMA 平均差', DMI: 'DMI 趋向',
  KDJ: 'KDJ 随机', MACD: 'MACD 平滑异同', MTM: 'MTM 动量', OBV: 'OBV 能量潮', PSY: 'PSY 心理线', ROC: 'ROC 变动率',
  RSI: 'RSI 相对强弱', TRIX: 'TRIX 三重指数', VOL: 'VOL 成交量', VR: 'VR 成交量比率', WR: 'WR 威廉',
  HOLDERS: '股东户数', TOP10: '大股东/小股东持股',
};
/** Stocks only, drawn from the shareholder counts (no parameters). */
const HOLDER_PANES = new Set(['HOLDERS', 'TOP10']);
const HOLDER_COLORS = ['#2a78d6', '#eb6834'];
const MAX_SUBS = 3;
const MAIN_PARAMS: Record<string, number[]> = { MA: [5, 10, 20, 60] };
const COMPARE: Record<string, string> = {
  H00300: '沪深300全收益', H00852: '中证1000全收益', H00905: '中证500全收益', sh000001: '上证指数', sh000300: '沪深300',
  sh000688: '科创50', sh000905: '中证500', sz399001: '深证成指', sz399006: '创业板指',
};
const PERIODS: { label: string; value: BarPeriod }[] = [{ label: '日K', value: 'day' }, { label: '周K', value: 'week' }, { label: '月K', value: 'month' }];
const PERIOD_LABEL: Record<BarPeriod, string> = { day: '日', month: '月', week: '周' };
const ADJUST_LABEL: Record<Adjust, string> = { hfq: '后复权', none: '不复权', qfq: '前复权' };
const ADJUST_ACTIONS = (['qfq', 'none', 'hfq'] as const).map((value) => ({ name: ADJUST_LABEL[value], value }));
const PAGE: Record<BarPeriod, number> = { day: 600, month: 300, week: 400 };
const STATUS_LABEL: Record<string, string> = { confirmed: '已确认', failed: '已失效', forming: '形成中' };
const DIRECTION_COLOR: Record<string, string> = { bearish: DOWN, bullish: UP, neutral: '#eab308' };
const LEVEL_COLOR = { resistance: '#f97316', support: '#0ea5e9' };
const NO_MARKS: ChartMarks = { dividends: [], etf: [], fills: [], reports: [], risk: [], suspensions: [] };

// -- state -------------------------------------------------------------------------------------------
const container = ref<HTMLDivElement>();
let chart: Chart | null = null;
let loadToken = 0;
let resizeObserver: null | ResizeObserver = null;
const loading = ref(false);
const empty = ref(false);
const marks = ref<ChartMarks>();
const barEvents = ref<Record<number, string[]>>({}); // bar timestamp -> event lines
const holderRows = ref<HolderCount[]>([]);
const analysis = ref<ChartAnalysis>();
const hoveredTs = ref<number>(); // the tapped bar; undefined = the latest
const dataVersion = ref(0); // bumped after every load, so views of the (non-reactive) data list refresh
const sheetOpen = ref(false);
const adjustOpen = ref(false);
const detailOpen = ref(false);
const isIndex = computed(() => isIndexSymbol(props.symbol));
const heightStyle = computed(() => (typeof props.height === 'number' ? `${props.height}px` : props.height));

// -- data ----------------------------------------------------------------------------------------------
function day(timestamp: number): string {
  return new Date(timestamp + 8 * 3_600_000).toISOString().slice(0, 10); // bars are stamped at 00:00 Beijing time
}
function stamp(date: string): number {
  return new Date(`${date}T00:00:00+08:00`).getTime();
}
function toData(b: Record<string, any>): KLineData {
  return {
    amplitude: b.amplitude, close: b.close, high: b.high, low: b.low, open: b.open, pct_change: b.pct_change,
    timestamp: stamp(b.trade_date), turnover: b.amount ?? undefined, turnover_rate: b.turnover_rate, volume: b.volume ?? undefined,
  };
}
/** The last day before the period that contains ``date`` (paging a weekly or monthly chart to the left). */
function beforePeriod(date: string, period: BarPeriod): string {
  const d = new Date(`${date}T00:00:00Z`);
  if (period === 'week') d.setUTCDate(d.getUTCDate() - ((d.getUTCDay() + 6) % 7) - 1);
  else if (period === 'month') d.setUTCDate(0);
  else d.setUTCDate(d.getUTCDate() - 1);
  return d.toISOString().slice(0, 10);
}
function fetchBars(symbol: string, period: BarPeriod, adjust: Adjust, end?: string) {
  return isIndexSymbol(symbol) ? indexBarsApi(symbol, PAGE[period], period, end) : barsApi(symbol, adjust, PAGE[period], period, end);
}

const loader: DataLoader = {
  getBars: async ({ callback, timestamp, type }) => {
    const token = loadToken;
    const { adjust, period } = settings;
    const symbol = props.symbol;
    try {
      if (type === 'init') {
        loading.value = true;
        const bars = await fetchBars(symbol, period, adjust);
        if (token !== loadToken) return;
        empty.value = bars.length === 0;
        callback(bars.map(toData), { backward: false, forward: bars.length >= PAGE[period] });
        dataVersion.value += 1;
        await nextTick();
        await afterInit(token);
      } else if (type === 'forward' && timestamp) {
        const bars = await fetchBars(symbol, period, adjust, beforePeriod(day(timestamp), period));
        if (token !== loadToken) return;
        callback(bars.map(toData), { backward: false, forward: bars.length >= PAGE[period] });
        dataVersion.value += 1;
        renderMarks();
      } else {
        callback([], false);
      }
    } catch {
      if (token === loadToken) callback([], false);
    } finally {
      if (type === 'init' && token === loadToken) loading.value = false;
    }
  },
};

async function afterInit(token: number) {
  hoveredTs.value = undefined;
  const symbol = props.symbol;
  const index = isIndexSymbol(symbol);
  const [m, holders] = await Promise.all([
    index ? etfMarksApi(symbol).then((etf) => ({ ...NO_MARKS, etf })).catch(() => undefined) : marksApi(symbol).catch(() => undefined),
    index ? Promise.resolve([]) : holdersApi(symbol).catch(() => []),
  ]);
  if (token !== loadToken || !chart) return;
  marks.value = m;
  holderRows.value = holders;
  refreshHolderPanes();
  renderMarks();
  if (settings.auto) void loadAnalysis(token);
}

// -- marks ---------------------------------------------------------------------------------------------
function barIndexFor(data: KLineData[], date: string): number {
  const t = stamp(date);
  const slack = { day: 0, month: 31, week: 7 }[settings.period] * 86_400_000;
  if (!data.length || t > data.at(-1)!.timestamp || t < data[0]!.timestamp - slack) return -1;
  let lo = 0;
  let hi = data.length - 1;
  while (lo < hi) { // first bar dated on or after the event: the bar that contains it
    const mid = (lo + hi) >> 1;
    if (data[mid]!.timestamp < t) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

const REPORT_NAMES: Record<number, string> = { 3: '一季报', 6: '中报', 9: '三季报', 12: '年报' };
function reportName(reportDate: string): string {
  const [y, m] = reportDate.split('-');
  return `${y} 年${REPORT_NAMES[Number(m)] ?? '报告'}`;
}

const PM_PRIORITY_COLOR: Record<number, string> = { 1: '#dc2626', 2: '#f97316', 3: '#2563eb', 4: '#6b7280' };
const PM_BADGE: Record<string, string> = {
  base_neckline: '底', base_retest: '底', base_volume: '底', entry: '入', exhausted: '衰', false_break: '假', ladder: '减',
  realized: '兑', target: '目', top_break: '顶', top_confirmed: '顶', top_watch: '观', trailing: '止', waiting_index: '待',
};
const PM_LINE: Record<PmLevelKind, { color: string; dashed?: boolean }> = {
  base_zone: { color: '#0ea5e9' }, buyback_zone: { color: '#f97316' }, neckline: { color: '#2563eb' },
  reference: { color: '#6b7280', dashed: true }, target: { color: '#d97706' }, top_neckline: { color: '#dc2626', dashed: true },
};

function renderMarks() {
  if (!chart) return;
  chart.removeOverlay({ groupId: 'marks' });
  const data = chart.getDataList();
  const m = marks.value ?? NO_MARKS;
  if (!data.length) return;
  const slots = new Map<number, { above: MarkData[]; below: MarkData[] }>();
  const events: Record<number, string[]> = {};
  const add = (date: string, badge: Omit<MarkData, 'stack'>, line: string) => {
    const index = barIndexFor(data, date);
    if (index < 0) return;
    (events[data[index]!.timestamp] ??= []).push(line);
    if (!settings.marks) return;
    const slot = slots.get(index) ?? { above: [], below: [] };
    const side = badge.below ? slot.below : slot.above;
    if (!side.some((b) => b.text === badge.text)) side.push({ ...badge, stack: side.length });
    slots.set(index, slot);
  };
  for (const f of m.fills) {
    add(f.trade_date, { below: f.side === 'buy', color: f.side === 'buy' ? UP : DOWN, text: f.side === 'buy' ? 'B' : 'S' },
        `${f.side === 'buy' ? '买入' : '卖出'} ${f.qty.toLocaleString()} 股 @ ${f.price.toFixed(2)}（${f.account_name}）`);
  }
  for (const d of m.dividends) add(d.ex_date, { below: false, color: '#f59e0b', text: '除' }, `除权除息：${d.plan_profile ?? ''}`);
  for (const r of m.reports) add(r.notice_date, { below: false, color: '#3b82f6', text: '报' }, `发布${reportName(r.report_date)}`);
  for (const r of m.risk) {
    add(r.start_date, { below: false, color: '#a855f7', text: r.status.replace('DELISTING', '退') },
        `实施风险警示 ${r.status}${r.start_title ? `：${r.start_title}` : ''}`);
    if (r.end_date) add(r.end_date, { below: false, color: '#a855f7', text: '摘' }, `撤销风险警示 ${r.status}`);
  }
  for (const e of m.etf ?? []) {
    const inflow = e.abnormal === 'in';
    add(e.trade_date, { below: inflow, color: inflow ? UP : DOWN, text: `${e.strong ? '强' : ''}${inflow ? '申' : '赎'}` },
        `${e.group} ETF ${e.strong ? '强' : ''}异常净${inflow ? '申购' : '赎回'} ${(e.flow / 1e8).toFixed(1)} 亿（占规模 ${(e.flow_pct * 100).toFixed(2)}%，z ${e.z}）`);
  }
  for (const s of m.suspensions) {
    add(s.suspend_start, { below: false, color: '#6b7280', text: '停' },
        `停牌${s.suspend_end ? ` 至 ${s.suspend_end}${s.end_inferred ? '（按复牌日推断）' : ''}` : ''}${s.reason ? `：${s.reason}` : ''}`);
  }
  for (const e of props.structure?.events ?? []) {
    add(e.trade_date, { below: e.rule === 'entry', color: PM_PRIORITY_COLOR[e.priority] ?? '#6b7280', text: PM_BADGE[e.rule] ?? '仓' },
        `仓位管家·${e.priority_name}：${e.message}`);
  }
  barEvents.value = events;
  const overlays: OverlayCreate[] = [];
  for (const [index, slot] of slots) {
    const bar = data[index]!;
    for (const mark of [...slot.above, ...slot.below]) {
      overlays.push({ extendData: mark, groupId: 'marks', lock: true, name: 'eventMark',
                      points: [{ timestamp: bar.timestamp, value: mark.below ? bar.low : bar.high }] });
    }
  }
  if (overlays.length) chart.createOverlay(overlays);
  renderLevels();
  renderAuto();
}

// -- 仓位管家: confirmed levels and sentinels, read-only ------------------------------------------------------
/** Levels are served forward-adjusted: drawn on qfq charts (and indices or ETFs, never adjusted). */
const levelsDrawable = computed(() => isIndex.value || props.structure?.kind !== 'stock' || settings.adjust === 'qfq');
const currentLevels = computed(() => {
  const levels = props.structure?.levels ?? [];
  const round = Math.max(0, ...levels.map((l) => l.round_no));
  return levels.filter((l) => l.round_no === round);
});
const activeSentinels = computed(() => (props.structure?.sentinels ?? []).filter((s) => s.status === 'active'));

function renderLevels() {
  if (!chart) return;
  chart.removeOverlay({ groupId: 'pm' });
  const data = chart.getDataList();
  if (!props.structure || !data.length || !levelsDrawable.value) return;
  const last = data.at(-1)!;
  const overlays: OverlayCreate[] = [];
  for (const lv of currentLevels.value) {
    const start = data[Math.max(0, barIndexFor(data, lv.effective_date))]!;
    const style = PM_LINE[lv.kind];
    if ((lv.kind === 'base_zone' || lv.kind === 'buyback_zone') && lv.lower) {
      const what = lv.kind === 'buyback_zone' ? `回撤买入区（买回 ${Math.round((lv.fraction ?? 0) * 100)}%）` : '起涨区';
      overlays.push({ extendData: { color: style.color, label: `${what} ${lv.lower.toFixed(2)}–${lv.price.toFixed(2)}` } satisfies AutoLineData,
                      groupId: 'pm', lock: true, name: 'priceZone',
                      points: [{ timestamp: start.timestamp, value: lv.lower }, { timestamp: last.timestamp, value: lv.price }] });
      continue;
    }
    const lines = [{ label: `${LEVEL_NAMES[lv.kind]} ${lv.price.toFixed(2)}${lv.top_mode === 'observe' ? '（观察）' : ''}`, price: lv.price, ...style }];
    if (lv.kind === 'neckline') lines.push({ color: '#9ca3af', dashed: true, label: `失效线 ${(lv.price * 0.95).toFixed(2)}`, price: lv.price * 0.95 });
    for (const line of lines) {
      overlays.push({ extendData: { color: line.color, dashed: line.dashed, extend: true, label: line.label, width: 1.5 } satisfies AutoLineData,
                      groupId: 'pm', lock: true, name: 'autoLine',
                      points: [{ timestamp: start.timestamp, value: line.price }, { timestamp: last.timestamp, value: line.price }] });
    }
  }
  for (const s of activeSentinels.value) {
    overlays.push({ extendData: { label: `⚓哨兵 ${s.price.toFixed(2)} ${s.direction === 'up' ? '↑' : '↓'}` }, groupId: 'pm', lock: true,
                    name: 'sentinelLine', points: [{ timestamp: last.timestamp, value: s.price }] });
  }
  if (overlays.length) chart.createOverlay(overlays);
}

// -- automatic levels and patterns (read-only) -------------------------------------------------------------
async function loadAnalysis(token = loadToken) {
  const { adjust, period } = settings;
  const symbol = props.symbol;
  try {
    const result = await chartAnalysisApi(symbol, adjust, period, Math.min(500, PAGE[period]));
    if (token !== loadToken || symbol !== props.symbol) return;
    analysis.value = result;
  } catch {
    analysis.value = undefined;
  }
  renderAuto();
}

function renderAuto() {
  if (!chart) return;
  chart.removeOverlay({ groupId: 'auto' });
  const a = analysis.value;
  const data = chart.getDataList();
  if (!a || !settings.auto || !data.length) return;
  type Pt = { timestamp: number; value: number };
  const point = (p: ChartPoint): null | Pt => {
    const index = barIndexFor(data, p.date);
    return index < 0 ? null : { timestamp: data[index]!.timestamp, value: p.price };
  };
  const overlays: OverlayCreate[] = [];
  const line = (name: string, pts: (null | Pt)[], extendData: AutoLineData) => {
    if (pts.length >= 2 && pts.every(Boolean)) overlays.push({ extendData, groupId: 'auto', lock: true, name, points: pts as Pt[] });
  };
  for (const lv of a.levels) {
    const from = data[Math.max(0, barIndexFor(data, lv.first_date))]!.timestamp;
    overlays.push({ extendData: { color: LEVEL_COLOR[lv.kind], label: `${lv.kind === 'resistance' ? '阻力' : '支撑'} ${lv.price.toFixed(2)}` } satisfies AutoLineData,
                    groupId: 'auto', lock: true, name: 'priceZone', points: [{ timestamp: from, value: lv.low }, { timestamp: from, value: lv.high }] });
  }
  const lastDay = day(data.at(-1)!.timestamp);
  for (const p of a.patterns) {
    const color = p.status === 'failed' ? '#9ca3af' : DIRECTION_COLOR[p.direction] ?? '#eab308';
    line('autoPolyline', p.points.map(point), { color, dots: true, label: `${p.name}·${STATUS_LABEL[p.status]}`, width: 1.5 });
    for (const l of p.lines) line('autoLine', [point(l.start), point(l.end)], { color, dashed: true });
    if (p.target !== null && p.status !== 'failed') {
      line('autoLine', [point({ date: p.end_date, price: p.target }), point({ date: lastDay, price: p.target })],
           { color, dashed: true, label: `${p.name}目标 ${p.target.toFixed(2)}` });
    }
  }
  if (overlays.length) chart.createOverlay(overlays);
}

// -- indicators ----------------------------------------------------------------------------------------------
const holderPoints = computed<HolderPoint[]>(() => holderRows.value.map((r) => ({ holders: r.holders, t: stamp(r.end_date), top10: r.top10_pct })));
const holderStyles = () => ({ lines: HOLDER_COLORS.map((color) => ({ color, size: 1.5 })) }) as any;
const subOptions = computed(() => Object.entries(SUB_INDICATORS).filter(([name]) => !(isIndex.value && HOLDER_PANES.has(name))));

function applyIndicators() {
  if (!chart) return;
  const onMain = new Set(chart.getIndicators({ paneId: 'candle_pane' }).map((i) => i.name));
  for (const name of Object.keys(MAIN_INDICATORS)) {
    const want = settings.main.includes(name);
    if (want && !onMain.has(name)) chart.createIndicator({ calcParams: MAIN_PARAMS[name], name, paneId: 'candle_pane' }, true);
    if (!want && onMain.has(name)) chart.removeIndicator({ name, paneId: 'candle_pane' });
  }
  for (const name of Object.keys(SUB_INDICATORS)) {
    const paneId = `pane_${name}`;
    const has = chart.getIndicators({ paneId }).length > 0;
    const want = settings.subs.includes(name) && !(HOLDER_PANES.has(name) && isIndex.value);
    if (want && !has) {
      if (HOLDER_PANES.has(name)) chart.createIndicator({ extendData: { points: holderPoints.value }, name, paneId, styles: holderStyles() });
      else chart.createIndicator({ name, paneId });
      chart.setPaneOptions({ height: props.compact ? 64 : 76, id: paneId, minHeight: 40 });
      chart.overrideYAxis({ inside: true, paneId });
    }
    if (!want && has) chart.removeIndicator({ paneId });
  }
}
function refreshHolderPanes() {
  for (const name of HOLDER_PANES) {
    if (chart?.getIndicators({ name }).length) chart.overrideIndicator({ extendData: { points: holderPoints.value }, name, styles: holderStyles() });
  }
}

// -- index overlay -----------------------------------------------------------------------------------------------
let compareCloses: null | Record<string, number> = null;
let rebaseTimer: null | ReturnType<typeof setTimeout> = null;
function firstVisible(): number | undefined {
  const data = chart?.getDataList() ?? [];
  const { from } = chart?.getVisibleRange() ?? { from: 0 };
  return data[Math.max(0, from)]?.timestamp;
}
async function applyCompare() {
  if (!chart) return;
  chart.removeIndicator({ name: 'CMP', paneId: 'candle_pane' });
  compareCloses = null;
  const code = settings.compare;
  if (!code) return;
  const bars = await indexBarsApi(code, 6000).catch(() => []);
  if (!chart || settings.compare !== code) return;
  compareCloses = Object.fromEntries(bars.map((b) => [b.trade_date, b.close]));
  chart.createIndicator({ extendData: { base: firstVisible(), closes: compareCloses, label: COMPARE[code] }, name: 'CMP', paneId: 'candle_pane',
                          shortName: `对比 ${COMPARE[code]}`, styles: { lines: [{ color: '#f59e0b', size: 1.5 }] } as any }, true);
}
/** Re-anchor the comparison line at the first visible bar after scrolling or zooming. */
function rebaseCompare() {
  if (!settings.compare || !compareCloses) return;
  if (rebaseTimer) clearTimeout(rebaseTimer);
  rebaseTimer = setTimeout(() => {
    const base = firstVisible();
    if (chart && base !== undefined && settings.compare && compareCloses) {
      chart.overrideIndicator({ extendData: { base, closes: compareCloses, label: COMPARE[settings.compare] }, name: 'CMP' });
    }
  }, 150);
}

// -- tapping a bar -----------------------------------------------------------------------------------------------
/** The bar under a chart x coordinate (the crosshair action only carries coordinates). */
function hover(x?: number) {
  if (!chart || x === undefined) return;
  const [point] = chart.convertFromPixel([{ x }], { paneId: 'candle_pane' }) as { dataIndex?: number }[];
  const data = chart.getDataList();
  const index = point?.dataIndex;
  if (index !== undefined && index >= 0 && index < data.length) hoveredTs.value = data[index]!.timestamp;
}
/** KLineChart's internal store: the crosshair has no public getter or reset (guarded, falls back to keeping the bar). */
const store = () => (chart as any)?.getChartStore?.();
/** A touch that hid the crosshair (a drag or a tap elsewhere) brings the strip back to the latest bar. */
function onTouchEnd() {
  setTimeout(() => {
    const crosshair = store()?.getCrosshair?.();
    if (crosshair && !crosshair.paneId) hoveredTs.value = undefined;
  }, 0);
}
function backToLatest() {
  hoveredTs.value = undefined;
  store()?.setCrosshair?.();
}

const shownIndex = computed(() => {
  void dataVersion.value;
  const data = chart?.getDataList() ?? [];
  if (hoveredTs.value === undefined) return data.length - 1;
  return data.findIndex((d) => d.timestamp === hoveredTs.value);
});
const shown = computed<KLineData | undefined>(() => (dataVersion.value, chart?.getDataList()[shownIndex.value]));
const shownPrev = computed<KLineData | undefined>(() => (shownIndex.value > 0 ? chart?.getDataList()[shownIndex.value - 1] : undefined));
const shownChange = computed(() => {
  const bar = shown.value;
  if (!bar) return undefined;
  const pct = bar.pct_change as null | number | undefined;
  if (pct !== null && pct !== undefined) return pct / 100;
  return shownPrev.value ? bar.close / shownPrev.value.close - 1 : undefined;
});
/** From the data when given, else from high, low and the previous close (indices have no amplitude field). */
const shownAmplitude = computed(() => {
  const bar = shown.value;
  if (!bar) return undefined;
  if (typeof bar.amplitude === 'number') return bar.amplitude / 100;
  return shownPrev.value ? (bar.high - bar.low) / shownPrev.value.close : undefined;
});
const shownEvents = computed(() => (shown.value ? barEvents.value[shown.value.timestamp] ?? [] : []));
const holderPaneOn = computed(() => !isIndex.value && settings.subs.some((n) => HOLDER_PANES.has(n)));
/** The latest count and the latest report stake on or before the shown bar. */
const shownHolders = computed(() => {
  const bar = shown.value;
  if (!bar || !holderPaneOn.value || !holderRows.value.length) return undefined;
  let count: HolderCount | undefined;
  let stake: HolderCount | undefined;
  for (const r of holderRows.value) {
    if (stamp(r.end_date) > bar.timestamp) break;
    if (r.holders !== null) count = r;
    if (r.top10_pct !== null) stake = r;
  }
  return { count, stake };
});

function fmt(v: unknown, digits = 2): string {
  return typeof v === 'number' && Number.isFinite(v) ? v.toFixed(digits) : '—';
}
function pctText(value: null | number | undefined, digits = 2, signed = true): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—';
  return `${signed && value > 0 ? '+' : ''}${(value * 100).toFixed(digits)}%`;
}
function colorOf(value?: null | number): string | undefined {
  if (value === null || value === undefined || !Number.isFinite(value) || value === 0) return undefined;
  return value > 0 ? UP : DOWN;
}
const volumeText = (v?: number) => (v ? `${(v / 1e6).toFixed(2)}万手` : '—');
const holderLine = computed(() => {
  const h = shownHolders.value;
  if (!h) return '';
  if (!h.count && !h.stake) return '股东：这天之前没有披露';
  const parts: string[] = [];
  if (h.count) parts.push(`股东 ${(h.count.holders! / 1e4).toFixed(2)} 万户（${pctText(h.count.change, 1)}，公告 ${h.count.notice_date.slice(5)}）`);
  if (h.stake) parts.push(`前十大 ${fmt(h.stake.top10_pct, 1)}% · 其余 ${fmt(100 - h.stake.top10_pct!, 1)}%`);
  return parts.join(' · ');
});

// -- setup -----------------------------------------------------------------------------------------------------------
function themeStyles() {
  const bar = { downBorderColor: DOWN, downColor: DOWN, downWickColor: DOWN, noChangeBorderColor: '#888888', noChangeColor: '#888888',
                noChangeWickColor: '#888888', upBorderColor: UP, upColor: UP, upWickColor: UP };
  return {
    candle: { bar, priceMark: { last: { downColor: DOWN, noChangeColor: '#888888', upColor: UP } }, tooltip: { showRule: 'none' } },
    grid: { vertical: { show: false } },
    indicator: { bars: [{ downColor: 'rgba(22, 163, 74, 0.75)', noChangeColor: '#888888', upColor: 'rgba(229, 72, 77, 0.75)' }],
                 tooltip: { showRule: props.compact ? 'none' : 'always' } },
  } as any;
}

function reload() {
  loadToken += 1;
  for (const groupId of ['marks', 'auto', 'pm']) chart?.removeOverlay({ groupId });
  hoveredTs.value = undefined;
  analysis.value = undefined;
  holderRows.value = [];
  barEvents.value = {};
  refreshHolderPanes();
}

onMounted(() => {
  registerChartExtensions();
  if (!container.value) return;
  chart = init(container.value, { locale: 'zh-CN', timezone: 'Asia/Shanghai' });
  if (!chart) return;
  chart.setStyles('light');
  chart.setStyles(themeStyles());
  chart.setBarSpace(6);
  chart.overrideYAxis({ inside: true, paneId: 'candle_pane' });
  chart.subscribeAction('onCrosshairChange', (data: any) => hover(data?.x));
  chart.subscribeAction('onVisibleRangeChange', rebaseCompare);
  applyIndicators();
  chart.setSymbol({ pricePrecision: 2, ticker: props.symbol, volumePrecision: 0 });
  chart.setPeriod({ span: 1, type: settings.period });
  loadToken += 1;
  chart.setDataLoader(loader);
  void applyCompare();
  resizeObserver = new ResizeObserver(() => chart?.resize());
  resizeObserver.observe(container.value);
  container.value.addEventListener('touchend', onTouchEnd, { passive: true });
});

onBeforeUnmount(() => {
  resizeObserver?.disconnect();
  if (rebaseTimer) clearTimeout(rebaseTimer);
  if (container.value) {
    container.value.removeEventListener('touchend', onTouchEnd);
    dispose(container.value);
  }
  chart = null;
});

watch(() => props.symbol, (symbol) => {
  reload();
  applyIndicators(); // the holder panes come and go between stocks and indices
  chart?.setSymbol({ pricePrecision: 2, ticker: symbol, volumePrecision: 0 });
});
watch(() => settings.period, (period) => {
  reload();
  chart?.setPeriod({ span: 1, type: period });
});
watch(() => settings.adjust, () => {
  reload();
  chart?.resetData();
});
watch(() => [settings.main.join(), settings.subs.join()], applyIndicators);
watch(() => settings.compare, () => void applyCompare());
watch(() => settings.marks, renderMarks);
watch(() => props.structure, renderMarks);
watch(() => settings.auto, (on) => (on && !analysis.value ? void loadAnalysis() : renderAuto()));

defineExpose({ settings });
</script>

<template>
  <div class="kchart" :style="{ height: heightStyle }">
    <div v-if="!compact" class="kc-toolbar">
      <div class="kc-seg">
        <button v-for="p in PERIODS" :key="p.value" :class="{ on: settings.period === p.value }" @click="settings.period = p.value">{{ p.label }}</button>
      </div>
      <button v-if="!isIndex" class="kc-chip" @click="adjustOpen = true">{{ ADJUST_LABEL[settings.adjust] }} ▾</button>
      <button class="kc-chip" @click="sheetOpen = true">指标 ▾</button>
      <button class="kc-chip" :class="{ on: settings.marks }" @click="settings.marks = !settings.marks">标记{{ settings.marks ? '开' : '关' }}</button>
      <van-loading v-if="loading" size="16" style="margin-left: 4px" />
    </div>

    <div class="kc-strip" @click="detailOpen = true">
      <template v-if="shown">
        <div class="kc-head">
          <span>{{ day(shown.timestamp) }}（{{ PERIOD_LABEL[settings.period] }}K{{ hoveredTs === undefined ? '·最新' : '' }}）</span>
          <a v-if="hoveredTs !== undefined" class="kc-link" @click.stop="backToLatest">回到最新</a>
          <span v-else class="muted">点 K 线看当期，点这里看详情</span>
        </div>
        <div class="kc-grid">
          <span>开 <b>{{ fmt(shown.open) }}</b></span>
          <span>高 <b :style="{ color: UP }">{{ fmt(shown.high) }}</b></span>
          <span>低 <b :style="{ color: DOWN }">{{ fmt(shown.low) }}</b></span>
          <span>收 <b :style="{ color: colorOf(shownChange) }">{{ fmt(shown.close) }}</b></span>
          <span>涨跌 <b :style="{ color: colorOf(shownChange) }">{{ pctText(shownChange) }}</b></span>
          <span>振幅 <b>{{ pctText(shownAmplitude, 2, false) }}</b></span>
          <span>量 <b>{{ volumeText(shown.volume) }}</b></span>
          <span>额 <b>{{ shown.turnover ? big(shown.turnover) : '—' }}</b></span>
          <span>换手 <b>{{ typeof shown.turnover_rate === 'number' ? `${fmt(shown.turnover_rate)}%` : '—' }}</b></span>
        </div>
        <div v-if="holderPaneOn" class="kc-line">{{ holderLine || '股东：没有数据' }}</div>
        <div class="kc-line" :class="{ muted: !shownEvents.length }">{{ shownEvents.length ? `事件：${shownEvents.join('；')}` : '当期无事件' }}</div>
      </template>
      <div v-else class="kc-head muted">{{ loading ? '加载中…' : '没有行情数据' }}</div>
    </div>
    <div v-if="structure && currentLevels.length && !levelsDrawable" class="kc-line muted" style="padding: 0 8px 4px">
      价位按前复权保存，切到前复权可看到价位线
    </div>

    <div class="kc-body">
      <div ref="container" class="kc-canvas"></div>
      <div v-if="empty && !loading" class="kc-empty">没有行情数据</div>
    </div>

    <van-action-sheet v-model:show="adjustOpen" :actions="ADJUST_ACTIONS" cancel-text="取消" teleport="body"
                      @select="(a: any) => ((settings.adjust = a.value), (adjustOpen = false))" />

    <!-- indicators -->
    <van-popup v-model:show="sheetOpen" position="bottom" round closeable teleport="body" :style="{ maxHeight: '80vh' }">
      <div class="kc-sheet">
        <div class="kc-sheet-title">主图指标</div>
        <van-checkbox-group v-model="settings.main" class="kc-options">
          <van-checkbox v-for="(label, name) in MAIN_INDICATORS" :key="name" :name="name" shape="square">{{ label }}</van-checkbox>
        </van-checkbox-group>
        <div class="kc-sheet-title">副图指标 <span class="muted">（最多 {{ MAX_SUBS }} 个）</span></div>
        <van-checkbox-group v-model="settings.subs" class="kc-options" :max="MAX_SUBS">
          <van-checkbox v-for="[name, label] in subOptions" :key="name" :name="name" shape="square">{{ label }}</van-checkbox>
        </van-checkbox-group>
        <div class="kc-sheet-title">叠加指数</div>
        <van-radio-group :model-value="settings.compare ?? ''" class="kc-options" @update:model-value="(v: string) => (settings.compare = v || undefined)">
          <van-radio name="">不叠加</van-radio>
          <van-radio v-for="(label, code) in COMPARE" :key="code" :name="code">{{ label }}</van-radio>
        </van-radio-group>
        <van-cell-group :border="false" style="margin-top: 8px">
          <van-cell title="事件标记" label="除权除息、财报、风险警示、停牌、买卖点；指数为 ETF 异常申赎">
            <template #right-icon><van-switch v-model="settings.marks" size="20" /></template>
          </van-cell>
          <van-cell title="自动画线" label="支撑阻力、形态（按规则自动识别，仅供参考）">
            <template #right-icon><van-switch v-model="settings.auto" size="20" /></template>
          </van-cell>
        </van-cell-group>
      </div>
    </van-popup>

    <!-- the shown bar in full -->
    <van-popup v-model:show="detailOpen" position="bottom" round closeable teleport="body" :style="{ maxHeight: '80vh' }">
      <div class="kc-sheet">
        <template v-if="shown">
          <div class="kc-sheet-title">{{ day(shown.timestamp) }}（{{ PERIOD_LABEL[settings.period] }}K{{ hoveredTs === undefined ? '·最新' : '' }}）</div>
          <div class="kc-pairs">
            <span>开盘</span><b>{{ fmt(shown.open) }}</b>
            <span>最高</span><b :style="{ color: UP }">{{ fmt(shown.high) }}</b>
            <span>最低</span><b :style="{ color: DOWN }">{{ fmt(shown.low) }}</b>
            <span>收盘</span><b :style="{ color: colorOf(shownChange) }">{{ fmt(shown.close) }}</b>
            <span>涨跌幅</span><b :style="{ color: colorOf(shownChange) }">{{ pctText(shownChange) }}</b>
            <span>振幅</span><b>{{ pctText(shownAmplitude, 2, false) }}</b>
            <span>成交量</span><b>{{ volumeText(shown.volume) }}</b>
            <span>成交额</span><b>{{ shown.turnover ? big(shown.turnover) : '—' }}</b>
            <span>换手率</span><b>{{ typeof shown.turnover_rate === 'number' ? `${fmt(shown.turnover_rate)}%` : '—' }}</b>
          </div>
          <template v-if="shownHolders">
            <div class="kc-sheet-title">股东（截至当日最新一期）</div>
            <div v-if="shownHolders.count" class="kc-pairs">
              <span>股东户数</span><b>{{ (shownHolders.count.holders! / 1e4).toFixed(2) }} 万{{ shownHolders.count.approximate ? '（约）' : '' }}</b>
              <span>较上期</span><b>{{ pctText(shownHolders.count.change, 1) }}</b>
              <span>统计日 / 公告</span><b>{{ shownHolders.count.end_date.slice(2) }} / {{ shownHolders.count.notice_date.slice(5) }}</b>
            </div>
            <div v-if="shownHolders.stake" class="kc-pairs">
              <span>前十大股东</span><b>{{ fmt(shownHolders.stake.top10_pct, 1) }}%</b>
              <span>其余股东</span><b>{{ fmt(100 - shownHolders.stake.top10_pct!, 1) }}%</b>
              <span>十大流通股东</span><b>{{ shownHolders.stake.top10_free_pct === null ? '—' : `${fmt(shownHolders.stake.top10_free_pct, 1)}%` }}</b>
              <span>报告期</span><b>{{ shownHolders.stake.end_date.slice(2) }}</b>
            </div>
            <div v-if="!shownHolders.count && !shownHolders.stake" class="muted">这天之前没有披露</div>
          </template>
          <template v-if="shownEvents.length">
            <div class="kc-sheet-title">当期事件</div>
            <div v-for="(line, k) in shownEvents" :key="k" class="kc-event">{{ line }}</div>
          </template>
        </template>
        <template v-if="structure">
          <div class="kc-sheet-title">结构（仓位管家）</div>
          <div v-if="structure.phase" class="kc-event">
            {{ PM_PHASE[structure.phase] ?? structure.phase }}<span v-if="structure.completion !== null"> · 完成度 {{ (structure.completion * 100).toFixed(0) }}%</span>
          </div>
          <div class="kc-pairs">
            <template v-for="lv in currentLevels" :key="lv.id">
              <span :style="{ color: PM_LINE[lv.kind].color }">{{ LEVEL_NAMES[lv.kind] }}</span>
              <b>{{ lv.lower ? `${lv.lower.toFixed(2)}–` : '' }}{{ lv.price.toFixed(2) }}</b>
            </template>
            <template v-for="s in activeSentinels" :key="`s${s.id}`">
              <span style="color: #2563eb">⚓哨兵 {{ s.direction === 'up' ? '↑' : '↓' }}</span>
              <b>{{ s.price.toFixed(2) }}<template v-if="s.days">（约 {{ s.days }} 天）</template></b>
            </template>
          </div>
          <div v-if="!currentLevels.length" class="muted">{{ structure.item_id ? '还没有确认的价位' : '不在标的库中' }}</div>
        </template>
        <template v-if="settings.auto && analysis && (analysis.patterns.length || analysis.levels.length)">
          <div class="kc-sheet-title">自动分析</div>
          <div v-for="(p, k) in analysis.patterns" :key="`p${k}`" class="kc-event">
            <span :style="{ color: p.status === 'failed' ? '#9ca3af' : DIRECTION_COLOR[p.direction] }">{{ p.name }}·{{ STATUS_LABEL[p.status] }}</span>
            <span class="muted"> {{ p.start_date.slice(2) }} ~ {{ p.end_date.slice(2) }}{{ p.target !== null && p.status !== 'failed' ? `，目标 ${p.target.toFixed(2)}` : '' }}</span>
          </div>
          <div v-for="(lv, k) in [...analysis.levels].reverse()" :key="`l${k}`" class="kc-event" :style="{ color: LEVEL_COLOR[lv.kind] }">
            {{ lv.kind === 'resistance' ? '阻力' : '支撑' }} {{ lv.price.toFixed(2) }}{{ lv.extreme ? '' : ` ×${lv.touches}` }}
          </div>
        </template>
        <p class="muted" style="margin: 12px 0 0">双指缩放、左右拖动平移，拖到最左会加载更早的 K 线；点一下 K 线显示十字线和当期数据。</p>
      </div>
    </van-popup>
  </div>
</template>

<style scoped>
.kchart { display: flex; flex-direction: column; background: #fff; }
.kc-toolbar { display: flex; flex: none; gap: 6px; align-items: center; padding: 4px 8px; overflow-x: auto; white-space: nowrap; border-bottom: 1px solid #f2f3f5; }
.kc-seg { display: inline-flex; flex: none; padding: 2px; background: #f2f3f5; border-radius: 6px; }
.kc-seg button, .kc-chip {
  flex: none; min-width: 44px; height: 36px; padding: 0 10px; font-size: 13px; color: #323233; background: transparent;
  border: 0; border-radius: 5px;
}
.kc-seg button.on { color: #1677ff; font-weight: 600; background: #fff; box-shadow: 0 1px 2px rgb(0 0 0 / 8%); }
.kc-chip { background: #f2f3f5; }
.kc-chip.on { color: #1677ff; background: #e8f3ff; }
.kc-strip { flex: none; padding: 4px 8px; font-size: 11px; line-height: 16px; color: #646566; border-bottom: 1px solid #f2f3f5; }
.kc-head { display: flex; justify-content: space-between; color: #323233; }
.kc-link { padding: 0 4px; color: #1677ff; }
.kc-grid { display: grid; grid-template-columns: repeat(3, 1fr); column-gap: 6px; }
.kc-grid span, .kc-line { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.kc-grid b { font-weight: 500; color: #323233; }
.kc-body { position: relative; flex: 1; min-height: 0; }
.kc-canvas { position: absolute; inset: 0; }
.kc-empty { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; color: #969799; }
.kc-sheet { padding: 16px 16px 24px; }
.kc-sheet-title { margin: 12px 0 6px; font-size: 14px; font-weight: 600; }
.kc-sheet-title:first-child { margin-top: 0; }
.kc-options { display: grid; grid-template-columns: repeat(2, 1fr); gap: 0 8px; }
.kc-options :deep(.van-checkbox), .kc-options :deep(.van-radio) { min-height: 40px; font-size: 14px; }
.kc-pairs { display: grid; grid-template-columns: auto 1fr; gap: 4px 12px; font-size: 13px; }
.kc-pairs span { color: #969799; }
.kc-pairs b { font-weight: 500; text-align: right; }
.kc-pairs + .kc-pairs { margin-top: 8px; }
.kc-event { margin-bottom: 4px; font-size: 13px; line-height: 18px; }
</style>
