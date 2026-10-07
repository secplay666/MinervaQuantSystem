/**
 * 仓位管家 API (/api/v1/pm, docs/design/position-manager.md).  Prices are
 * forward-adjusted (qfq) as on the chart; the server keeps hfq.
 */
import { requestClient } from '#/api/request';

export type PmLabel = 'base' | 'left' | 'right' | 'top' | 'undecided';
export type PmLevelKind = 'base_zone' | 'buyback_zone' | 'neckline' | 'reference' | 'target' | 'top_neckline';
export type PmPool = 'archived' | 'buyback' | 'hold' | 'ready' | 'watch';

export const PM_LABELS: { color: string; key: PmLabel; name: string; rule: string }[] = [
  { color: 'red', key: 'right', name: '右侧', rule: '入场、减仓、退出规则全部生效' },
  { color: 'orange', key: 'top', name: '顶部', rule: '只做退出，不再入场' },
  { color: 'gold', key: 'base', name: '筑底', rule: '只做筑底判据提示' },
  { color: 'green', key: 'left', name: '左侧', rule: '规则休眠' },
  { color: 'default', key: 'undecided', name: '未定', rule: '规则休眠，先确认方向' },
];
export const PM_LABEL = Object.fromEntries(PM_LABELS.map((l) => [l.key, l])) as Record<PmLabel, (typeof PM_LABELS)[number]>;
export const LEVEL_NAMES: Record<PmLevelKind, string> = {
  base_zone: '起涨区', buyback_zone: '回撤买入区', neckline: '颈线', reference: '参考线', target: '量度目标',
  top_neckline: '头部颈线',
};
export const POOL_NAMES: Record<PmPool, string> = { archived: '归档', buyback: '回撤关注', hold: '持有', ready: '就绪', watch: '观察' };
export const PATH_COLOR: Record<string, string> = { A: 'orange', B: 'gold', C: 'red' };
/** Grades are an ordering aid: muted, distinct hues (not the red/green of prices or status). */
export const GRADE_COLOR: Record<string, string> = { A: 'geekblue', B: 'cyan', C: 'default', D: 'default' };
export const PHASE: Record<string, { color: string; name: string }> = {
  active: { color: 'red', name: '进行中' },
  exhausted: { color: 'default', name: '已衰竭' },
  pending: { color: 'blue', name: '待突破' },
  realized: { color: 'orange', name: '已兑现' },
};
export const PRIORITY: Record<number, { color: string; name: string }> = {
  1: { color: 'red', name: '清仓警报' },
  2: { color: 'orange', name: '减仓' },
  3: { color: 'blue', name: '入场与共振' },
  4: { color: 'default', name: '提示' },
};

export interface StageView {
  days: number;
  gap?: number;
  label: PmLabel;
  ma?: number;
  name: string;
  previous?: string;
  reason: string;
  since_index: null | number;
  slope?: number;
  stage: 'advance' | 'base' | 'decline' | 'top' | 'unknown';
}

export interface PmSentinel {
  basis: string;
  crossed_on: null | string;
  days: null | number;
  direction: 'down' | 'up';
  distance: null | number;
  effective_date: string;
  entered_price: number;
  id: number;
  note: null | string;
  price: number;
  source_ref: null | string;
  status: 'active' | 'crossed' | 'removed';
  version: number;
}

export interface PmQualityBrief { grade: null | string; score: null | number }

/** A stock's SW L1 industry on the money map (C = crowding). */
export interface PmIndustry { as_of: string; c: number; code: string; high: number; low: number; name: string;
  zone: null | string; zone_name: null | string }

export interface PmRow {
  activated_on: null | string;
  breakout_close: null | number;
  buyback: null | string;
  completion_change: null | number;
  entered: boolean;
  half_entry: boolean;
  industry?: null | PmIndustry;
  last_exit: null | { date: string; name: string; rule: string };
  next_distance: null | number;
  path: null | string;
  pool: PmPool;
  pool_name: string;
  quality: null | PmQualityBrief;
  round_no: number;
  sentinels: PmSentinel[];
  change: null | number;
  close: null | number;
  completion: null | number;
  groups: string[];
  id: number;
  kind: 'etf' | 'index' | 'stock';
  label: PmLabel;
  label_name: string;
  label_source: 'manual' | 'system' | null;
  latest: null | string;
  main_index: null | string;
  main_index_label: null | PmLabel;
  main_index_name: null | string;
  name: null | string;
  neckline: null | number;
  next_price: null | number;
  next_step: null | string;
  note: null | string;
  phase: null | string;
  phase_name: null | string;
  round_return: null | number;
  script_return: null | number;
  stage: null | StageView;
  star: number;
  symbol: string;
  target: null | number;
  top_watch: boolean;
  waiting_for: string;
  weight: number;
}

export interface PmEvent {
  close: null | number;
  completion: null | number;
  message: string;
  priority: number;
  priority_name: string;
  rule: string;
  trade_date: string;
  weight: number;
}

export interface PmLevel {
  basis: string;
  created_by: string;
  effective_date: string;
  entered_lower: null | number;
  entered_price: number;
  id: number;
  kind: PmLevelKind;
  lower: null | number;
  note: null | string;
  price: number;
  round_no: number;
  source: string;
  fraction: null | number;
  top_mode: 'confirmed' | 'observe' | null;
  version: number;
}

export interface PmDetail {
  activated_on: null | string;
  events: PmEvent[];
  index_today: null | { completion: null | number; label: PmLabel; label_name: string; phase: null | string; symbol: string };
  item: PmRow;
  label_history: { created_by: string; effective_date: string; label: PmLabel; label_name: string; reason: null | string; source: string }[];
  label_mode: 'manual' | 'suggest';
  ladder: { completion: number; done_on: null | string; keep: number; price: number }[];
  levels: PmLevel[];
  peak_close: null | number;
  prompts: PmEvent[];
  round_no: number;
  segments: PmSegment[];
  auto_base: boolean;
  daily_range: null | number;
  path: null | { levels: Record<string, any>; name: string; path: 'A' | 'B' | 'C'; pullback: number; reason: string };
  quality: null | { as_of: string; dims: Record<string, { name: string; pe?: null | number; period?: null | string; score: null | number; value: any }>;
                    grade: null | string; score: null | number };
  zone_event: null | PmEvent;
}

export interface PmSegment {
  entry_close: number;
  entry_date: string;
  entry_weight: number;
  exit_close: number;
  exit_date: null | string;
  return: number;
}

export interface PmTierItem {
  completion: null | number;
  completion_change: null | number;
  distance: null | number;
  id: number;
  label: PmLabel;
  messages?: string[];
  name: null | string;
  priority?: number;
  quality: null | PmQualityBrief;
  star: number;
  symbol: string;
  waiting_for: string;
}

export interface PmTier { items: PmTierItem[]; name: string; tier: number }

export interface PmSignal {
  id: number;
  item_id: number;
  message: string;
  name: null | string;
  payload: { close?: number; completion?: null | number; weight?: number };
  priority: number;
  priority_name: string;
  read: boolean;
  rule: string;
  symbol: string;
  trade_date: string;
}

export interface PmIndexBand {
  change: null | number;
  close: number;
  item_id: null | number;
  label: PmLabel;
  label_name: string;
  label_source: 'manual' | 'system';
  latest: string;
  name: string;
  stage: null | StageView;
  symbol: string;
}

export interface PmBoard {
  counts: Record<PmLabel, number>;
  indices: PmIndexBand[];
  items: PmRow[];
  label_mode: 'manual' | 'suggest';
  latest: null | string;
  new_signals: number;
  pools: Record<Exclude<PmPool, 'archived'>, number>;
  signals: PmSignal[];
  tiers: PmTier[];
  unread: number;
}

export interface PmSettings {
  auto_base: boolean;
  crowd_high: number;
  crowd_low: number;
  evaluated_through: null | string;
  label_mode: 'manual' | 'suggest';
  presets: { key: string; name: string; params: Record<string, number> }[];
  push_daily: boolean;
  rule_defaults: Record<string, any>;
  rule_overrides: Record<string, any>;
  rule_params: Record<string, any>;
  stage_overrides: Record<string, number>;
  stage_params: Record<string, number>;
  stage_preset: string;
}

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
  stage: null | StageView;
  symbol: string;
}

export interface PmJob {
  created_at: string;
  error: null | string;
  finished_at: null | string;
  id: string;
  params: Record<string, number>;
  result: null | Record<string, any>;
  status: 'done' | 'failed' | 'queued' | 'running';
}

export interface LevelEntry {
  basis?: 'hfq' | 'none' | 'qfq';
  effective_date?: string;
  fraction?: number;
  kind: PmLevelKind;
  lower?: number;
  new_round?: boolean;
  note?: string;
  price: number;
  source?: 'drawing' | 'manual' | 'pattern';
  top_mode?: 'confirmed' | 'observe';
}

export const pmItemsApi = () =>
  requestClient.get<{ groups: string[]; items: PmRow[]; label_mode: 'manual' | 'suggest' }>('/pm/items');
export const pmAddItemsApi = (text: string, group?: string) =>
  requestClient.post<{ added: string[]; errors: string[]; skipped: string[] }>('/pm/items', { group, text });
export const pmPatchItemApi = (id: number, body: { archived?: boolean; groups?: string[]; note?: string; primary_index?: string; star?: number }) =>
  requestClient.request<{ id: number }>(`/pm/items/${id}`, { data: body, method: 'PATCH' });
export const pmItemApi = (id: number) => requestClient.get<PmDetail>(`/pm/items/${id}`);
export const pmLabelApi = (id: number, body: { label?: PmLabel; use_view?: boolean }) =>
  requestClient.put<PmDetail>(`/pm/items/${id}/label`, body);
export const pmLevelApi = (id: number, body: LevelEntry) => requestClient.post<PmDetail>(`/pm/items/${id}/levels`, body);
export const pmChartApi = (symbol: string) => requestClient.get<PmChart>(`/pm/chart/${symbol}`);
export const pmBoardApi = () => requestClient.get<PmBoard>('/pm/board');
export const pmSignalsApi = (unread = false) =>
  requestClient.get<{ signals: PmSignal[]; unread: number }>('/pm/signals', { params: { unread } });
export const pmReadSignalsApi = (ids?: number[]) =>
  requestClient.post<{ marked: number; unread: number }>('/pm/signals/read', { ids: ids ?? null });
export const pmSettingsApi = () => requestClient.get<PmSettings>('/pm/settings');
export const pmSaveSettingsApi = (body: Partial<{ auto_base: boolean; crowd_high: number; crowd_low: number; label_mode: string; push_daily: boolean; rule_params: Record<string, any>;
  stage_params: Record<string, number>; stage_preset: string }>) => requestClient.put<PmSettings>('/pm/settings', body);
export const pmStageApi = (symbol: string, preset?: string) =>
  requestClient.get<{ kind: string; name: string; params: Record<string, number>; segments: { end: string; name: string; stage: string; start: string }[];
    symbol: string; view: null | StageView }>(`/pm/stage/${symbol}`, { params: { preset } });
export const pmStartBacktestApi = (body: { end?: string; params?: Record<string, number>; preset: string; start?: string }) =>
  requestClient.post<PmJob>('/pm/stage-backtest', body);
export const pmJobApi = (id: string) => requestClient.get<PmJob>(`/pm/jobs/${id}`);

export interface SentinelEntry { basis?: string; direction?: 'down' | 'up'; note?: string; price: number; source_ref?: string }
export const pmAddSentinelApi = (itemId: number, body: SentinelEntry) =>
  requestClient.post<PmDetail>(`/pm/items/${itemId}/sentinels`, body);
export const pmMoveSentinelApi = (id: number, body: SentinelEntry) =>
  requestClient.request<PmDetail>(`/pm/sentinels/${id}`, { data: body, method: 'PATCH' });
export const pmRemoveSentinelApi = (id: number) => requestClient.delete<PmDetail>(`/pm/sentinels/${id}`);

export type PmBreakoutRow = PmRow & { from_breakout: null | number };
export const pmBreakoutsApi = () =>
  requestClient.get<{ latest: null | string; removed: (Partial<PmRow> & { reason: string; removed_on: string })[];
                      resonant: PmBreakoutRow[]; waiting: PmBreakoutRow[] }>('/pm/lists/breakouts');
export interface PmTopRow {
  change?: null | number;
  close: null | number;
  completion: null | number;
  confirmed_close?: null | number;
  confirmed_on?: string;
  distance?: null | number;
  how?: string;
  id: number;
  label?: PmLabel;
  name: null | string;
  state: 'confirmed' | 'observing' | 'watching';
  symbol: string;
  top_neckline?: number;
  verdict?: null | string;
}
export const pmTopsApi = () =>
  requestClient.get<{ accuracy: { early: number; neutral: number; right: number }; confirmed: PmTopRow[];
                      observing: PmTopRow[]; watching: PmTopRow[] }>('/pm/lists/tops');
export interface PmCampaign {
  archived: boolean;
  item_id: number;
  name: null | string;
  open: boolean;
  round_no: number;
  round_return: null | number;
  script_return: null | number;
  segments: PmSegment[];
  symbol: string;
  until: null | string;
}
export const pmCampaignsApi = () =>
  requestClient.get<{ rows: PmCampaign[]; totals: { average: null | number; closed: number; open: number; rounds: number;
                                                    win_rate: null | number; wins: number } }>('/pm/campaigns');
