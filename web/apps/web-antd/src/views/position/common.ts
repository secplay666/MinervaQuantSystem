/** Shared display helpers of the 仓位管家 pages. */
import type { Router } from 'vue-router';

import type { PmLabel } from '#/api';

import { PM_LABEL } from '#/api';

export function px(value?: null | number, digits = 2): string {
  return value === null || value === undefined ? '—' : value.toFixed(digits);
}

export function labelColor(label?: null | PmLabel): string {
  return label ? PM_LABEL[label]?.color ?? 'default' : 'default';
}

export function labelName(label?: null | PmLabel): string {
  return label ? PM_LABEL[label]?.name ?? label : '—';
}

/** Completion as a 0..100 progress value (beyond the target shows full). */
export function progress(completion?: null | number): number {
  if (completion === null || completion === undefined) return 0;
  return Math.max(0, Math.min(100, Math.round(completion * 100)));
}

/** The chart of a code in a real browser tab (the chart page's own convention). */
export function openChart(router: Router, symbol: string): void {
  window.open(router.resolve({ path: '/chart', query: { symbol } }).href, '_blank', 'noopener');
}
