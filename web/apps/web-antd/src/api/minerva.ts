/**
 * Minerva business API (/api/v1).  Money is integer fen, quantities shares,
 * dates ISO strings; see docs/design/stage4-app-design.md.
 */
import { requestClient } from '#/api/request';

export interface Gate {
  detail: Record<string, any>;
  gate: string;
  hint: null | string;
  message: string;
  passed: boolean;
}

export interface Check {
  actual: null | string;
  decision: 'pass' | 'reject' | 'warn';
  limit: null | string;
  message: string;
  rule_id: string;
}

export interface Intent {
  checks: Check[];
  est_fees_fen: number;
  est_notional_fen: number;
  execute_on: string;
  execution: null | string;
  execution_note: null | string;
  filled_qty: number;
  history: { action: string; actor: string; at: string; qty_after: null | number; qty_before: null | number; reason: null | string }[];
  intent_id: string;
  limit_down_fen: null | number;
  limit_up_fen: null | number;
  name?: null | string;
  paper_locked?: boolean; // paper account: traded at the open, locked until the evening's paper run
  proposed_qty: number;
  qty: number;
  rank: null | number;
  reason: string;
  ref_price_fen: number;
  risk: 'pass' | 'reject' | 'warn';
  run_id: string;
  seq: number;
  side: 'buy' | 'sell';
  status: string;
  symbol: string;
  target_weight?: null | number;
  valid_until: string;
}

export interface DecisionRun {
  account_id: string;
  cash_fen: null | number;
  config_hash: null | string;
  created_at: string;
  created_by: string;
  data_version: null | string;
  gates: Gate[];
  kind: 'forced' | 'monitor' | 'rebalance';
  nav_fen: null | number;
  next_session: null | string;
  positions: null | number;
  reason: null | string;
  run_id: string;
  status: 'blocked' | 'complete' | 'failed' | 'superseded';
  strategy_id: null | string;
  summary: null | Record<string, any>;
  trade_date: string;
}

export interface DecisionDetail extends DecisionRun {
  account_mode: null | string;
  paper_cutoff: null | string;
  events: { action_hint: null | string; body: null | string; event_id: string; level: string; symbol: null | string; title: string }[];
  intents: Intent[];
  run_checks: Check[];
  targets: { explanation: Record<string, any>; name?: null | string; rank: null | number; score: null | number; symbol: string; target_qty: null | number; target_weight: number }[];
}

export interface Account {
  account_id: string;
  holdings_confirmed_date: null | string;
  initial_cash_fen: number;
  is_active: boolean;
  latest: null | { cash_fen: number; market_value_fen: number; nav_fen: number; positions: number; trade_date: string };
  mode: 'manual' | 'paper';
  name: string;
  note: null | string;
  start_date: string;
  strategy_config: string;
}

export interface Holding {
  close: null | number;
  cost_fen: number;
  market_value_fen: null | number;
  name: null | string;
  pnl_fen: null | number;
  qty: number;
  sellable: number;
  symbol: string;
  weight: null | number;
}

export interface AccountDetail extends Account {
  cash_fen: number;
  holdings: Holding[];
  market_value_fen: number;
  nav_fen: number;
}

export interface EventItem {
  account_id: null | string;
  action_hint: null | string;
  at: string;
  body: null | string;
  category: string;
  event_id: string;
  level: 'critical' | 'info' | 'warning';
  read: boolean;
  run_id: null | string;
  symbol: null | string;
  title: string;
  trade_date: null | string;
}

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

export type BarPeriod = 'day' | 'month' | 'week';

export interface ChartMarks {
  dividends: { bonus_per_10: null | number; cash_per_10: null | number; ex_date: string; plan_profile: null | string;
               report_date: string; transfer_per_10: null | number }[];
  fills: { account_id: string; account_name: string; price: number; qty: number; side: 'buy' | 'sell'; source: string;
           trade_date: string }[];
  reports: { notice_date: string; report_date: string }[];
  risk: { end_date: null | string; start_date: string; start_title: null | string; status: string }[];
  suspensions: { end_inferred?: boolean; reason: null | string; suspend_end: null | string; suspend_start: string }[];
  etf?: EtfMark[]; // index charts: abnormal broad-ETF subscription days
}
export interface EtfMark { abnormal: 'in' | 'out'; flow: number; flow_pct: number; group: string; strong: boolean; trade_date: string; z: number }

export interface SavedOverlay {
  extendData?: unknown;
  lock?: boolean;
  name: string;
  points: { timestamp: number; value: number }[];
  styles?: unknown;
}

export interface Overview {
  breadth: { amount_cny: number; down: number; flat: number; limit_down: number; limit_up: number; traded: number; up: number };
  indices: { change_pct: null | number; close: number; name: string; symbol: string }[];
  industries: { change_pct: number; count: number; name: string }[];
  previous_session: null | string;
  session: string;
}

export interface ManagedUser {
  display_name: string;
  id: number;
  is_active: boolean;
  last_login_at: null | string;
  must_change_password: boolean;
  permissions: string[];
  roles: string[];
  temporary_password?: string;
  totp_enabled: boolean;
  username: string;
}

// -- system --------------------------------------------------------------------------------
export const metaApi = () => requestClient.get<{ environment: string; environment_label: string; name: string }>('/meta');
export const systemStatusApi = () => requestClient.get<Record<string, any>>('/system/status');

export interface NotifyDelivery {
  attempted_at: string;
  attempts: number;
  channel: string;
  error: null | string;
  event_id: string;
  level: string;
  sent_at: null | string;
  status: string;
  title: string;
}
export interface NotifyStatus {
  channels: { label: string; name: string }[];
  deliveries: NotifyDelivery[];
  link: null | string;
  lookback_hours: number;
  min_level: string;
  problems: string[];
}
export const notifyStatusApi = () => requestClient.get<NotifyStatus>('/system/notify');
export const notifyTestApi = () =>
  requestClient.post<{ results: { channel: string; error: null | string; status: string }[] }>('/system/notify/test');

// -- decisions -----------------------------------------------------------------------------
export const decisionsApi = (params: { account_id?: string; include_superseded?: boolean; limit?: number; trade_date?: string }) =>
  requestClient.get<DecisionRun[]>('/decisions', { params });
export const decisionApi = (runId: string) => requestClient.get<DecisionDetail>(`/decisions/${runId}`);
export const approveIntentApi = (id: string, reason?: string) => requestClient.post<Intent>(`/intents/${id}/approve`, { reason });
export const rejectIntentApi = (id: string, reason: string) => requestClient.post<Intent>(`/intents/${id}/reject`, { reason });
export const modifyIntentApi = (id: string, qty: number, reason: string) =>
  requestClient.post<Intent>(`/intents/${id}/modify`, { qty, reason });
export const overrideIntentApi = (id: string, reason: string) => requestClient.post<Intent>(`/intents/${id}/override`, { reason });
export const reviewBatchApi = (runId: string, body: { action: 'approve' | 'reject'; intent_ids: string[]; reason?: string }) =>
  requestClient.post<{ done: number; failed: { intent_id: string; message: string; symbol: null | string }[] }>(
    `/decisions/${runId}/review-batch`, body);
export interface TodoAccount {
  account_id: string;
  latest: { failed_gate: null | { gate: string; message: string }; kind: string; reason: null | string; run_id: string;
            status: string; trade_date: string };
  mode: string;
  name: string;
  pending: number;
  pending_runs: { kind: string; next_session: null | string; paper_cutoff: null | string; pending: number; run_id: string;
                  trade_date: string; valid_until: string }[];
}
export const todoApi = () =>
  requestClient.get<{ accounts: TodoAccount[]; critical_unread: { account_id: null | string; at: string; event_id: string; run_id: null | string; title: string }[] }>('/todo');
export const approveAllApi = (runId: string, includeWarnings: boolean) =>
  requestClient.post<{ approved: number }>(`/decisions/${runId}/approve-all`, { include_warnings: includeWarnings });
export const triggerDecisionApi = (accountId: string, reason: string, rerun = false) =>
  requestClient.post<{ id: string; status: string }>('/decisions/trigger', { account_id: accountId, reason, rerun });
export const jobApi = (id: string) => requestClient.get<{ result: any; status: string }>(`/jobs/${id}`);

// -- accounts ------------------------------------------------------------------------------
export const accountsApi = () => requestClient.get<Account[]>('/accounts');
export const accountApi = (id: string) => requestClient.get<AccountDetail>(`/accounts/${id}`);
export const createAccountApi = (body: Record<string, any>) => requestClient.post<Account>('/accounts', body);
export const patchAccountApi = (id: string, body: Record<string, any>) =>
  requestClient.request<Account>(`/accounts/${id}`, { data: body, method: 'PATCH' });
export const accountNavApi = (id: string) =>
  requestClient.get<{ cash_fen: number; market_value_fen: number; nav_fen: number; positions: number; trade_date: string }[]>(`/accounts/${id}/nav`);
export interface Exposure {
  as_of: null | string;
  cash_weight: null | number;
  effective_names: number;
  industries: { count: number; name: string; target_count: number; target_weight: number; weight: number }[];
  nav_fen: number;
  target_date: null | string;
  target_run_id: null | string;
  top10_weight: number;
}
export const accountExposureApi = (id: string) => requestClient.get<Exposure>(`/accounts/${id}/exposure`);
export const accountEventsApi = (id: string) => requestClient.get<Record<string, any>[]>(`/accounts/${id}/events`);
export const accountFillsApi = (id: string) => requestClient.get<Record<string, any>[]>(`/accounts/${id}/fills`);
export const addFillApi = (id: string, body: Record<string, any>) => requestClient.post(`/accounts/${id}/fills`, body);
export const holdingsPreviewApi = (id: string, body: Record<string, any>) =>
  requestClient.post<{ batch_id: string; cash_after_fen: number; cash_before_fen: number; diff: { change: number; current: number; new: number; symbol: string }[]; errors: string[] }>(
    `/accounts/${id}/holdings/preview`, body);
export const holdingsCommitApi = (id: string, batchId: string, reason: string) =>
  requestClient.post(`/accounts/${id}/holdings/commit`, { batch_id: batchId, reason });
/** A manual account's ex-rights / ex-dividend adjustment not recorded yet, with the suggested result. */
export interface CorporateAction {
  bonus_per_10: number;
  cash_fen: number; // suggested cash dividend, before tax
  cash_per_10: number;
  event_id: string;
  ex_date: string;
  name: null | string;
  new_quantity: number;
  old_quantity: number;
  plan: null | string;
  symbol: string;
  transfer_per_10: number;
}
export const corporateActionsApi = (id: string) => requestClient.get<{ rows: CorporateAction[] }>(`/accounts/${id}/corporate-actions`);
export const applyCorporateActionApi = (id: string, body: { cash: string; event_id: string; new_quantity: number; reason: string }) =>
  requestClient.post(`/accounts/${id}/corporate-actions`, body);
export const reverseEventApi = (eventId: string, reason: string) =>
  requestClient.post(`/position-events/${eventId}/reverse`, { reason });

// -- events --------------------------------------------------------------------------------
export const eventsApi = (params: { account_id?: string; category?: string; level?: string; limit?: number; unread?: boolean }) =>
  requestClient.get<{ items: EventItem[]; unread: number }>('/events', { params });
export const readEventApi = (id: string) => requestClient.post(`/events/${id}/read`);
export const readAllEventsApi = () => requestClient.post('/events/read-all');

// -- market --------------------------------------------------------------------------------
export const overviewApi = (tradeDate?: string) => requestClient.get<Overview>('/market/overview', { params: { trade_date: tradeDate } });
export const indexBarsApi = (symbol: string, limit = 250, period: BarPeriod = 'day', end?: string) =>
  requestClient.get<Bar[]>(`/market/indices/${symbol}/bars`, { params: { end, limit, period } });
export const indicesApi = () => requestClient.get<{ latest: string; name: string; symbol: string }[]>('/market/indices');
/** Index codes (sh000001, sz399006, H00300) as opposed to six-digit stock codes. */
export const isIndexSymbol = (symbol: string) => /^(sh|sz|bj)\d{6}$|^H\d{5}$/.test(symbol);
export const searchApi = (q: string) => requestClient.get<{ board: string; name: string; symbol: string }[]>('/instruments/search', { params: { q } });
export const instrumentApi = (symbol: string) => requestClient.get<Record<string, any>>(`/instruments/${symbol}`);
export const barsApi = (symbol: string, adjust = 'qfq', limit = 250, period: BarPeriod = 'day', end?: string) =>
  requestClient.get<Bar[]>(`/instruments/${symbol}/bars`, { params: { adjust, end, limit, period } });
export interface ChartPoint { date: string; price: number }
export interface ChartAnalysis {
  atr?: number;
  bars: number;
  candles: { bars: number; date: string; direction: 'bearish' | 'bullish' | 'neutral'; kind: string; label: string; name: string }[];
  fibonacci: null | { confirmed: boolean; from: ChartPoint; levels: { price: number; ratio: number }[]; to: ChartPoint };
  levels: { extreme: boolean; first_date: string; flipped: boolean; high: number; kind: 'resistance' | 'support'; last_date: string;
            low: number; price: number; score: number; touches: number }[];
  patterns: { breakout_date: null | string; direction: 'bearish' | 'bullish' | 'neutral'; end_date: string; kind: string;
              lines: { end: ChartPoint; start: ChartPoint }[]; name: string; note: string; points: ChartPoint[];
              start_date: string; status: 'confirmed' | 'failed' | 'forming'; target: null | number }[];
  pivots: (ChartPoint & { confirmed: boolean; kind: 'H' | 'L' })[];
  trendlines: { anchor: ChartPoint; broken: boolean; broken_date: null | string; channel: null | { end: ChartPoint; start: ChartPoint };
                end: ChartPoint; kind: 'down' | 'up'; score: number; start: ChartPoint; touches: number }[];
}
export const chartAnalysisApi = (symbol: string, params: { adjust: string; bars: number; period: BarPeriod; sensitivity: string }) =>
  requestClient.get<ChartAnalysis>(`/charts/${symbol}/analysis`, { params });
export const marksApi = (symbol: string) => requestClient.get<ChartMarks>(`/instruments/${symbol}/marks`);
export const drawingsApi = (symbol: string) =>
  requestClient.get<{ overlays: SavedOverlay[]; symbol: string; updated_at: null | string }>(`/charts/${symbol}/drawings`);
export const saveDrawingsApi = (symbol: string, overlays: SavedOverlay[]) =>
  requestClient.put<{ count: number }>(`/charts/${symbol}/drawings`, { overlays });
export const fundamentalsApi = (symbol: string) => requestClient.get<Record<string, any>[]>(`/instruments/${symbol}/fundamentals`);
export interface Signals {
  account_id: null | string;
  history: { account_id: string; rank: null | number; score: null | number; target_weight: number; trade_date: string }[];
  row: null | Record<string, any>;
  run_id: null | string;
  scored: number;
  trade_date: null | string;
  universe: number;
}
export const signalsApi = (symbol: string) => requestClient.get<Signals>(`/instruments/${symbol}/signals`);

// -- administration ------------------------------------------------------------------------
export interface InvitationItem {
  code?: string; // only in the response that created it
  created_at: string;
  created_by: string;
  expires_at: string;
  hint: string;
  id: number;
  max_uses: number;
  note: null | string;
  revoked_at: null | string;
  roles: string[];
  state: 'active' | 'expired' | 'revoked' | 'used_up';
  used_count: number;
  users: string[];
}
export const invitationsApi = () => requestClient.get<InvitationItem[]>('/invitations');
export const createInvitationApi = (body: { days: number; max_uses: number; note?: string; roles: string[] }) =>
  requestClient.post<InvitationItem>('/invitations', body);
export const revokeInvitationApi = (id: number) => requestClient.post<InvitationItem>(`/invitations/${id}/revoke`);
export const usersApi = () => requestClient.get<ManagedUser[]>('/users');
export const createUserApi = (body: { display_name: string; roles: string[]; username: string }) =>
  requestClient.post<ManagedUser>('/users', body);
export const patchUserApi = (id: number, body: { display_name?: string; is_active?: boolean; roles?: string[] }) =>
  requestClient.request<ManagedUser>(`/users/${id}`, { data: body, method: 'PATCH' });
export const resetPasswordApi = (id: number) => requestClient.post<{ temporary_password: string }>(`/users/${id}/reset-password`);
export const unlockUserApi = (id: number) => requestClient.post(`/users/${id}/unlock`);
export const rolesApi = () =>
  requestClient.get<{ builtin: boolean; code: string; description: null | string; name: string; permissions: string[] }[]>('/roles');
export const permissionsApi = () => requestClient.get<{ code: string; group: string; name: string }[]>('/permissions');
export const createRoleApi = (body: { code: string; description?: string; name: string; permissions: string[] }) =>
  requestClient.post('/roles', body);
export const setRolePermissionsApi = (code: string, permissions: string[]) =>
  requestClient.put(`/roles/${code}/permissions`, { permissions });
export const sessionsApi = () => requestClient.get<Record<string, any>[]>('/sessions');
export const revokeSessionApi = (id: number) => requestClient.delete(`/sessions/${id}`);
export const auditApi = (params: { action?: string; actor?: string; before_id?: number; limit?: number }) =>
  requestClient.get<Record<string, any>[]>('/audit', { params });

// -- ETF 资金（宽基 ETF 份额变化估算的净申购，app/etf.py） -------------------------------------
export interface EtfAbnormal { abnormal: 'in' | 'out'; flow: number; flow_pct: number; strong: boolean; trade_date: string; z: number }
export interface EtfGroupSummary {
  abnormal_in_250d: number;
  abnormal_out_250d: number;
  aum: null | number; // CNY
  chart_symbol: string;
  flow_1d: null | number;
  flow_5d: null | number;
  flow_20d: null | number;
  flow_60d: null | number;
  flow_250d: null | number;
  funds: number;
  id: string;
  last_abnormal: EtfAbnormal | null;
  name: string;
  strong_250d: number;
}
export interface EtfOverview {
  as_of: string; // last day both exchanges have published
  groups: EtfGroupSummary[];
  latest: string;
  rules: { baseline: number; min_share: number; strong_min_share: number; strong_z: number; z: number };
}
export interface EtfDay {
  abnormal: 'in' | 'out' | null;
  aum: null | number;
  cumulative: number;
  events: number;
  flow: null | number;
  flow_pct: null | number;
  funds: number;
  partial: boolean;
  strong: boolean;
  trade_date: string;
  z: null | number;
}
export interface EtfFund {
  aum: null | number;
  close: null | number;
  event: null | string;
  exchange: string;
  flow: null | number;
  flow_5d: null | number;
  flow_20d: null | number;
  flow_60d: null | number;
  name: string;
  recent_events: { event: string; share_change: number; trade_date: string }[];
  shares: null | number;
  symbol: string;
}
/** National-team holdings from the funds' annual and interim reports (top-10 holder tables). */
export interface EtfHolders {
  classes: { id: string; name: string }[];
  funds: {
    by_class: Record<string, number>;
    exchange: string;
    holders: { holder: string; holder_class: null | string; pct: number; rank: number; shares: number }[];
    name: string;
    national_pct: number;
    national_value: number;
    notice_date: string;
    report_date: string;
    symbol: string;
  }[];
  latest_period: null | string;
  periods: { aum: null | number; by_class: Record<string, number>; funds: number; national_share: null | number;
             national_value: number; report_date: string }[];
}
export const etfHoldersApi = (group: string) => requestClient.get<EtfHolders>(`/etf/groups/${group}/holders`);
/** 资金波段: 10-session net creation against the group's own history (point-in-time band). */
export interface EtfWave {
  after_5: null | number;
  after_20: null | number;
  after_60: null | number;
  counter: boolean;
  index_window: null | number;
  trade_date: string;
  wave: 'creation' | 'redemption';
  wave_flow: number;
  wave_pct: number;
}
export interface EtfWaves {
  base: Record<string, { mean: null | number; n: number; up: null | number }>;
  current: null | { counter: boolean; extreme: 'creation' | 'redemption' | null; high: null | number; index_window: null | number;
    low: null | number; rank: null | number; streak: number; trade_date: string; wave_flow: null | number; wave_pct: null | number };
  group: { chart_symbol: string; id: string; name: string };
  rules: { gap: number; horizons: number[]; min_history: number; sessions: number; tail: number };
  series: { high: null | number; low: null | number; rank: null | number; trade_date: string; wave_pct: null | number }[];
  waves: EtfWave[];
}
export const etfWavesApi = (group: string) => requestClient.get<EtfWaves>(`/etf/groups/${group}/waves`);
export const etfMarksApi = (symbol: string) => requestClient.get<EtfMark[]>(`/etf/marks/${symbol}`);
export const etfOverviewApi = () => requestClient.get<EtfOverview>('/etf/overview');
export const etfSeriesApi = (group: string) =>
  requestClient.get<{ as_of: string; group: { chart_symbol: string; id: string; name: string }; rows: EtfDay[] }>(`/etf/groups/${group}/series`);
export const etfFundsApi = (group: string, day?: string) =>
  requestClient.get<{ date: string; rows: EtfFund[] }>(`/etf/groups/${group}/funds`, { params: { day } });
