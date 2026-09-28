import dayjs from 'dayjs';

export const UP = '#e5484d';
export const DOWN = '#16a34a';

export function yuan(fen?: null | number, digits = 2): string {
  if (fen === null || fen === undefined) return '—';
  return (fen / 100).toLocaleString('zh-CN', { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

export function price(fen?: null | number): string {
  return fen === null || fen === undefined ? '—' : (fen / 100).toFixed(2);
}

export function pct(v?: null | number, digits = 2, signed = false): string {
  if (v === null || v === undefined) return '—';
  return `${signed && v > 0 ? '+' : ''}${(v * 100).toFixed(digits)}%`;
}

export function color(v?: null | number): string | undefined {
  if (!v) return undefined;
  return v > 0 ? UP : DOWN;
}

export function big(v?: null | number): string {
  if (v === null || v === undefined) return '—';
  if (Math.abs(v) >= 1e8) return `${(v / 1e8).toFixed(2)}亿`;
  if (Math.abs(v) >= 1e4) return `${(v / 1e4).toFixed(2)}万`;
  return v.toFixed(2);
}

export function time(iso?: null | string): string {
  return iso ? dayjs(iso).format('MM-DD HH:mm') : '—';
}

export const INTENT: Record<string, [string, string]> = {
  approved: ['已批准', 'success'],
  expired: ['已过期', 'default'],
  modified: ['已修改', 'primary'],
  pending_approval: ['待审核', 'warning'],
  rejected: ['已拒绝', 'default'],
  rejected_by_risk: ['风控拒绝', 'danger'],
  superseded: ['已作废', 'default'],
};

export const RUN: Record<string, [string, string]> = {
  blocked: ['阻断', 'danger'],
  complete: ['完成', 'success'],
  failed: ['失败', 'danger'],
  superseded: ['已作废', 'default'],
};

export const KIND: Record<string, string> = { forced: '强制调仓', monitor: '监控日', rebalance: '调仓日' };

export const LEVEL: Record<string, [string, string]> = {
  critical: ['严重', 'danger'],
  info: ['提示', 'primary'],
  warning: ['警告', 'warning'],
};
