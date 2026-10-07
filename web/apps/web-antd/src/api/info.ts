import { requestClient } from '#/api/request';

/** 回购增持: buyback plans and holder changes (information, not signals). */
export interface BuybackRow {
  amount_lower: null | number;
  amount_upper: null | number;
  close: null | number;
  close_vs_avg: null | number;
  done_amount: null | number;
  done_avg_price: null | number;
  done_pct: null | number;
  end_date: null | string;
  finish_date: null | string;
  industry: null | string;
  kind: 'cancel' | 'incentive' | 'other';
  kind_name: string;
  latest_notice_date: string;
  name: null | string;
  notice_date: string;
  plan_id: string;
  plan_pct_lower: null | number;
  plan_pct_upper: null | number;
  price_cap: null | number;
  progress: string;
  progress_name: string;
  purpose: null | string;
  start_date: null | string;
  symbol: string;
}
export interface HolderChangeRow {
  amount: null | number;
  avg_price: null | number;
  change_key: string;
  change_pct_total: null | number;
  change_shares: null | number;
  channel: null | string;
  direction: '减持' | '增持';
  end_date: null | string;
  hold_pct_after: null | number;
  holder: null | string;
  industry: null | string;
  name: null | string;
  notice_date: string;
  start_date: null | string;
  symbol: string;
}
export interface CompanyIndustryRow {
  buyback_amount: number;
  buyback_companies: number;
  cancel_companies: number;
  decrease_amount: number;
  decrease_companies: number;
  increase_amount: number;
  increase_companies: number;
  industry: string;
}
export const buybacksApi = (params: { days?: number; industry?: string; kind?: string; min_pct?: number; progress?: string }) =>
  requestClient.get<{ as_of: string; days: number; kinds: Record<string, string>; progress: Record<string, string>; rows: BuybackRow[]; total: number }>(
    '/company-actions/buybacks', { params });
export const holderChangesApi = (params: { days?: number; direction?: string; industry?: string; min_pct?: number }) =>
  requestClient.get<{ as_of: string; days: number; rows: HolderChangeRow[]; total: number }>('/company-actions/holders', { params });
export const companyIndustriesApi = (days = 90) =>
  requestClient.get<{ as_of: string; days: number; rows: CompanyIndustryRow[] }>('/company-actions/industries', { params: { days } });

/** 市场宽度: shares of stocks above their moving averages (information, not signals). */
export interface BreadthDay {
  above20: number;
  above60: number;
  above120: number;
  above250: number;
  downs: number;
  highs: number;
  lows: number;
  stocks: number;
  trade_date: string;
  ups: number;
}
export interface BreadthIndustry {
  [key: string]: null | number | string;
  industry: string;
}
export interface Breadth {
  as_of: string;
  index: { close: number; trade_date: string }[];
  industries: BreadthIndustry[];
  industry_days: string[];
  latest: Record<string, null | number | string>;
  rules: { industry_lookback: number; min_history: number; windows: number[] };
  series: BreadthDay[];
}
export const breadthApi = () => requestClient.get<Breadth>('/breadth');
