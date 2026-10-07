import { requestClient } from '#/api/request';

/** Statistics known at a week: per horizon (weeks) n, mean excess and share of losses
 * against the average industry (x) and the CSI 300 (h). */
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
  short: boolean; // baseline shorter than the full window: not in the statistics
  zone: null | string;
}

export interface MoneyMapFrame {
  cells: Record<string, MoneyMapStats>;
  entry: MoneyMapStats;
  rows: MoneyMapRow[];
  week: string;
  zones: Record<string, MoneyMapStats>;
}

export interface MoneyMapRules {
  base_min: number;
  c_bands: string[];
  cells: string[];
  crowded: number;
  fight: [number, number, number];
  horizons: number[];
  long: number;
  long_min: number;
  quiet_return: number;
  r_bands: string[];
  short: number;
  starting: [number, number, number, number];
  zones: Record<string, string>;
}

export interface MoneyMap {
  as_of: string;
  entries: { c: number; code: string; h13: null | number; h26: null | number; r12: null | number; week: string;
    x13: null | number; x26: null | number }[];
  frames: MoneyMapFrame[];
  industries: { code: string; first: string; name: string }[];
  rules: MoneyMapRules;
  start: string;
  weeks_total: number;
}

export interface MoneyMapIndustry {
  as_of: string;
  c: (null | number)[];
  close: (null | number)[];
  code: string;
  dates: string[];
  entries: string[];
  name: string;
  relative: (null | number)[];
  share: (null | number)[];
}

export const moneyMapApi = (weeks = 156) => requestClient.get<MoneyMap>('/moneymap', { params: { weeks } });
export const moneyMapIndustryApi = (code: string) => requestClient.get<MoneyMapIndustry>(`/moneymap/industries/${code}`);
