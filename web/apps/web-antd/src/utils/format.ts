import dayjs from 'dayjs';

/** A-share convention: red for gains, green for losses. */
export const UP_COLOR = '#e5484d';
export const FAMILY_LABEL: Record<string, string> = {
  growth: '成长', liquidity: '流动性', momentum: '动量', quality: '质量', size: '规模', technical: '技术', value: '价值',
  volatility: '波动',
};
/** Family order of the factor library (features/registry.py FAMILIES). */
export const FAMILY_ORDER = ['value', 'quality', 'growth', 'momentum', 'volatility', 'liquidity', 'size', 'technical'];
export const DOWN_COLOR = '#16a34a';

export function yuan(fen?: null | number, digits = 2): string {
  if (fen === null || fen === undefined) return '—';
  return (fen / 100).toLocaleString('zh-CN', { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

/** Large amounts in 万元 / 亿元. */
export function bigYuan(value?: null | number, fromFen = true): string {
  if (value === null || value === undefined) return '—';
  const cny = fromFen ? value / 100 : value;
  if (Math.abs(cny) >= 1e8) return `${(cny / 1e8).toFixed(2)} 亿`;
  if (Math.abs(cny) >= 1e4) return `${(cny / 1e4).toFixed(2)} 万`;
  return cny.toFixed(2);
}

export function price(fen?: null | number): string {
  return fen === null || fen === undefined ? '—' : (fen / 100).toFixed(2);
}

export function pct(value?: null | number, digits = 2, signed = false): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  const text = `${(value * 100).toFixed(digits)}%`;
  return signed && value > 0 ? `+${text}` : text;
}

export function changeColor(value?: null | number): string | undefined {
  if (!value) return undefined;
  return value > 0 ? UP_COLOR : DOWN_COLOR;
}

export function dateTime(iso?: null | string): string {
  return iso ? dayjs(iso).format('YYYY-MM-DD HH:mm') : '—';
}

export const RUN_STATUS: Record<string, { color: string; label: string }> = {
  blocked: { color: 'error', label: '阻断' },
  complete: { color: 'success', label: '完成' },
  failed: { color: 'error', label: '失败' },
  superseded: { color: 'default', label: '已作废' },
};

export const RUN_KIND: Record<string, string> = { forced: '强制调仓', monitor: '监控日', rebalance: '调仓日' };

export const INTENT_STATUS: Record<string, { color: string; label: string }> = {
  approved: { color: 'success', label: '已批准' },
  expired: { color: 'default', label: '已过期' },
  modified: { color: 'processing', label: '已修改' },
  pending_approval: { color: 'warning', label: '待审核' },
  rejected: { color: 'default', label: '已拒绝' },
  rejected_by_risk: { color: 'error', label: '风控拒绝' },
  superseded: { color: 'default', label: '已作废' },
};

export const EXECUTION: Record<string, { color: string; label: string }> = {
  filled: { color: 'success', label: '已成交' },
  partial: { color: 'processing', label: '部分成交' },
  partial_closed: { color: 'warning', label: '部分成交（结束）' },
  unfilled: { color: 'default', label: '未成交' },
};

export const RISK: Record<string, { color: string; label: string }> = {
  pass: { color: 'success', label: '通过' },
  reject: { color: 'error', label: '拒绝' },
  warn: { color: 'warning', label: '警告' },
};

export const LEVEL: Record<string, { color: string; label: string }> = {
  critical: { color: 'error', label: '严重' },
  info: { color: 'blue', label: '提示' },
  warning: { color: 'warning', label: '警告' },
};

export const EVENT_KIND: Record<string, string> = {
  adjustment: '持仓调整', cash: '现金调整', corporate_action: '除权', delisting: '退市结算',
  deposit: '入金', fill: '成交', reversal: '冲正',
};

export const ROLE_LABEL: Record<string, string> = { admin: '管理员', reviewer: '审核员', viewer: '只读' };
