/**
 * Types and calls of the market pages and the K-line chart (the PC api/minerva.ts
 * and api/position.ts contracts, paths relative to /api/v1).
 */
import { qs, request } from '../../api';

export type BarPeriod = 'day' | 'month' | 'week';
export type Adjust = 'hfq' | 'none' | 'qfq';

export interface Bar {
  amount: null | number;
  amplitude?: null | number; // %
  close: number;
  high: number;
  low: number;
  open: number;
  pct_change?: null | number; // %
  trade_date: string;
  turnover_rate?: null | number; // %
  volume: null | number;
}

export interface EtfMark { abnormal: 'in' | 'out'; flow: number; flow_pct: number; group: string; strong: boolean; trade_date: string; z: number }

export interface ChartMarks {
  dividends: { ex_date: string; plan_profile: null | string }[];
  etf?: EtfMark[];
  fills: { account_name: string; price: number; qty: number; side: 'buy' | 'sell'; trade_date: string }[];
  reports: { notice_date: string; report_date: string }[];
  risk: { end_date: null | string; start_date: string; start_title: null | string; status: string }[];
  suspensions: { end_inferred?: boolean; reason: null | string; suspend_end: null | string; suspend_start: string }[];
}

/** A shareholder count by count date; the top-ten stakes come with periodic reports only. */
export interface HolderCount {
  approximate: boolean;
  avg_free_shares: null | number;
  change: null | number; // from the previous count
  end_date: string;
  holders: null | number;
  notice_date: string;
  top10_free_pct: null | number; // % of the float shares
  top10_pct: null | number; // % of the total shares
}

export interface ChartPoint { date: string; price: number }
export interface ChartAnalysis {
  levels: { extreme: boolean; first_date: string; flipped: boolean; high: number; kind: 'resistance' | 'support'; low: number;
            price: number; touches: number }[];
  patterns: { direction: 'bearish' | 'bullish' | 'neutral'; end_date: string; lines: { end: ChartPoint; start: ChartPoint }[];
              name: string; points: ChartPoint[]; start_date: string; status: 'confirmed' | 'failed' | 'forming'; target: null | number }[];
}

// -- 仓位管家 chart payload ------------------------------------------------------------------------
export type PmLabel = 'base' | 'left' | 'right' | 'top' | 'undecided';
export type PmLevelKind = 'base_zone' | 'buyback_zone' | 'neckline' | 'reference' | 'target' | 'top_neckline';
export interface PmLevel {
  effective_date: string;
  fraction: null | number;
  id: number;
  kind: PmLevelKind;
  lower: null | number;
  price: number;
  round_no: number;
  top_mode: 'confirmed' | 'observe' | null;
}
export interface PmSentinel { days: null | number; direction: 'down' | 'up'; id: number; price: number; status: 'active' | 'crossed' | 'removed' }
export interface PmEvent { message: string; priority: number; priority_name: string; rule: string; trade_date: string }
export interface PmChart {
  close: null | number;
  completion: null | number;
  events: PmEvent[];
  item_id: null | number;
  kind: string;
  label: null | PmLabel;
  levels: PmLevel[];
  phase: null | string;
  sentinels: PmSentinel[];
  stage: null | { label: PmLabel; name: string; reason: string; stage: string };
  symbol: string;
}
export const LEVEL_NAMES: Record<PmLevelKind, string> = {
  base_zone: '起涨区', buyback_zone: '回撤买入区', neckline: '颈线', reference: '参考线', target: '量度目标', top_neckline: '头部颈线',
};
export const PM_LABEL: Record<PmLabel, { name: string; type: 'danger' | 'default' | 'primary' | 'success' | 'warning' }> = {
  base: { name: '筑底', type: 'warning' }, left: { name: '左侧', type: 'success' }, right: { name: '右侧', type: 'danger' },
  top: { name: '顶部', type: 'warning' }, undecided: { name: '未定', type: 'default' },
};
export const PM_PHASE: Record<string, string> = { active: '进行中', exhausted: '已衰竭', pending: '待突破', realized: '已兑现' };

// -- market ----------------------------------------------------------------------------------------
export interface Overview {
  breadth: { amount_cny: number; down: number; flat: number; limit_down: number; limit_up: number; traded: number; up: number };
  indices: { change_pct: null | number; close: number; name: string; symbol: string }[];
  industries: { change_pct: number; count: number; name: string }[];
  previous_session: null | string;
  session: string;
}
export interface SearchHit { board: string; name: string; symbol: string }

export const BOARD: Record<string, string> = { BSE: '北交所', CHINEXT: '创业板', INDEX: '指数', SSE_MAIN: '沪市主板', STAR: '科创板', SZSE_MAIN: '深市主板' };
export const RISK: Record<string, string> = { '*ST': '*ST', DELISTING: '退市整理', normal: '正常', ST: 'ST' };
export const FAMILY_LABEL: Record<string, string> = {
  growth: '成长', holders: '股东', liquidity: '流动性', momentum: '动量', quality: '质量', size: '规模', technical: '技术',
  value: '价值', volatility: '波动',
};
/** Family order of the factor library (features/registry.py FAMILIES). */
export const FAMILY_ORDER = ['value', 'quality', 'growth', 'momentum', 'volatility', 'liquidity', 'size', 'technical', 'holders'];

/** Index codes (sh000001, sz399006, H00300) as opposed to six-digit stock codes. */
export const isIndexSymbol = (symbol: string) => /^(sh|sz|bj)\d{6}$|^H\d{5}$/.test(symbol);

export const barsApi = (symbol: string, adjust: Adjust, limit: number, period: BarPeriod, end?: string) =>
  request<Bar[]>(`/instruments/${symbol}/bars${qs({ adjust, end, limit, period })}`);
export const indexBarsApi = (symbol: string, limit: number, period: BarPeriod = 'day', end?: string) =>
  request<Bar[]>(`/market/indices/${symbol}/bars${qs({ end, limit, period })}`);
export const marksApi = (symbol: string) => request<ChartMarks>(`/instruments/${symbol}/marks`);
export const etfMarksApi = (symbol: string) => request<EtfMark[]>(`/etf/marks/${symbol}`);
export const holdersApi = (symbol: string) => request<HolderCount[]>(`/instruments/${symbol}/holders`);
export const searchApi = (q: string) => request<SearchHit[]>(`/instruments/search${qs({ q })}`);
export const pmChartApi = (symbol: string) => request<PmChart>(`/pm/chart/${symbol}`);
export const chartAnalysisApi = (symbol: string, adjust: Adjust, period: BarPeriod, bars: number) =>
  request<ChartAnalysis>(`/charts/${symbol}/analysis${qs({ adjust, bars, period, sensitivity: 'medium' })}`);

/** 亿 with a sign, e.g. +12.35 亿 (amounts in CNY). */
export function yi(value?: null | number, signed = true): string {
  if (value === null || value === undefined) return '—';
  return `${signed && value > 0 ? '+' : ''}${(value / 1e8).toFixed(2)} 亿`;
}
