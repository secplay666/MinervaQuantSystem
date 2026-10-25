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
