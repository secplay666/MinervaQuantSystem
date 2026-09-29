<script lang="ts" setup>
/**
 * Stock chart workstation on KLineChart 10: daily, weekly and monthly bars
 * with paging to the left, main and sub indicators with editable parameters,
 * an index comparison line, event badges (fills, ex-dates, reports, risk
 * warnings, suspensions), drawing tools saved per user and stock, range
 * statistics, a data window and keyboard navigation.
 */
import type { Chart, DataLoader, KLineData, Overlay, OverlayCreate, OverlayMode } from 'klinecharts';

import type { MarkData, RangeStats } from './extensions';

import type { BarPeriod, ChartMarks, SavedOverlay } from '#/api';

import { computed, h, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue';

import { usePreferences } from '@vben/preferences';

import {
  Button, Checkbox, Divider, Dropdown, Input, Menu, message, Modal, Popconfirm, Segmented, Select, Space,
  Tooltip,
} from 'ant-design-vue';
import { dispose, init } from 'klinecharts';

import { barsApi, drawingsApi, indexBarsApi, marksApi, saveDrawingsApi } from '#/api';
import { bigYuan, DOWN_COLOR, UP_COLOR } from '#/utils/format';

import { rangeStats, registerChartExtensions } from './extensions';

const props = withDefaults(defineProps<{ height?: number | string; name?: string; symbol: string }>(), { height: 640 });

// -- settings (kept in the browser) -------------------------------------------------------------
type MarkKind = 'dividends' | 'fills' | 'reports' | 'risk' | 'suspensions';
interface ChartSettings {
  adjust: 'hfq' | 'none' | 'qfq';
  compare?: string;
  magnet: OverlayMode;
  main: string[];
  marks: Record<MarkKind, boolean>;
  params: Record<string, number[]>;
  period: BarPeriod;
  panel: boolean;
  subs: string[];
}
const STORAGE_KEY = 'minerva.chart.v1';
const DEFAULTS: ChartSettings = {
  adjust: 'qfq', compare: undefined, magnet: 'weak_magnet', main: ['MA'], panel: true, period: 'day', subs: ['VOL', 'MACD'],
  marks: { dividends: true, fills: true, reports: true, risk: true, suspensions: true },
  params: { MA: [5, 10, 20, 60] },
};
function loadSettings(): ChartSettings {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '{}');
    return { ...DEFAULTS, ...saved, marks: { ...DEFAULTS.marks, ...saved.marks }, params: { ...DEFAULTS.params, ...saved.params } };
  } catch {
    return { ...DEFAULTS };
  }
}
const settings = reactive<ChartSettings>(loadSettings());
watch(settings, () => localStorage.setItem(STORAGE_KEY, JSON.stringify(settings)), { deep: true });

const MAIN_INDICATORS: Record<string, string> = { BBI: 'BBI 多空', BOLL: 'BOLL 布林', EMA: 'EMA 指数均线', MA: 'MA 均线', SAR: 'SAR 抛物线' };
const SUB_INDICATORS: Record<string, string> = {
  BIAS: 'BIAS 乖离率', BRAR: 'BRAR 人气意愿', CCI: 'CCI 顺势', CR: 'CR 能量', DMA: 'DMA 平均差', DMI: 'DMI 趋向',
  KDJ: 'KDJ 随机', MACD: 'MACD 平滑异同', MTM: 'MTM 动量', OBV: 'OBV 能量潮', PSY: 'PSY 心理线', ROC: 'ROC 变动率',
  RSI: 'RSI 相对强弱', TRIX: 'TRIX 三重指数', VOL: 'VOL 成交量', VR: 'VR 成交量比率', WR: 'WR 威廉',
};
const COMPARE: Record<string, string> = {
  H00300: '沪深300全收益', H00852: '中证1000全收益', H00905: '中证500全收益', sh000001: '上证指数', sh000300: '沪深300',
  sh000688: '科创50', sh000905: '中证500', sz399001: '深证成指', sz399006: '创业板指',
};
const MARK_LABEL: Record<MarkKind, string> = {
  dividends: '除权除息', fills: '买卖点', reports: '财报', risk: '风险警示', suspensions: '停牌',
};
const DRAW_TOOLS = [
  { label: '线段', name: 'segment', tip: '趋势线：两点确定' },
  { label: '射线', name: 'rayLine', tip: '从第一点出发的射线' },
  { label: '直线', name: 'straightLine', tip: '两端延长的直线' },
  { label: '水平', name: 'horizontalStraightLine', tip: '水平线（支撑、压力位）' },
  { label: '横射', name: 'horizontalRayLine', tip: '水平射线' },
  { label: '竖线', name: 'verticalStraightLine', tip: '垂直线（时间位置）' },
  { label: '价格', name: 'priceLine', tip: '带价格标注的水平线' },
  { label: '平行', name: 'parallelStraightLine', tip: '平行线：三点确定' },
  { label: '通道', name: 'priceChannelLine', tip: '价格通道' },
  { label: '黄金', name: 'fibonacciLine', tip: '黄金分割（斐波那契回撤）' },
  { label: '矩形', name: 'rect', tip: '矩形区域' },
  { label: '圆', name: 'circle', tip: '圆' },
  { label: '画笔', name: 'brush', tip: '自由画笔' },
  { label: '注释', name: 'simpleAnnotation', tip: '文字注释（带指向）' },
  { label: '标签', name: 'simpleTag', tip: '价格标签' },
];
const PAGE: Record<BarPeriod, number> = { day: 1000, month: 300, week: 600 };

// -- state --------------------------------------------------------------------------------------
const { isDark } = usePreferences();
const root = ref<HTMLDivElement>();
const container = ref<HTMLDivElement>();
let chart: Chart | null = null;
let loadToken = 0;
const loading = ref(false);
const empty = ref(false);
const marks = ref<ChartMarks>();
const barEvents = ref<Record<number, string[]>>({}); // bar timestamp -> event lines for the data window
const hovered = ref<KLineData>();
const hoveredIndex = ref(-1);
const tool = ref('');
const drawingsLocked = ref(false);
const drawingsHidden = ref(false);
const drawingCount = ref(0);
const range = ref<null | RangeStats>();
const fullscreen = ref(false);
const paramsDialog = reactive<{ name: string; open: boolean; text: string }>({ name: '', open: false, text: '' });
let quiet = false; // true while overlays are removed or restored by code, so no save is triggered
let resizeObserver: null | ResizeObserver = null;

const heightStyle = computed(() => (typeof props.height === 'number' ? `${props.height}px` : props.height));
const dataVersion = ref(0); // bumped after every load, so views of the (non-reactive) data list refresh
const shown = computed(() => (dataVersion.value, hovered.value ?? chart?.getDataList().at(-1)));
const shownPrev = computed(() => {
  void dataVersion.value;
  const data = chart?.getDataList() ?? [];
  const index = hovered.value ? hoveredIndex.value : data.length - 1;
  return index > 0 ? data[index - 1] : undefined;
});

/** The bar under a chart x coordinate (the crosshair action only carries coordinates). */
function hover(x?: number) {
  if (!chart || x === undefined) {
    hovered.value = undefined;
    hoveredIndex.value = -1;
    return;
  }
  const [point] = chart.convertFromPixel([{ x }], { paneId: 'candle_pane' }) as { dataIndex?: number }[];
  const data = chart.getDataList();
  const index = point?.dataIndex;
  if (index === undefined || index < 0 || index >= data.length) return;
  hovered.value = data[index];
  hoveredIndex.value = index;
}

// -- data ---------------------------------------------------------------------------------------
function day(timestamp: number): string {
  return new Date(timestamp + 8 * 3_600_000).toISOString().slice(0, 10); // bars are stamped at 00:00 Beijing time
}
function stamp(date: string): number {
  return new Date(`${date}T00:00:00+08:00`).getTime();
}
function toData(b: Record<string, any>): KLineData {
  return {
    amplitude: b.amplitude, close: b.close, high: b.high, low: b.low, open: b.open, pct_change: b.pct_change,
    timestamp: stamp(b.trade_date), turnover: b.amount ?? undefined, turnover_rate: b.turnover_rate,
    volume: b.volume ?? undefined,
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

const loader: DataLoader = {
  getBars: async ({ callback, timestamp, type }) => {
    const token = loadToken;
    const { adjust, period } = settings;
    const symbol = props.symbol;
    try {
      if (type === 'init') {
        loading.value = true;
        const bars = await barsApi(symbol, adjust, PAGE[period], period);
        if (token !== loadToken) return;
        empty.value = bars.length === 0;
        callback(bars.map(toData), { backward: false, forward: bars.length >= PAGE[period] });
        dataVersion.value += 1;
        await nextTick();
        await afterInit(token);
      } else if (type === 'forward' && timestamp) {
        const bars = await barsApi(symbol, adjust, PAGE[period], period, beforePeriod(day(timestamp), period));
        if (token !== loadToken) return;
        callback(bars.map(toData), { backward: false, forward: bars.length >= PAGE[period] });
        dataVersion.value += 1;
        if (hoveredIndex.value >= 0) hoveredIndex.value += bars.length; // older bars were put in front
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
  hovered.value = undefined;
  range.value = null;
  const symbol = props.symbol;
  const [m, saved] = await Promise.all([marksApi(symbol).catch(() => undefined), drawingsApi(symbol).catch(() => undefined)]);
  if (token !== loadToken || !chart) return;
  marks.value = m;
  renderMarks();
  quiet = true;
  chart.removeOverlay({ groupId: 'drawings' });
  for (const overlay of saved?.overlays ?? []) chart.createOverlay(drawingOverlay(overlay.name, overlay));
  quiet = false;
  drawingCount.value = saved?.overlays.length ?? 0;
  applyDrawingState();
}

// -- marks --------------------------------------------------------------------------------------
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

const PERIOD_NAMES: Record<number, string> = { 3: '一季报', 6: '中报', 9: '三季报', 12: '年报' };
function reportName(reportDate: string): string {
  const [y, m] = reportDate.split('-');
  return `${y} 年${PERIOD_NAMES[Number(m)] ?? '报告'}`;
}

function renderMarks() {
  if (!chart) return;
  quiet = true;
  chart.removeOverlay({ groupId: 'marks' });
  quiet = false;
  const data = chart.getDataList();
  const m = marks.value;
  if (!data.length || !m) return;
  const slots = new Map<number, { above: MarkData[]; below: MarkData[] }>();
  const events: Record<number, string[]> = {};
  const add = (kind: MarkKind, date: string, badge: Omit<MarkData, 'stack'>, line: string) => {
    const index = barIndexFor(data, date);
    if (index < 0) return;
    const t = data[index]!.timestamp;
    (events[t] ??= []).push(line);
    if (!settings.marks[kind]) return;
    const slot = slots.get(index) ?? { above: [], below: [] };
    const side = badge.below ? slot.below : slot.above;
    if (!side.some((b) => b.text === badge.text)) side.push({ ...badge, stack: side.length });
    slots.set(index, slot);
  };
  for (const f of m.fills) {
    add('fills', f.trade_date, { below: f.side === 'buy', color: f.side === 'buy' ? UP_COLOR : DOWN_COLOR, text: f.side === 'buy' ? 'B' : 'S' },
        `${f.side === 'buy' ? '买入' : '卖出'} ${f.qty.toLocaleString()} 股 @ ${f.price.toFixed(2)}（${f.account_name}）`);
  }
  for (const d of m.dividends) add('dividends', d.ex_date, { below: false, color: '#f59e0b', text: '除' }, `除权除息：${d.plan_profile ?? ''}`);
  for (const r of m.reports) add('reports', r.notice_date, { below: false, color: '#3b82f6', text: '报' }, `发布${reportName(r.report_date)}`);
  for (const r of m.risk) {
    add('risk', r.start_date, { below: false, color: '#a855f7', text: r.status.replace('DELISTING', '退') },
        `实施风险警示 ${r.status}${r.start_title ? `：${r.start_title}` : ''}`);
    if (r.end_date) add('risk', r.end_date, { below: false, color: '#a855f7', text: '摘' }, `撤销风险警示 ${r.status}`);
  }
  for (const s of m.suspensions) {
    add('suspensions', s.suspend_start, { below: false, color: '#6b7280', text: '停' },
        `停牌${s.suspend_end ? ` 至 ${s.suspend_end}` : ''}${s.reason ? `：${s.reason}` : ''}`);
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
}

// -- indicators -----------------------------------------------------------------------------------
function paramsOf(name: string): number[] | undefined {
  return settings.params[name];
}
function subPane(name: string): string {
  return `pane_${name}`;
}
function applyIndicators() {
  if (!chart) return;
  const onMain = new Set(chart.getIndicators({ paneId: 'candle_pane' }).map((i) => i.name));
  for (const name of Object.keys(MAIN_INDICATORS)) {
    const want = settings.main.includes(name);
    if (want && !onMain.has(name)) chart.createIndicator({ calcParams: paramsOf(name), name, paneId: 'candle_pane' }, true);
    if (!want && onMain.has(name)) chart.removeIndicator({ name, paneId: 'candle_pane' });
  }
  for (const name of Object.keys(SUB_INDICATORS)) {
    const paneId = subPane(name);
    const has = chart.getIndicators({ paneId }).length > 0;
    const want = settings.subs.includes(name);
    if (want && !has) {
      chart.createIndicator({ calcParams: paramsOf(name), name, paneId });
      chart.setPaneOptions({ height: 110, id: paneId, minHeight: 50 });
    }
    if (!want && has) chart.removeIndicator({ paneId });
  }
}

async function applyCompare() {
  if (!chart) return;
  chart.removeIndicator({ name: 'CMP', paneId: 'candle_pane' });
  compareCloses = null;
  const code = settings.compare;
  if (!code) return;
  const bars = await indexBarsApi(code, 6000).catch(() => []);
  if (!chart || settings.compare !== code) return;
  const closes = Object.fromEntries(bars.map((b) => [b.trade_date, b.close]));
  compareCloses = closes;
  chart.createIndicator({ extendData: { base: firstVisible(), closes, label: COMPARE[code] }, name: 'CMP', paneId: 'candle_pane',
                          shortName: `对比 ${COMPARE[code]}`, styles: { lines: [{ color: '#f59e0b', size: 1.5 }] } }, true);
}

let compareCloses: null | Record<string, number> = null;
let rebaseTimer: null | ReturnType<typeof setTimeout> = null;
function firstVisible(): number | undefined {
  const data = chart?.getDataList() ?? [];
  const { from } = chart?.getVisibleRange() ?? { from: 0 };
  return data[Math.max(0, from)]?.timestamp;
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

function openParams(name: string) {
  const current = chart?.getIndicators({ name })[0]?.calcParams as number[] | undefined;
  Object.assign(paramsDialog, { name, open: true, text: (paramsOf(name) ?? current ?? []).join(', ') });
}
function saveParams() {
  const values = paramsDialog.text.split(/[,，\s]+/).filter(Boolean).map(Number);
  if (!values.length || values.some((v) => !Number.isFinite(v) || v <= 0)) {
    message.warning('参数须为正数，用逗号分隔');
    return;
  }
  settings.params[paramsDialog.name] = values;
  chart?.overrideIndicator({ calcParams: values, name: paramsDialog.name });
  paramsDialog.open = false;
}
function resetParams() {
  delete settings.params[paramsDialog.name];
  paramsDialog.open = false;
  // Recreate with the library defaults.
  const name = paramsDialog.name;
  if (settings.main.includes(name)) {
    chart?.removeIndicator({ name, paneId: 'candle_pane' });
  } else {
    chart?.removeIndicator({ paneId: subPane(name) });
  }
  applyIndicators();
}
const activeIndicators = computed(() => [...settings.main, ...settings.subs]);

// -- drawings -------------------------------------------------------------------------------------
function drawingOverlay(name: string, saved?: SavedOverlay): OverlayCreate {
  return {
    extendData: saved?.extendData,
    groupId: 'drawings',
    lock: saved?.lock ?? drawingsLocked.value,
    mode: settings.magnet,
    name,
    points: saved?.points,
    styles: (saved?.styles as OverlayCreate['styles']) ?? undefined,
    visible: !drawingsHidden.value,
    onDrawEnd: () => {
      tool.value = '';
      scheduleSave();
    },
    onPressedMoveEnd: () => scheduleSave(),
    onRemoved: () => scheduleSave(),
    onRightClick: (event) => {
      chart?.removeOverlay({ id: event.overlay.id });
    },
  };
}

function startTool(name: string) {
  if (!chart) return;
  if (name === 'simpleAnnotation' || name === 'simpleTag') {
    let text = '';
    Modal.confirm({
      content: () => h(Input, { maxlength: 60, onChange: (e: any) => (text = e.target.value), placeholder: '输入文字' }),
      okText: '开始放置',
      onOk: () => {
        tool.value = name;
        chart?.createOverlay({ ...drawingOverlay(name), extendData: text || (name === 'simpleTag' ? '' : '注释') });
      },
      title: name === 'simpleTag' ? '标签文字（可留空，显示价格）' : '注释文字',
    });
    return;
  }
  tool.value = name;
  chart.createOverlay(drawingOverlay(name));
}

let saveTimer: null | ReturnType<typeof setTimeout> = null;
function scheduleSave() {
  if (quiet || !chart) return;
  const symbol = props.symbol;
  const overlays = serialize();
  drawingCount.value = overlays.length;
  if (saveTimer) clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    saveTimer = null;
    saveDrawingsApi(symbol, overlays).catch(() => message.error('画线保存失败'));
  }, 800);
}
function serialize(): SavedOverlay[] {
  return (chart?.getOverlays({ groupId: 'drawings' }) ?? [])
    .filter((o: Overlay) => o.points.length > 0 && o.points.every((p) => p.timestamp !== undefined && p.value !== undefined))
    .map((o: Overlay) => ({ extendData: o.extendData, lock: o.lock, name: o.name,
                            points: o.points.map((p) => ({ timestamp: p.timestamp!, value: p.value! })),
                            styles: o.styles ?? undefined }));
}
function applyDrawingState() {
  chart?.overrideOverlay({ groupId: 'drawings', lock: drawingsLocked.value, mode: settings.magnet, visible: !drawingsHidden.value });
}
function clearDrawings() {
  chart?.removeOverlay({ groupId: 'drawings' });
  drawingCount.value = 0;
  if (saveTimer) clearTimeout(saveTimer);
  saveDrawingsApi(props.symbol, []).catch(() => message.error('画线保存失败'));
}
const MAGNET: Record<OverlayMode, string> = { normal: '不吸附', strong_magnet: '强吸附', weak_magnet: '弱吸附' };
function cycleMagnet() {
  const order: OverlayMode[] = ['weak_magnet', 'strong_magnet', 'normal'];
  settings.magnet = order[(order.indexOf(settings.magnet) + 1) % order.length]!;
  applyDrawingState();
}

// -- range statistics -----------------------------------------------------------------------------
function startRange() {
  if (!chart) return;
  quiet = true;
  chart.removeOverlay({ groupId: 'range' });
  quiet = false;
  range.value = null;
  tool.value = 'rangeStat';
  const update = (event: { overlay: Overlay }) => {
    const [a, b] = event.overlay.points;
    range.value = a?.timestamp && b?.timestamp ? rangeStats(chart!.getDataList(), a.timestamp, b.timestamp) : null;
  };
  chart.createOverlay({
    groupId: 'range', name: 'rangeStat',
    onDrawEnd: (e) => { tool.value = ''; update(e); },
    onPressedMoveEnd: update,
    onRemoved: () => (range.value = null),
  });
}
function clearRange() {
  chart?.removeOverlay({ groupId: 'range' });
  range.value = null;
}

// -- keyboard, fullscreen, picture ----------------------------------------------------------------
function moveCrosshair(step: number) {
  if (!chart) return;
  const data = chart.getDataList();
  if (!data.length) return;
  const index = Math.min(data.length - 1, Math.max(0, (hoveredIndex.value < 0 ? data.length - 1 : hoveredIndex.value) + step));
  const { from, to } = chart.getVisibleRange();
  if (index < from || index >= to) chart.scrollToDataIndex(index);
  const pixel = chart.convertToPixel({ dataIndex: index, value: data[index]!.close }, { paneId: 'candle_pane' }) as { x?: number; y?: number };
  if (pixel.x !== undefined) chart.executeAction('onCrosshairChange', { paneId: 'candle_pane', x: pixel.x, y: pixel.y });
  hovered.value = data[index]; // the programmatic crosshair does not fire the action
  hoveredIndex.value = index;
}
function onKeydown(event: KeyboardEvent) {
  if (!chart || (event.target as HTMLElement)?.tagName === 'INPUT') return;
  const index = hoveredIndex.value < 0 ? chart.getDataList().length - 1 : hoveredIndex.value;
  const handlers: Record<string, () => void> = {
    ArrowDown: () => chart!.zoomAtDataIndex(0.8, index),
    ArrowLeft: () => moveCrosshair(-1),
    ArrowRight: () => moveCrosshair(1),
    ArrowUp: () => chart!.zoomAtDataIndex(1.25, index),
    End: () => chart!.scrollToRealTime(),
    Escape: () => {
      tool.value = '';
      hovered.value = undefined;
      hoveredIndex.value = -1;
    },
  };
  const handler = handlers[event.key];
  if (handler) {
    event.preventDefault();
    handler();
  }
}
async function toggleFullscreen() {
  if (document.fullscreenElement) await document.exitFullscreen();
  else await root.value?.requestFullscreen();
}
function onFullscreenChange() {
  fullscreen.value = document.fullscreenElement === root.value;
  setTimeout(() => chart?.resize(), 50);
}
function savePicture() {
  if (!chart) return;
  const url = chart.getConvertPictureUrl(true, 'png', isDark.value ? '#151517' : '#ffffff');
  Object.assign(document.createElement('a'), { download: `${props.symbol}-${settings.period}.png`, href: url }).click();
}

// -- setup ------------------------------------------------------------------------------------------
function themeStyles() {
  const bar = { downBorderColor: DOWN_COLOR, downColor: DOWN_COLOR, downWickColor: DOWN_COLOR, noChangeBorderColor: '#888888',
                noChangeColor: '#888888', noChangeWickColor: '#888888', upBorderColor: UP_COLOR, upColor: UP_COLOR,
                upWickColor: UP_COLOR };
  return {
    candle: { bar, priceMark: { last: { downColor: DOWN_COLOR, noChangeColor: '#888888', upColor: UP_COLOR } },
              tooltip: { showRule: 'always', showType: 'standard' } },
    grid: { vertical: { show: false } },
    indicator: { bars: [{ downColor: 'rgba(22, 163, 74, 0.75)', noChangeColor: '#888888', upColor: 'rgba(229, 72, 77, 0.75)' }] },
  } as any;
}
function applyTheme() {
  chart?.setStyles(isDark.value ? 'dark' : 'light');
  chart?.setStyles(themeStyles());
}

function reload() {
  loadToken += 1;
  quiet = true;
  chart?.removeOverlay({ groupId: 'marks' });
  chart?.removeOverlay({ groupId: 'drawings' });
  chart?.removeOverlay({ groupId: 'range' });
  quiet = false;
  range.value = null;
}

onMounted(() => {
  registerChartExtensions();
  if (!container.value) return;
  chart = init(container.value, { locale: 'zh-CN', timezone: 'Asia/Shanghai' });
  if (!chart) return;
  applyTheme();
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
  document.addEventListener('fullscreenchange', onFullscreenChange);
});

onBeforeUnmount(() => {
  if (saveTimer) {
    clearTimeout(saveTimer);
    saveDrawingsApi(props.symbol, serialize()).catch(() => undefined);
  }
  resizeObserver?.disconnect();
  document.removeEventListener('fullscreenchange', onFullscreenChange);
  if (container.value) dispose(container.value);
  chart = null;
});

watch(() => props.symbol, (symbol, previous) => {
  if (saveTimer && previous) { // flush the previous stock's drawings first
    clearTimeout(saveTimer);
    saveTimer = null;
    saveDrawingsApi(previous, serialize()).catch(() => undefined);
  }
  reload();
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
watch(() => ({ ...settings.marks }), renderMarks);
watch(isDark, applyTheme);
watch(() => settings.panel, () => nextTick(() => chart?.resize()));

// -- data window ----------------------------------------------------------------------------------
function fmt(v: unknown, digits = 2): string {
  return typeof v === 'number' && Number.isFinite(v) ? v.toFixed(digits) : '—';
}
function colorOf(value?: number): string | undefined {
  if (value === undefined || !Number.isFinite(value) || value === 0) return undefined;
  return value > 0 ? UP_COLOR : DOWN_COLOR;
}
const shownChange = computed(() => {
  const bar = shown.value;
  if (!bar) return undefined;
  const pct = bar.pct_change as null | number | undefined;
  if (pct !== null && pct !== undefined) return pct / 100;
  return shownPrev.value ? bar.close / shownPrev.value.close - 1 : undefined;
});
const shownEvents = computed(() => (shown.value ? barEvents.value[shown.value.timestamp] ?? [] : []));
const PERIOD_LABEL: Record<BarPeriod, string> = { day: '日', month: '月', week: '周' };
</script>

<template>
  <div ref="root" class="stock-chart flex flex-col" :class="{ 'is-fullscreen': fullscreen }" :style="{ height: fullscreen ? '100vh' : heightStyle }"
       tabindex="0" @keydown="onKeydown">
    <!-- top toolbar -->
    <div class="flex flex-wrap items-center gap-2 border-b px-2 py-1.5">
      <b v-if="name" class="mr-1">{{ name }}</b>
      <Segmented v-model:value="settings.period" size="small"
                 :options="[{ label: '日K', value: 'day' }, { label: '周K', value: 'week' }, { label: '月K', value: 'month' }]" />
      <Segmented v-model:value="settings.adjust" size="small"
                 :options="[{ label: '前复权', value: 'qfq' }, { label: '不复权', value: 'none' }, { label: '后复权', value: 'hfq' }]" />
      <Select v-model:value="settings.main" mode="multiple" size="small" :max-tag-count="2" placeholder="主图指标" style="min-width: 170px"
              :options="Object.entries(MAIN_INDICATORS).map(([value, label]) => ({ label, value }))" />
      <Select v-model:value="settings.subs" mode="multiple" size="small" :max-tag-count="3" placeholder="副图指标" style="min-width: 220px"
              :options="Object.entries(SUB_INDICATORS).map(([value, label]) => ({ label, value }))" />
      <Dropdown :trigger="['click']">
        <Button size="small">指标参数</Button>
        <template #overlay>
          <Menu @click="({ key }: any) => openParams(String(key))">
            <Menu.Item v-for="n in activeIndicators" :key="n">{{ MAIN_INDICATORS[n] ?? SUB_INDICATORS[n] ?? n }}</Menu.Item>
          </Menu>
        </template>
      </Dropdown>
      <Select v-model:value="settings.compare" allow-clear size="small" placeholder="叠加指数对比" style="width: 150px"
              :options="Object.entries(COMPARE).map(([value, label]) => ({ label, value }))" />
      <Dropdown :trigger="['click']">
        <Button size="small">标记</Button>
        <template #overlay>
          <div class="bg-background rounded p-2 shadow" @click.stop>
            <div v-for="(label, kind) in MARK_LABEL" :key="kind"><Checkbox v-model:checked="settings.marks[kind]">{{ label }}</Checkbox></div>
          </div>
        </template>
      </Dropdown>
      <div class="flex-1"></div>
      <span v-if="loading" class="text-muted-foreground text-xs">加载中…</span>
      <Tooltip title="框选一段 K 线，统计涨跌幅、振幅、成交">
        <Button size="small" :type="tool === 'rangeStat' ? 'primary' : 'default'" @click="startRange">区间统计</Button>
      </Tooltip>
      <Button size="small" @click="savePicture">截图</Button>
      <Button size="small" @click="toggleFullscreen">{{ fullscreen ? '退出全屏' : '全屏' }}</Button>
      <Button size="small" @click="settings.panel = !settings.panel">{{ settings.panel ? '隐藏数据' : '数据窗口' }}</Button>
    </div>

    <div class="flex min-h-0 flex-1">
      <!-- drawing tools -->
      <div class="draw-bar flex w-12 flex-col items-center gap-1 overflow-y-auto border-r py-1">
        <Tooltip v-for="t in DRAW_TOOLS" :key="t.name" :title="t.tip" placement="right">
          <button class="draw-btn" :class="{ active: tool === t.name }" @click="startTool(t.name)">{{ t.label }}</button>
        </Tooltip>
        <Divider class="my-1" />
        <Tooltip :title="`画线端点吸附到开高低收：${MAGNET[settings.magnet]}`" placement="right">
          <button class="draw-btn" :class="{ active: settings.magnet !== 'normal' }" @click="cycleMagnet">{{ MAGNET[settings.magnet].slice(0, 2) }}</button>
        </Tooltip>
        <Tooltip :title="drawingsLocked ? '解锁画线' : '锁定画线（防止误拖）'" placement="right">
          <button class="draw-btn" :class="{ active: drawingsLocked }" @click="drawingsLocked = !drawingsLocked; applyDrawingState()">{{ drawingsLocked ? '解锁' : '锁定' }}</button>
        </Tooltip>
        <Tooltip :title="drawingsHidden ? '显示画线' : '隐藏画线'" placement="right">
          <button class="draw-btn" :class="{ active: drawingsHidden }" @click="drawingsHidden = !drawingsHidden; applyDrawingState()">{{ drawingsHidden ? '显示' : '隐藏' }}</button>
        </Tooltip>
        <Popconfirm :title="`删除这只股票上的全部 ${drawingCount} 条画线？`" placement="right" @confirm="clearDrawings">
          <button class="draw-btn danger" :disabled="!drawingCount">清空</button>
        </Popconfirm>
      </div>

      <!-- chart -->
      <div class="relative min-w-0 flex-1">
        <div ref="container" class="absolute inset-0"></div>
        <div v-if="empty && !loading" class="text-muted-foreground absolute inset-0 flex items-center justify-center">没有行情数据</div>
      </div>

      <!-- data window -->
      <div v-if="settings.panel" class="data-panel w-56 shrink-0 overflow-y-auto border-l p-2 text-xs">
        <template v-if="shown">
          <div class="mb-1 font-semibold">{{ day(shown.timestamp) }}（{{ PERIOD_LABEL[settings.period] }}K{{ hovered ? '' : '·最新' }}）</div>
          <div class="grid grid-cols-2 gap-x-2 gap-y-0.5">
            <span class="text-muted-foreground">开盘</span><span class="text-right">{{ fmt(shown.open) }}</span>
            <span class="text-muted-foreground">最高</span><span class="text-right" :style="{ color: UP_COLOR }">{{ fmt(shown.high) }}</span>
            <span class="text-muted-foreground">最低</span><span class="text-right" :style="{ color: DOWN_COLOR }">{{ fmt(shown.low) }}</span>
            <span class="text-muted-foreground">收盘</span><span class="text-right" :style="{ color: colorOf(shownChange) }">{{ fmt(shown.close) }}</span>
            <span class="text-muted-foreground">涨跌幅</span>
            <span class="text-right" :style="{ color: colorOf(shownChange) }">{{ shownChange === undefined ? '—' : `${shownChange > 0 ? '+' : ''}${(shownChange * 100).toFixed(2)}%` }}</span>
            <span class="text-muted-foreground">振幅</span><span class="text-right">{{ fmt(shown.amplitude) }}%</span>
            <span class="text-muted-foreground">成交量</span><span class="text-right">{{ shown.volume ? `${(shown.volume / 1e6).toFixed(2)} 万手` : '—' }}</span>
            <span class="text-muted-foreground">成交额</span><span class="text-right">{{ shown.turnover ? `${bigYuan(shown.turnover, false)}` : '—' }}</span>
            <span class="text-muted-foreground">换手率</span><span class="text-right">{{ fmt(shown.turnover_rate) }}%</span>
          </div>
          <template v-if="shownEvents.length">
            <Divider class="my-2" />
            <div class="mb-1 font-semibold">当期事件</div>
            <div v-for="(line, k) in shownEvents" :key="k" class="mb-0.5">{{ line }}</div>
          </template>
        </template>
        <template v-if="range">
          <Divider class="my-2" />
          <div class="mb-1 flex items-center justify-between font-semibold">
            <span>区间统计</span><a class="font-normal" @click="clearRange">清除</a>
          </div>
          <div class="grid grid-cols-2 gap-x-2 gap-y-0.5">
            <span class="text-muted-foreground">起止</span><span class="text-right">{{ day(range.start.timestamp).slice(2) }} ~ {{ day(range.end.timestamp).slice(2) }}</span>
            <span class="text-muted-foreground">K 线数</span><span class="text-right">{{ range.bars }}</span>
            <span class="text-muted-foreground">涨跌幅</span>
            <span class="text-right" :style="{ color: colorOf(range.change) }">{{ range.change > 0 ? '+' : '' }}{{ (range.change * 100).toFixed(2) }}%</span>
            <span class="text-muted-foreground">最高 / 最低</span><span class="text-right">{{ fmt(range.high) }} / {{ fmt(range.low) }}</span>
            <span class="text-muted-foreground">振幅</span><span class="text-right">{{ (range.amplitude * 100).toFixed(2) }}%</span>
            <span class="text-muted-foreground">成交额</span><span class="text-right">{{ bigYuan(range.amount, false) }}</span>
            <span class="text-muted-foreground">换手率</span><span class="text-right">{{ range.turnoverRate === null ? '—' : `${range.turnoverRate.toFixed(2)}%` }}</span>
          </div>
        </template>
        <Divider class="my-2" />
        <div class="text-muted-foreground leading-5">
          ←/→ 逐根移动，↑/↓ 放大缩小，End 回到最新；鼠标拖动平移、滚轮缩放。画线：点左侧工具后在图上点击放置，拖动端点调整，右键删除，自动保存。
        </div>
      </div>
    </div>

    <Modal v-model:open="paramsDialog.open" :title="`${MAIN_INDICATORS[paramsDialog.name] ?? SUB_INDICATORS[paramsDialog.name] ?? paramsDialog.name} 参数`">
      <Space direction="vertical" class="w-full">
        <Input v-model:value="paramsDialog.text" placeholder="例如 5, 10, 20, 60" />
        <span class="text-muted-foreground text-xs">多个参数用逗号分隔；MA、EMA 可以写多个周期。</span>
      </Space>
      <template #footer>
        <Button @click="resetParams">恢复默认</Button>
        <Button type="primary" @click="saveParams">应用</Button>
      </template>
    </Modal>
  </div>
</template>

<style scoped>
.stock-chart {
  outline: none;
  background: hsl(var(--background));
}

.stock-chart.is-fullscreen {
  padding: 4px;
}

.draw-btn {
  width: 38px;
  padding: 3px 0;
  font-size: 12px;
  line-height: 16px;
  color: hsl(var(--foreground));
  cursor: pointer;
  background: transparent;
  border: 1px solid transparent;
  border-radius: 4px;
}

.draw-btn:hover {
  background: hsl(var(--accent));
}

.draw-btn.active {
  color: #fff;
  background: hsl(var(--primary));
}

.draw-btn.danger {
  color: #e5484d;
}

.draw-btn:disabled {
  cursor: not-allowed;
  opacity: 0.4;
}
</style>
