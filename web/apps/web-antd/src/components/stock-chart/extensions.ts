/**
 * KLineChart extensions for the stock chart: event badges on bars, a
 * range-statistics band and an index comparison line.  Registered once.
 */
import type { KLineData, OverlayFigure } from 'klinecharts';

import { registerIndicator, registerOverlay, utils } from 'klinecharts';

export interface MarkData {
  below: boolean; // under the low (buys) or above the high (everything else)
  color: string;
  outline?: boolean; // hollow badge (candlestick patterns), so it reads apart from events
  stack: number; // badges on the same bar and side are stacked
  text: string;
}

/**
 * A label of the automatic lines, placed by the one ``autoLabels`` overlay so
 * labels of different kinds (patterns, trend lines, targets, price bands) do
 * not cover each other.  Points are indices into that overlay's points.
 */
export interface AutoLabel {
  anchor: number;
  color: string;
  dy?: number; // preferred offset from the anchor, negative = above
  edge?: boolean; // with ``to``: pinned to the pane's top or bottom when the line is off the price scale
  extend?: boolean; // with ``to``: the line runs on to the right edge
  right?: boolean; // pinned to the chart's right edge (price bands; ``to`` is the band's other side)
  text: string;
  to?: number; // the label rides the line anchor -> to, at its right-most visible spot
}

/** Automatic lines (read-only overlays): a line through points, a line that may run to the right edge, a price band. */
export interface AutoLineData {
  color: string;
  dashed?: boolean;
  dots?: boolean;
  extend?: boolean;
  label?: string;
  width?: number;
}

export interface RangeStats {
  amount: number;
  amplitude: number;
  bars: number;
  change: number;
  end: KLineData;
  high: number;
  low: number;
  start: KLineData;
  turnoverRate: null | number;
  volume: number;
}

/** Statistics of the bars between two timestamps (both included), like a trading app's 区间统计. */
export function rangeStats(data: KLineData[], t1: number, t2: number): null | RangeStats {
  const [from, to] = t1 <= t2 ? [t1, t2] : [t2, t1];
  const i0 = data.findIndex((d) => d.timestamp >= from);
  let i1 = -1;
  for (let i = data.length - 1; i >= 0; i--) {
    if (data[i]!.timestamp <= to) {
      i1 = i;
      break;
    }
  }
  if (i0 < 0 || i1 < i0) return null;
  const bars = data.slice(i0, i1 + 1);
  const base = i0 > 0 ? data[i0 - 1]!.close : bars[0]!.open; // previous close, as trading apps do
  const high = Math.max(...bars.map((d) => d.high));
  const low = Math.min(...bars.map((d) => d.low));
  const rates = bars.map((d) => d.turnover_rate as null | number | undefined);
  return {
    amount: bars.reduce((n, d) => n + (d.turnover ?? 0), 0),
    amplitude: (high - low) / base,
    bars: bars.length,
    change: bars.at(-1)!.close / base - 1,
    end: bars.at(-1)!,
    high,
    low,
    start: bars[0]!,
    turnoverRate: rates.every((r) => r === null || r === undefined) ? null : rates.reduce<number>((n, r) => n + (r ?? 0), 0),
    volume: bars.reduce((n, d) => n + (d.volume ?? 0), 0),
  };
}

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
        attrs: { x: c.x, y: mark.below ? c.y + shift : c.y - shift, text: mark.text, align: 'center',
                 baseline: mark.below ? 'top' : 'bottom' },
        styles: mark.outline
          ? { style: 'stroke_fill', color: mark.color, size: 11, weight: 'bold', family: 'Helvetica Neue, sans-serif',
              backgroundColor: 'rgba(0, 0, 0, 0)', borderColor: mark.color, borderSize: 1, borderRadius: 2,
              paddingLeft: 2, paddingRight: 2, paddingTop: 1, paddingBottom: 1 }
          : { style: 'fill', color: '#ffffff', size: 11, weight: 'bold', family: 'Helvetica Neue, sans-serif',
              backgroundColor: mark.color, borderRadius: 2, paddingLeft: 3, paddingRight: 3, paddingTop: 1,
              paddingBottom: 1, borderSize: 0 },
        ignoreEvent: true,
      }];
    },
  });

  // Rectangle and circle drawings: KLineChart has these as figures only, not as drawing tools.
  const shapeStyles = { borderColor: '#1677ff', borderSize: 1, borderStyle: 'solid', color: 'rgba(22, 119, 255, 0.10)',
                        style: 'stroke_fill' };
  registerOverlay({
    name: 'rectBox',
    totalStep: 3,
    needDefaultPointFigure: true,
    needDefaultXAxisFigure: true,
    needDefaultYAxisFigure: true,
    createPointFigures: ({ coordinates }) => {
      const [a, b] = coordinates;
      if (!a || !b) return [];
      return [{ type: 'rect', attrs: { x: Math.min(a.x, b.x), y: Math.min(a.y, b.y), width: Math.abs(b.x - a.x),
                                       height: Math.abs(b.y - a.y) }, styles: shapeStyles }];
    },
  });
  registerOverlay({
    name: 'circleShape',
    totalStep: 3,
    needDefaultPointFigure: true,
    needDefaultXAxisFigure: true,
    needDefaultYAxisFigure: true,
    createPointFigures: ({ coordinates }) => {
      const [a, b] = coordinates;
      if (!a || !b) return [];
      return [{ type: 'circle', attrs: { x: a.x, y: a.y, r: Math.hypot(b.x - a.x, b.y - a.y) }, styles: shapeStyles }];
    },
  });

  const label = (x: number, y: number, text: string, color: string, align: CanvasTextAlign = 'left'): OverlayFigure => ({
    type: 'text',
    attrs: { x, y, text, align, baseline: 'bottom' },
    styles: { style: 'fill', color: '#ffffff', size: 11, backgroundColor: color, borderRadius: 2, paddingLeft: 4,
              paddingRight: 4, paddingTop: 1, paddingBottom: 1, borderSize: 0 },
    ignoreEvent: true,
  });
  const lineStyle = (d: AutoLineData) => ({ color: d.color, size: d.width ?? 1, style: d.dashed ? 'dashed' : 'solid',
                                            dashedValue: [4, 3] });

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
        for (const c of coordinates) figures.push({ type: 'circle', attrs: { x: c.x, y: c.y, r: 2.5 },
                                                     styles: { style: 'fill', color: d.color }, ignoreEvent: true });
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
      if (!d || !a || !b) return [];
      let end = b;
      if (d.extend && b.x !== a.x && bounding.width > b.x) {
        end = { x: bounding.width, y: a.y + ((b.y - a.y) / (b.x - a.x)) * (bounding.width - a.x) };
      }
      const figures: OverlayFigure[] = [{ type: 'line', attrs: { coordinates: [a, end] }, styles: lineStyle(d), ignoreEvent: true }];
      if (d.label) figures.push(label(Math.min(b.x, bounding.width - 4), b.y - 3, d.label, d.color, b.x > bounding.width - 120 ? 'right' : 'left'));
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
      if (!d || !a || !b) return [];
      const top = Math.min(a.y, b.y);
      const height = Math.max(2, Math.abs(b.y - a.y));
      const x = Math.max(0, Math.min(a.x, bounding.width));
      return [
        { type: 'rect', attrs: { x, y: top, width: bounding.width - x, height },
          styles: { style: 'fill', color: `${d.color}22` }, ignoreEvent: true },
        { type: 'line', attrs: { coordinates: [{ x, y: top + height / 2 }, { x: bounding.width, y: top + height / 2 }] },
          styles: { color: d.color, size: 1, style: 'dashed', dashedValue: [2, 3] }, ignoreEvent: true },
        ...(d.label ? [label(bounding.width - 4, top - 1, d.label, d.color, 'right')] : []),
      ];
    },
  });

  // All automatic labels in one overlay: each goes where it prefers unless that
  // spot is taken, then moves up or down one label height at a time and keeps a
  // thin leader line to its anchor.  Earlier labels win (the caller orders them).
  // Lines are clipped to the price pane first, so a label sits on the part of
  // its line that is on screen; nothing is drawn outside the pane.
  const LABEL_HEIGHT = 16;
  type P = { x: number; y: number };
  /** Liang-Barsky: the part of segment a-b inside [0, w] x [0, h], or null. */
  const clip = (a: P, b: P, w: number, h: number): [P, P] | null => {
    let t0 = 0;
    let t1 = 1;
    const dx = b.x - a.x;
    const dy = b.y - a.y;
    for (const [pv, qv] of [[-dx, a.x], [dx, w - a.x], [-dy, a.y], [dy, h - a.y]] as const) {
      if (pv === 0) {
        if (qv < 0) return null;
        continue;
      }
      const r = qv / pv;
      if (pv < 0) {
        if (r > t1) return null;
        t0 = Math.max(t0, r);
      } else {
        if (r < t0) return null;
        t1 = Math.min(t1, r);
      }
    }
    return [{ x: a.x + t0 * dx, y: a.y + t0 * dy }, { x: a.x + t1 * dx, y: a.y + t1 * dy }];
  };
  registerOverlay<{ labels: AutoLabel[]; reserveTop?: number; reserveWidth?: number }>({
    name: 'autoLabels',
    totalStep: 2,
    lock: true,
    needDefaultPointFigure: false,
    needDefaultXAxisFigure: false,
    needDefaultYAxisFigure: false,
    createPointFigures: ({ bounding, coordinates, overlay }) => {
      const W = bounding.width;
      const H = bounding.height;
      const figures: OverlayFigure[] = [];
      const placed: { x1: number; x2: number; y1: number; y2: number }[] = [];
      const reserveTop = overlay.extendData?.reserveTop ?? 0; // the chart's own legend at the top left
      if (reserveTop) placed.push({ x1: 0, x2: Math.min(W, overlay.extendData?.reserveWidth ?? 600), y1: 0, y2: reserveTop });
      const overlaps = (box: (typeof placed)[number]) => placed.some((b) => box.x1 < b.x2 + 2 && box.x2 > b.x1 - 2
                                                                         && box.y1 < b.y2 + 1 && box.y2 > b.y1 - 1);
      for (const l of overlay.extendData?.labels ?? []) {
        const a = coordinates[l.anchor];
        if (!a) continue;
        const b = l.to === undefined ? undefined : coordinates[l.to];
        let x = a.x;
        let y = a.y;
        let text = l.text;
        let align: 'center' | 'left' | 'right' = 'center';
        if (l.right) { // a price band: labelled at the right edge while some of it is on screen
          const bottom = b ? b.y : a.y;
          if (Math.max(a.y, bottom) < 0 || Math.min(a.y, bottom) > H) continue;
          x = W - 4;
          y = Math.min(Math.max(a.y, LABEL_HEIGHT + 2), H);
          align = 'right';
        } else if (b) { // a line: at the right end of its visible part
          const far = l.extend && b.x !== a.x && W > b.x ? { x: W, y: a.y + ((b.y - a.y) / (b.x - a.x)) * (W - a.x) } : b;
          const visible = clip(a, far, W, H);
          if (visible) {
            const right = visible[0].x > visible[1].x ? visible[0] : visible[1];
            x = Math.min(right.x, W - 4);
            y = right.y;
          } else if (l.edge && Math.min(a.x, far.x) < W && Math.max(a.x, far.x) > 0) {
            // off the price scale (a far target): pinned to the nearer edge, with the direction
            const above = a.y < 0;
            x = Math.min(Math.max(a.x, far.x), W) - 4;
            y = above ? LABEL_HEIGHT + 2 : H - 2;
            text = `${l.text} ${above ? '↑' : '↓'}`;
          } else {
            continue; // the line is not on screen
          }
          align = 'right';
        } else if (a.x < 0 || a.x > W) {
          continue;
        } else {
          y = Math.min(Math.max(a.y, LABEL_HEIGHT + 2 - (l.dy ?? -3)), H);
        }
        const width = utils.calcTextWidth(text, 11) + 8;
        let x1 = align === 'right' ? x - width : align === 'center' ? x - width / 2 : x;
        x1 = Math.max(0, Math.min(x1, W - width));
        const base = Math.min(Math.max(y + (l.dy ?? -3), LABEL_HEIGHT), H);
        let box = { x1, x2: x1 + width, y1: base - LABEL_HEIGHT, y2: base };
        for (const step of [0, -1, 1, -2, 2, -3, 3, -4, 4]) {
          const shift = step * (LABEL_HEIGHT + 2);
          const candidate = { x1, x2: x1 + width, y1: base - LABEL_HEIGHT + shift, y2: base + shift };
          if (candidate.y1 < 0 || candidate.y2 > H) continue;
          box = candidate;
          if (!overlaps(candidate)) break;
        }
        placed.push(box);
        const anchorY = Math.min(Math.max(y, 0), H);
        if (Math.abs(box.y2 - base) > 1) { // moved: a leader line back to where it belongs
          const edge = box.y2 < anchorY ? box.y2 : box.y1;
          figures.push({ type: 'line', attrs: { coordinates: [{ x: Math.min(Math.max(x, box.x1), box.x2), y: edge }, { x, y: anchorY }] },
                         styles: { color: l.color, size: 1, style: 'dashed', dashedValue: [2, 2] }, ignoreEvent: true });
        }
        figures.push(label(box.x1, box.y2, text, l.color, 'left'));
      }
      return figures;
    },
  });

  registerOverlay({
    name: 'rangeStat',
    totalStep: 3,
    needDefaultPointFigure: true,
    needDefaultXAxisFigure: true,
    needDefaultYAxisFigure: false,
    createPointFigures: ({ chart, coordinates, bounding, overlay }) => {
      const figures: OverlayFigure[] = [];
      const [a, b] = coordinates;
      if (!a) return figures;
      const line = (x: number) => ({ type: 'line', attrs: { coordinates: [{ x, y: 0 }, { x, y: bounding.height }] },
                                     styles: { color: '#3b82f6', size: 1, style: 'dashed' }, ignoreEvent: true });
      figures.push(line(a.x));
      if (!b) return figures;
      figures.push(line(b.x), {
        type: 'rect',
        attrs: { x: Math.min(a.x, b.x), y: 0, width: Math.abs(b.x - a.x), height: bounding.height },
        styles: { style: 'fill', color: 'rgba(59, 130, 246, 0.10)' },
      });
      const [p1, p2] = overlay.points;
      const stats = p1?.timestamp && p2?.timestamp ? rangeStats(chart.getDataList(), p1.timestamp, p2.timestamp) : null;
      if (stats) {
        const sign = stats.change > 0 ? '+' : '';
        figures.push({
          type: 'text',
          attrs: { x: (a.x + b.x) / 2, y: 6, align: 'center', baseline: 'top',
                   text: `${stats.bars} 根  ${sign}${(stats.change * 100).toFixed(2)}%  振幅 ${(stats.amplitude * 100).toFixed(2)}%` },
          styles: { style: 'fill', color: '#ffffff', size: 12, backgroundColor: stats.change >= 0 ? '#e5484d' : '#16a34a',
                    borderRadius: 3, paddingLeft: 6, paddingRight: 6, paddingTop: 3, paddingBottom: 3, borderSize: 0 },
          ignoreEvent: true,
        });
      }
      return figures;
    },
  });

  // Index comparison on the price pane: the index rebased to the stock's close
  // at ``base`` (the first visible bar; the chart updates it when scrolled), so
  // both lines start together on screen, as trading apps overlay an index.
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
}
