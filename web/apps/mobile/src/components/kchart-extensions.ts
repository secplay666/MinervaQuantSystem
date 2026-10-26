/**
 * KLineChart extensions of the phone chart (ported from the PC
 * components/stock-chart/extensions.ts without the drawing tools): event
 * badges, read-only lines and bands, the index comparison line and the
 * shareholder panes.  Registered once.
 */
import type { KLineData, OverlayFigure } from 'klinecharts';

import { registerIndicator, registerOverlay } from 'klinecharts';

export interface MarkData {
  below: boolean; // under the low (buys) or above the high (everything else)
  color: string;
  stack: number; // badges on the same bar and side are stacked
  text: string;
}

/** Read-only lines: through points, or running on to the right edge; a price band. */
export interface AutoLineData {
  color: string;
  dashed?: boolean;
  dots?: boolean;
  extend?: boolean;
  label?: string;
  width?: number;
}

/** A shareholder count at its count date (ms, 00:00 Beijing time); stakes in % (periodic reports only). */
export interface HolderPoint {
  holders: null | number;
  t: number;
  top10: null | number;
}

/** For each bar, the latest point dated on or before it that has a value (a step line between counts). */
export function carryForward(data: KLineData[], points: HolderPoint[], pick: (p: HolderPoint) => null | number): (number | undefined)[] {
  const known = points.filter((p) => pick(p) !== null);
  let k = -1;
  return data.map((d) => {
    while (k + 1 < known.length && known[k + 1]!.t <= d.timestamp) k += 1;
    return k >= 0 ? (pick(known[k]!) as number) : undefined;
  });
}

/** Right-edge labels stay clear of the price labels of the inside y axis. */
const AXIS_GAP = 50;

let registered = false;

export function registerChartExtensions() {
  if (registered) return;
  registered = true;

  registerOverlay<MarkData>({
    name: 'eventMark',
    totalStep: 2,
    lock: true,
    needDefaultPointFigure: false,
    needDefaultXAxisFigure: false,
    needDefaultYAxisFigure: false,
    createPointFigures: ({ coordinates, overlay }) => {
      const c = coordinates[0];
      const mark = overlay.extendData;
      if (!c || !mark) return [];
      const shift = 6 + mark.stack * 17;
      return [{
        type: 'text',
        attrs: { x: c.x, y: mark.below ? c.y + shift : c.y - shift, text: mark.text, align: 'center', baseline: mark.below ? 'top' : 'bottom' },
        styles: { style: 'fill', color: '#ffffff', size: 11, weight: 'bold', family: 'Helvetica Neue, sans-serif',
                  backgroundColor: mark.color, borderRadius: 2, paddingLeft: 3, paddingRight: 3, paddingTop: 1, paddingBottom: 1, borderSize: 0 },
        ignoreEvent: true,
      }];
    },
  });

  const label = (x: number, y: number, text: string, color: string, align: CanvasTextAlign = 'left'): OverlayFigure => ({
    type: 'text',
    attrs: { x, y, text, align, baseline: 'bottom' },
    styles: { style: 'fill', color: '#ffffff', size: 10, backgroundColor: color, borderRadius: 2, paddingLeft: 3,
              paddingRight: 3, paddingTop: 1, paddingBottom: 1, borderSize: 0 },
    ignoreEvent: true,
  });
  const lineStyle = (d: AutoLineData) => ({ color: d.color, size: d.width ?? 1, style: d.dashed ? 'dashed' : 'solid', dashedValue: [4, 3] });

  registerOverlay<AutoLineData>({
    name: 'autoPolyline',
    totalStep: 2,
    lock: true,
    needDefaultPointFigure: false,
    needDefaultXAxisFigure: false,
    needDefaultYAxisFigure: false,
    createPointFigures: ({ coordinates, overlay }) => {
      const d = overlay.extendData;
      if (!d || coordinates.length < 2) return [];
      const figures: OverlayFigure[] = [{ type: 'line', attrs: { coordinates }, styles: lineStyle(d), ignoreEvent: true }];
      if (d.dots) {
        for (const c of coordinates) figures.push({ type: 'circle', attrs: { x: c.x, y: c.y, r: 2.5 }, styles: { style: 'fill', color: d.color }, ignoreEvent: true });
      }
      if (d.label) {
        const top = coordinates.reduce((a, b) => (b.y < a.y ? b : a));
        figures.push(label(top.x, top.y - 6, d.label, d.color, 'center'));
      }
      return figures;
    },
  });

  registerOverlay<AutoLineData>({
    name: 'autoLine',
    totalStep: 3,
    lock: true,
    needDefaultPointFigure: false,
    needDefaultXAxisFigure: false,
    needDefaultYAxisFigure: false,
    createPointFigures: ({ bounding, coordinates, overlay }) => {
      const d = overlay.extendData;
      const [a, b] = coordinates;
      if (!d || !a || !b || Math.min(a.x, b.x) > bounding.width) return []; // starts right of the screen: nothing to label
      let end = b;
      if (d.extend && b.x !== a.x && bounding.width > b.x) {
        end = { x: bounding.width, y: a.y + ((b.y - a.y) / (b.x - a.x)) * (bounding.width - a.x) };
      }
      const figures: OverlayFigure[] = [{ type: 'line', attrs: { coordinates: [a, end] }, styles: lineStyle(d), ignoreEvent: true }];
      if (d.label) figures.push(label(Math.min(end.x, bounding.width - AXIS_GAP), end.y - 2, d.label, d.color, 'right'));
      return figures;
    },
  });

  registerOverlay<AutoLineData>({
    name: 'priceZone',
    totalStep: 3,
    lock: true,
    needDefaultPointFigure: false,
    needDefaultXAxisFigure: false,
    needDefaultYAxisFigure: false,
    createPointFigures: ({ bounding, coordinates, overlay }) => {
      const d = overlay.extendData;
      const [a, b] = coordinates;
      if (!d || !a || !b || a.x > bounding.width) return [];
      const top = Math.min(a.y, b.y);
      const height = Math.max(2, Math.abs(b.y - a.y));
      const x = Math.max(0, Math.min(a.x, bounding.width));
      return [
        { type: 'rect', attrs: { x, y: top, width: bounding.width - x, height }, styles: { style: 'fill', color: `${d.color}22` }, ignoreEvent: true },
        { type: 'line', attrs: { coordinates: [{ x, y: top + height / 2 }, { x: bounding.width, y: top + height / 2 }] },
          styles: { color: d.color, size: 1, style: 'dashed', dashedValue: [2, 3] }, ignoreEvent: true },
        ...(d.label ? [label(bounding.width - AXIS_GAP, top - 1, d.label, d.color, 'right')] : []),
      ];
    },
  });

  // A sentinel of the position manager: a blue dash-dot line across the pane with its label (read-only on phones).
  registerOverlay<{ label: string }>({
    name: 'sentinelLine',
    totalStep: 2,
    lock: true,
    needDefaultPointFigure: false,
    needDefaultXAxisFigure: false,
    needDefaultYAxisFigure: false,
    createPointFigures: ({ bounding, coordinates, overlay }) => {
      const [a] = coordinates;
      if (!a) return [];
      return [
        { type: 'line', attrs: { coordinates: [{ x: 0, y: a.y }, { x: bounding.width, y: a.y }] },
          styles: { color: '#2563eb', size: 1, style: 'dashed', dashedValue: [6, 3, 2, 3] }, ignoreEvent: true },
        label(bounding.width - AXIS_GAP, a.y - 2, overlay.extendData?.label ?? '⚓哨兵', '#2563eb', 'right'),
      ];
    },
  });

  // Index comparison on the price pane: the index rebased to the stock's close at ``base`` (the first visible bar).
  registerIndicator<{ cmp?: number }, number, { base?: number; closes: Record<string, number>; label: string }>({
    name: 'CMP',
    shortName: '对比',
    series: 'price',
    precision: 2,
    calcParams: [],
    figures: [{ key: 'cmp', title: '相对走势: ', type: 'line' }],
    regenerateFigures: () => [{ key: 'cmp', title: '相对走势: ', type: 'line' }],
    calc: (dataList, indicator) => {
      const closes = indicator.extendData?.closes ?? {};
      const base = indicator.extendData?.base ?? dataList[0]?.timestamp ?? 0;
      const dayOf = (t: number) => new Date(t + 8 * 3_600_000).toISOString().slice(0, 10);
      const anchor = dataList.find((d) => d.timestamp >= base && closes[dayOf(d.timestamp)] !== undefined);
      if (!anchor) return dataList.map(() => ({}));
      const ratio = anchor.close / closes[dayOf(anchor.timestamp)]!;
      return dataList.map((d) => {
        const value = closes[dayOf(d.timestamp)];
        return value === undefined ? {} : { cmp: value * ratio };
      });
    },
  });

  // Shareholder panes: step lines through the counts by count date, carried to the next count.
  registerIndicator<{ holders?: number }, number, { points: HolderPoint[] }>({
    name: 'HOLDERS',
    shortName: '股东户数',
    precision: 2,
    calcParams: [],
    figures: [{ key: 'holders', title: '万户: ', type: 'line' }],
    calc: (dataList, indicator) => carryForward(dataList, indicator.extendData?.points ?? [], (p) => (p.holders === null ? null : p.holders / 1e4))
      .map((v) => (v === undefined ? {} : { holders: v })),
  });
  registerIndicator<{ big?: number; small?: number }, number, { points: HolderPoint[] }>({
    name: 'TOP10',
    shortName: '大股东/小股东',
    precision: 2,
    calcParams: [],
    figures: [{ key: 'big', title: '前十大股东%: ', type: 'line' }, { key: 'small', title: '其余股东%: ', type: 'line' }],
    calc: (dataList, indicator) => carryForward(dataList, indicator.extendData?.points ?? [], (p) => p.top10)
      .map((v) => (v === undefined ? {} : { big: v, small: 100 - v })),
  });
}
