import { requestClient } from '#/api/request';

/** 盘中大资金监测 (app/monitor.py): broad-index ETF groups, minute by minute. */
export interface BigMoneyPoint {
  active_net: null | number;
  amount: null | number;
  baseline: null | number;
  big_net: null | number;
  big_net_30m: null | number;
  big_share: null | number;
  index_change: null | number;
  index_price: null | number;
  premium: null | number;
  ratio: null | number;
  time: string;
}
export interface BigMoneyFund {
  amount?: null | number;
  big_net?: null | number;
  big_share?: null | number;
  name: string;
  premium?: null | number;
  price?: null | number;
  ratio?: null | number;
  symbol: string;
}
export interface BigMoneyGroup {
  alerts: { direction: 'buy' | 'sell'; time: string }[];
  funds: BigMoneyFund[];
  id: string;
  index: string;
  latest: BigMoneyPoint | null;
  name: string;
  series: BigMoneyPoint[];
  usual_turnover: number;
}
export interface BigMoneyDay {
  big_order_yuan: number;
  day: string;
  days: string[];
  errors: number;
  final: boolean;
  groups: BigMoneyGroup[];
  live: boolean;
  updated: string;
}
export const bigMoneyApi = (day?: string) =>
  requestClient.get<BigMoneyDay>('/monitor/big-money', { params: day ? { day } : {} });

/** 恐慌指数 (app/monitor.py, monitor/volatility.py): 30-day volatility indices of the A-share options, VIX method. */
export interface VolatilityPoint {
  near_vol: null | number;
  next_vol: null | number;
  time: string;
  value: null | number;
}
export interface VolatilityIndex {
  alert: null | string;
  change: null | number;
  exchange: null | string;
  history: { trade_date: string; value: number }[];
  history_from: null | string;
  id: string;
  latest: (VolatilityPoint & { forward: null | number; near_expiry: null | string; next_expiry: null | string; strikes: number }) | null;
  median: null | number;
  name: string;
  percentile: null | number;
  previous_close: null | number;
  series: VolatilityPoint[];
  underlying: null | string;
  value: null | number;
}
export interface VolatilityDay {
  day: null | string;
  days: string[];
  errors: number;
  final: boolean;
  history_years: number;
  indices: VolatilityIndex[];
  live: boolean;
  updated: null | string;
}
export const volatilityApi = (day?: string) =>
  requestClient.get<VolatilityDay>('/monitor/volatility', { params: day ? { day } : {} });
