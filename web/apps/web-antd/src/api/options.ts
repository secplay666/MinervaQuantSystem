import { requestClient } from '#/api/request';

/** 期权: SSE ETF options and CFFEX index options (app/options.py). */
export interface OptionUnderlying {
  contracts: number;
  exchange: 'CFFEX' | 'SSE';
  first_date: string;
  last_date: string;
  live: number;
  minutes: boolean;
  name: string;
  underlying: string;
  unit: string;
}
export interface OptionSide {
  change: null | number;
  change_pct: null | number;
  close: null | number;
  contract: string;
  delta: null | number;
  intrinsic: null | number;
  iv: null | number;
  name: string;
  open_interest: null | number;
  prev_close: null | number;
  settle: null | number;
  time_value: null | number;
  volume: null | number;
}
export interface OptionChainRow {
  adjusted: boolean;
  atm: boolean;
  call: null | OptionSide;
  put: null | OptionSide;
  strike: number;
}
export interface OptionMonth {
  contracts: number;
  expired: boolean;
  expiry_date: string;
  expiry_month: string;
}
export interface OptionChain {
  day: string;
  days_left: number;
  expired: boolean;
  expiry_date: string;
  month: string;
  months: OptionMonth[];
  rows: OptionChainRow[];
  sessions: string[];
  underlying: { change_pct: null | number; close: null | number; code: string; exchange: string; name: string };
  unit: string;
}
export interface OptionBar {
  amount: null | number;
  close: null | number;
  delta?: null | number;
  gamma?: null | number;
  high: null | number;
  intrinsic: null | number;
  iv?: null | number;
  low: null | number;
  open: null | number;
  open_interest: null | number;
  settle: null | number;
  theta?: null | number;
  time_value: null | number;
  trade_date: string;
  underlying_close: null | number;
  vega?: null | number;
  volume: null | number;
}
export interface OptionTerms {
  adjusted: boolean;
  code: string;
  contract: string;
  exchange: 'CFFEX' | 'SSE';
  expiry_date: string;
  expiry_month: string;
  kind: 'call' | 'put';
  multiplier: null | number;
  name: string;
  strike: number;
  underlying: string;
  underlying_name: string;
}
export interface OptionContract {
  bars: OptionBar[];
  minute_days: string[];
  terms: OptionTerms;
  unit: string;
}
export interface OptionMinute {
  avg_price: null | number;
  open_interest: null | number;
  price: null | number;
  time: string;
  volume: null | number;
}

export const optionUnderlyingsApi = () => requestClient.get<{ underlyings: OptionUnderlying[] }>('/options/underlyings');
export const optionChainApi = (params: { day?: string; month?: string; underlying: string }) =>
  requestClient.get<OptionChain>('/options/chain', { params });
export const optionContractApi = (contract: string) =>
  requestClient.get<OptionContract>(`/options/contracts/${encodeURIComponent(contract)}`);
export const optionMinutesApi = (contract: string, day: string) =>
  requestClient.get<{ contract: string; day: string; prev_close: null | number; rows: OptionMinute[] }>(
    `/options/contracts/${encodeURIComponent(contract)}/minutes`, { params: { day } });
