/** Types and helpers of the 行情资讯 pages (money map, breadth, company actions, big money, options). */
import { computed, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';

/* Categorical slots 1-4 of the validated palette used on the PC pages (light mode); text in ink, not series colours. */
export const SERIES = ['#2a78d6', '#eb6834', '#1baf7a', '#eda100'];
export const INK = '#52514e';
export const MUTED = '#8c8b85';
export const GRID = '#e1e0d9';

export const missing = (v?: null | number): v is null | undefined => v === null || v === undefined || Number.isNaN(v);
/** Yuan as 亿: 123456789 -> "1.23 亿" (``signed`` adds +). */
export const yi = (v?: null | number, digits = 2, signed = false) =>
  missing(v) ? '—' : `${signed && v > 0 ? '+' : ''}${(v / 1e8).toFixed(digits)} 亿`;
/** A percentage already in percent units: 1.234 -> "+1.23%". */
export const signedPercent = (v?: null | number, digits = 2) => (missing(v) ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(digits)}%`);

/**
 * A popup bound to a query parameter: opening pushes a history entry, so the phone's back button closes it
 * instead of leaving the page; a deep link with the parameter opens it directly.
 */
export function useQueryPopup(key: string) {
  const route = useRoute();
  const router = useRouter();
  let pushed = false;
  const value = computed(() => (route.query[key] as string | undefined) || undefined);
  const query = (v?: string) => {
    const next = { ...route.query };
    if (v) next[key] = v;
    else delete next[key];
    return { query: next };
  };
  function open(v: string) {
    if (value.value) {
      void router.replace(query(v));
    } else {
      pushed = true;
      void router.push(query(v));
    }
  }
  function close() {
    if (!value.value) return;
    if (pushed) router.back();
    else void router.replace(query());
  }
  watch(value, (v) => {
    if (!v) pushed = false;
  });
  return { close, open, value };
}

/* ---- 钱去哪地图 ---- */
export interface MoneyMapStats {
  [horizon: string]: { h?: null | number; h_lose?: null | number; n: number; x?: null | number; x_lose?: null | number };
}
export interface MoneyMapRow {
  c: number;
  cell: null | string;
  code: string;
  dc60: null | number;
  r3: null | number;
  r12: null | number;
  share: null | number;
  short: boolean;
  zone: null | string;
}
export interface MoneyMapFrame {
  cells: Record<string, MoneyMapStats>;
  entry: MoneyMapStats;
  rows: MoneyMapRow[];
  week: string;
  zones: Record<string, MoneyMapStats>;
}
export interface MoneyMap {
  as_of: string;
  frames: MoneyMapFrame[];
  industries: { code: string; first: string; name: string }[];
  rules: {
    cells: string[];
    crowded: number;
    fight: [number, number, number];
    long: number;
    quiet_return: number;
    short: number;
    starting: [number, number, number, number];
    zones: Record<string, string>;
  };
  start: string;
  weeks_total: number;
}
export interface MoneyMapIndustry {
  c: (null | number)[];
  code: string;
  dates: string[];
  entries: string[];
  name: string;
  relative: (null | number)[];
  share: (null | number)[];
}

/* ---- 市场宽度 ---- */
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
export interface Breadth {
  as_of: string;
  index: { close: number; trade_date: string }[];
  industries: Record<string, null | number | string>[];
  industry_days: string[];
  latest: Record<string, null | number | string>;
  rules: { industry_lookback: number; min_history: number; windows: number[] };
  series: BreadthDay[];
}

/* ---- 回购增持 ---- */
export interface BuybackRow {
  amount_lower: null | number;
  amount_upper: null | number;
  close: null | number;
  close_vs_avg: null | number;
  done_amount: null | number;
  done_avg_price: null | number;
  done_pct: null | number;
  end_date: null | string;
  industry: null | string;
  kind: 'cancel' | 'incentive' | 'other';
  kind_name: string;
  latest_notice_date: string;
  name: null | string;
  notice_date: string;
  plan_id: string;
  plan_pct_lower: null | number;
  plan_pct_upper: null | number;
  progress: string;
  progress_name: string;
  purpose: null | string;
  start_date: null | string;
  symbol: string;
}
export interface HolderChangeRow {
  amount: null | number;
  change_key: string;
  change_pct_total: null | number;
  change_shares: null | number;
  channel: null | string;
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

/* ---- 盘中大资金 ---- */
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
  day: string;
  days: string[];
  errors: number;
  final: boolean;
  groups: BigMoneyGroup[];
  live: boolean;
  updated: string;
}

/* ---- 期权 ---- */
export interface OptionUnderlying {
  exchange: 'CFFEX' | 'SSE';
  name: string;
  underlying: string;
}
export interface OptionSide {
  change: null | number;
  change_pct: null | number;
  close: null | number;
  contract: string;
  delta: null | number;
  iv: null | number;
  open_interest: null | number;
  volume: null | number;
}
export interface OptionChainRow {
  adjusted: boolean;
  atm: boolean;
  call: null | OptionSide;
  put: null | OptionSide;
  strike: number;
}
export interface OptionChain {
  day: string;
  days_left: number;
  expired: boolean;
  expiry_date: string;
  month: string;
  months: { contracts: number; expired: boolean; expiry_date: string; expiry_month: string }[];
  rows: OptionChainRow[];
  sessions: string[];
  underlying: { change_pct: null | number; close: null | number; name: string };
  unit: string;
}
export interface OptionBar {
  close: null | number;
  delta?: null | number;
  high: null | number;
  intrinsic: null | number;
  iv?: null | number;
  low: null | number;
  open: null | number;
  open_interest: null | number;
  settle: null | number;
  time_value: null | number;
  trade_date: string;
  underlying_close: null | number;
  volume: null | number;
}
export interface OptionContract {
  bars: OptionBar[];
  minute_days: string[];
  terms: {
    adjusted: boolean;
    code: string;
    contract: string;
    exchange: 'CFFEX' | 'SSE';
    expiry_date: string;
    kind: 'call' | 'put';
    multiplier: null | number;
    name: string;
    strike: number;
    underlying_name: string;
  };
}
export interface OptionMinute {
  avg_price: null | number;
  open_interest: null | number;
  price: null | number;
  time: string;
  volume: null | number;
}
