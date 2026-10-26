<script setup lang="ts">
import type { BuybackRow, CompanyIndustryRow, HolderChangeRow } from './info-api';

import { computed, onMounted, ref, watch } from 'vue';
import { useRouter } from 'vue-router';

import { showDialog, showFailToast } from 'vant';

import { qs, request } from '../../api';
import { pct } from '../../format';
import { missing } from './info-api';

type Tab = 'buybacks' | 'decrease' | 'increase' | 'industries';
type Sort = [field: string, label: string, dir: 'asc' | 'desc'];

const router = useRouter();
const tab = ref<Tab>('buybacks');
const days = ref(90);
const industry = ref('');
const kind = ref('');
const progress = ref('');
const minPct = ref(0);
const loading = ref(false);
const refreshing = ref(false);
const failed = ref('');
const asOf = ref('');
const buybacks = ref<BuybackRow[]>([]);
const holders = ref<HolderChangeRow[]>([]);
const industries = ref<CompanyIndustryRow[]>([]);
const sortIndex = ref(0);
const sorting = ref(false);
const industrySort = ref('');
const limit = ref(30);
const listLoading = ref(false);
let seq = 0;

const SW_L1 = ['农林牧渔', '基础化工', '钢铁', '有色金属', '电子', '家用电器', '食品饮料', '纺织服饰', '轻工制造', '医药生物',
  '公用事业', '交通运输', '房地产', '商贸零售', '社会服务', '综合', '建筑材料', '建筑装饰', '电力设备', '国防军工', '计算机',
  '传媒', '通信', '银行', '非银金融', '汽车', '机械设备', '煤炭', '石油石化', '环保', '美容护理'];
const PROGRESS = [['004', '实施中'], ['006', '已完成'], ['001', '董事会通过'], ['002', '股东会通过'], ['007', '待股东会'], ['005', '已停止']];
const PROGRESS_TYPE: Record<string, 'default' | 'primary' | 'success'> = { '004': 'primary', '005': 'default', '006': 'success' };
const KIND_COLOR: Record<string, string> = { cancel: '#1989fa', incentive: '#7232dd', other: '#969799' };
const SORTS: Record<'buybacks' | 'holders', Sort[]> = {
  buybacks: [['latest_notice_date', '最新公告', 'desc'], ['plan_pct_lower', '计划占市值（下限）', 'desc'],
             ['done_pct', '已回购占市值', 'desc'], ['close_vs_avg', '现价 ÷ 均价（低的在前）', 'asc'],
             ['notice_date', '首次公告日', 'desc'], ['industry', '行业', 'asc']],
  holders: [['notice_date', '公告日', 'desc'], ['change_shares', '变动股数', 'desc'], ['change_pct_total', '占总股本', 'desc'],
            ['amount', '约合金额', 'desc'], ['hold_pct_after', '变动后持股', 'desc'], ['end_date', '变动结束日', 'desc'],
            ['industry', '行业', 'asc']],
};
const INDUSTRY_COLUMNS: [keyof CompanyIndustryRow, string][] = [
  ['buyback_companies', '在回购的公司'], ['cancel_companies', '其中注销'], ['buyback_amount', '回购计划金额'],
  ['increase_companies', '股东增持（家）'], ['increase_amount', '增持金额'], ['decrease_companies', '股东减持（家）'],
  ['decrease_amount', '减持金额'],
];

const dayOptions = [30, 90, 180, 365].map((d) => ({ text: `近 ${d} 天`, value: d }));
const industryOptions = [{ text: '全部行业', value: '' }, ...SW_L1.map((name) => ({ text: name, value: name }))];
const kindOptions = [{ text: '全部用途', value: '' }, { text: '注销', value: 'cancel' },
                     { text: '股权激励/员工持股', value: 'incentive' }, { text: '其他', value: 'other' }];
const progressOptions = [{ text: '全部进度', value: '' }, ...PROGRESS.map(([value, text]) => ({ text: text!, value: value! }))];
const pctOptions = computed(() => [{ text: '占比不限', value: 0 },
  ...(tab.value === 'buybacks' ? [0.5, 1, 2] : [0.5, 1, 5]).map((v) => ({ text: `${tab.value === 'buybacks' ? '计划' : '变动'} ≥ ${v}%`, value: v }))]);

const yi = (value?: null | number) => (missing(value) ? '—' : `${(value / 1e8).toFixed(2)} 亿`);
const ratio = (value?: null | number, digits = 2) => (missing(value) ? '—' : `${value.toFixed(digits)}%`);
/** "0.80 ~ 1.00 亿" / "2.66 ~ 3.33%": a planned range with one unit. */
function range(lower: null | number | undefined, upper: null | number | undefined, scale: number, unit: string) {
  if (missing(lower) && missing(upper)) return '—';
  const text = (v?: null | number) => (missing(v) ? '?' : (v / scale).toFixed(2));
  return missing(upper) || lower === upper ? `${text(lower)}${unit}` : `${text(lower)} ~ ${text(upper)}${unit}`;
}

const empty = (v: unknown) => v === null || v === undefined || (typeof v === 'number' && Number.isNaN(v));
/** Sorted copy: empty values stay at the bottom either way. */
function sorted<T>(rows: T[], field: string, dir: 'asc' | 'desc'): T[] {
  return [...rows].sort((a: any, b: any) => {
    const x = a[field];
    const y = b[field];
    if (empty(x) || empty(y)) return empty(x) && empty(y) ? 0 : empty(x) ? 1 : -1;
    const cmp = typeof x === 'string' ? x.localeCompare(y, 'zh-CN') : x - y;
    return dir === 'desc' ? -cmp : cmp;
  });
}

const sorts = computed(() => SORTS[tab.value === 'buybacks' ? 'buybacks' : 'holders']);
const sort = computed(() => sorts.value[sortIndex.value] ?? sorts.value[0]!);
const rows = computed<any[]>(() => {
  if (tab.value === 'industries') return industries.value;
  return sorted<any>(tab.value === 'buybacks' ? buybacks.value : holders.value, sort.value[0], sort.value[2]);
});
const shown = computed(() => rows.value.slice(0, limit.value));
const industryRows = computed(() => {
  const key = industrySort.value;
  if (!key) return industries.value;
  return sorted(industries.value, key, key === 'industry' ? 'asc' : 'desc');
});

async function load() {
  const mine = ++seq;
  loading.value = true;
  failed.value = '';
  try {
    if (tab.value === 'buybacks') {
      const result = await request(`/company-actions/buybacks${qs({ days: days.value, industry: industry.value, kind: kind.value,
                                                                      min_pct: minPct.value || undefined, progress: progress.value })}`);
      if (mine !== seq) return;
      buybacks.value = result.rows;
      asOf.value = result.as_of;
    } else if (tab.value === 'industries') {
      const result = await request(`/company-actions/industries${qs({ days: days.value })}`);
      if (mine !== seq) return;
      industries.value = result.rows;
      asOf.value = result.as_of;
    } else {
      const result = await request(`/company-actions/holders${qs({ days: days.value, direction: tab.value === 'increase' ? '增持' : '减持',
                                                                    industry: industry.value, min_pct: minPct.value || undefined })}`);
      if (mine !== seq) return;
      holders.value = result.rows;
      asOf.value = result.as_of;
    }
    limit.value = 30;
  } catch (error: any) {
    if (mine !== seq) return;
    failed.value = error.message;
    showFailToast(error.message);
  } finally {
    if (mine === seq) {
      loading.value = false;
      refreshing.value = false;
    }
  }
}

function more() {
  limit.value += 30;
  listLoading.value = false;
}

function purpose(row: BuybackRow) {
  void showDialog({ message: row.purpose || '公告没有写明用途', title: row.kind_name });
}

watch(tab, () => {
  minPct.value = 0;
  sortIndex.value = 0;
});
watch([tab, days, industry, kind, progress, minPct], () => void load());
watch(sortIndex, () => (limit.value = 30));
onMounted(load);
</script>

<template>
  <van-nav-bar title="回购增持" left-arrow @click-left="router.back()" />
  <van-tabs v-model:active="tab">
    <van-tab name="buybacks" title="回购" />
    <van-tab name="increase" title="股东增持" />
    <van-tab name="decrease" title="股东减持" />
    <van-tab name="industries" title="按行业汇总" />
  </van-tabs>
  <van-dropdown-menu style="--van-dropdown-menu-title-font-size: 13px; --van-dropdown-menu-title-padding: 0 4px">
    <van-dropdown-item v-model="days" :options="dayOptions" />
    <van-dropdown-item v-if="tab !== 'industries'" v-model="industry" :options="industryOptions" />
    <template v-if="tab === 'buybacks'">
      <van-dropdown-item v-model="kind" :options="kindOptions" />
      <van-dropdown-item v-model="progress" :options="progressOptions" />
    </template>
    <van-dropdown-item v-if="tab !== 'industries'" v-model="minPct" :options="pctOptions" />
  </van-dropdown-menu>

  <van-pull-refresh v-model="refreshing" @refresh="load">
    <div v-if="asOf" class="note" style="margin-top: 12px">公司回购和大股东增减持的公告，数据截至 {{ asOf }}。只是信息，不作为买入信号</div>
    <van-empty v-if="failed && !rows.length" :description="failed" />
    <van-loading v-else-if="loading && !rows.length" style="padding: 48px 0; text-align: center" />
    <van-empty v-else-if="!rows.length" description="没有符合条件的记录" />

    <div v-else-if="tab === 'industries'" class="card" :style="{ opacity: loading ? 0.5 : 1 }">
      <div class="muted" style="margin-bottom: 6px">点表头按该列排序（数字从大到小），再点恢复。</div>
      <div class="table-wrap">
        <table class="table">
          <thead>
            <tr>
              <th @click="industrySort = industrySort === 'industry' ? '' : 'industry'">申万一级行业{{ industrySort === 'industry' ? ' ↑' : '' }}</th>
              <th v-for="[key, title] in INDUSTRY_COLUMNS" :key="key" @click="industrySort = industrySort === key ? '' : key">
                {{ title }}{{ industrySort === key ? ' ↓' : '' }}
              </th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in industryRows" :key="row.industry">
              <td>{{ row.industry }}</td>
              <td v-for="[key] in INDUSTRY_COLUMNS" :key="key">{{ key.endsWith('_amount') ? yi(row[key] as number) : row[key] }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <template v-else>
      <div class="ca-bar">
        <span class="muted">共 {{ rows.length }} {{ tab === 'buybacks' ? '个计划' : '条' }}</span>
        <span class="ca-sort" @click="sorting = true">排序：{{ sort[1] }} <van-icon name="arrow-down" /></span>
      </div>
      <van-list v-model:loading="listLoading" :finished="limit >= rows.length" finished-text="没有更多了" :offset="200"
                :style="{ opacity: loading ? 0.5 : 1 }" @load="more">
        <van-cell-group inset>
          <template v-if="tab === 'buybacks'">
            <van-cell v-for="r in (shown as BuybackRow[])" :key="r.plan_id" is-link title-style="flex: 5"
                      @click="router.push(`/instruments/${r.symbol}`)">
              <template #title>
                <span class="ca-name">{{ r.name || r.symbol }}</span> <span class="muted">{{ r.symbol }}</span>
              </template>
              <template #value>
                <van-tag :type="PROGRESS_TYPE[r.progress] ?? 'warning'" plain>{{ r.progress_name }}</van-tag>
              </template>
              <template #label>
                <div class="ca-line">
                  {{ r.industry ?? '—' }} ·
                  <van-tag :color="KIND_COLOR[r.kind]" plain @click.stop="purpose(r)">{{ r.kind_name }} ⓘ</van-tag>
                  · 最新公告 {{ r.latest_notice_date }}
                </div>
                <div class="ca-line">计划 {{ range(r.amount_lower, r.amount_upper, 1e8, ' 亿') }}（占市值 {{ range(r.plan_pct_lower, r.plan_pct_upper, 1, '%') }}）</div>
                <div class="ca-line">已回购 {{ r.done_amount ? `${yi(r.done_amount)}（${ratio(r.done_pct)}）` : '—' }}</div>
                <div class="ca-line">
                  回购均价 / 现价 {{ r.done_avg_price?.toFixed(2) ?? '—' }} / {{ r.close?.toFixed(2) ?? '—' }}
                  <template v-if="r.close_vs_avg">（现价 ÷ 均价 {{ r.close_vs_avg.toFixed(2) }}）</template>
                </div>
                <div class="ca-line">
                  首次公告 {{ r.notice_date }}<template v-if="r.start_date || r.end_date"> · 期限 {{ r.start_date ?? '—' }} ~ {{ r.end_date ?? '—' }}</template>
                </div>
              </template>
            </van-cell>
          </template>
          <template v-else>
            <van-cell v-for="r in (shown as HolderChangeRow[])" :key="r.change_key" is-link title-style="flex: 3"
                      @click="router.push(`/instruments/${r.symbol}`)">
              <template #title>
                <span class="ca-name">{{ r.name || r.symbol }}</span> <span class="muted">{{ r.symbol }}</span>
              </template>
              <template #value>
                <div style="color: #323233; font-weight: 600">{{ yi(r.amount) }}</div>
                <div class="muted">约合金额</div>
              </template>
              <template #label>
                <div class="ca-line">{{ r.industry ?? '—' }} · 公告 {{ r.notice_date }}<template v-if="r.channel"> · {{ r.channel }}</template></div>
                <div class="ca-line">股东：{{ r.holder ?? '—' }}</div>
                <div class="ca-line">
                  变动 {{ r.change_shares ? `${(r.change_shares / 1e4).toFixed(2)} 万股` : '—' }}（占总股本 {{ ratio(r.change_pct_total) }}）
                  · 变动后持股 {{ ratio(r.hold_pct_after) }}
                </div>
                <div class="ca-line">变动期间 {{ r.start_date ?? '—' }} ~ {{ r.end_date ?? '—' }}</div>
              </template>
            </van-cell>
          </template>
        </van-cell-group>
      </van-list>
    </template>

    <div class="note">
      数据来自东方财富数据中心，每晚更新。回购"计划占市值"用计划金额除以最新总市值；"进度"是按各计划的状态推断的名称；点用途标签看公告里的用途原文。
      股东增减持的"约合金额"按成交均价估算，没有均价时用最新收盘价。
      回测（2016 年起，公告后下一交易日开盘买入、持有半年）：随便买一只股票的胜率 {{ pct(0.481, 0) }}；全部回购 {{ pct(0.563, 0) }}；
      回购注销且业绩不差、计划不低于市值 1% 约 {{ pct(0.6, 0) }}，但相对全市场只多 0.4%；大股东增持之后 {{ pct(0.476, 0) }}，减持之后 {{ pct(0.443, 0) }}。
      所以这里只是信息，不作为买入信号。
    </div>
  </van-pull-refresh>

  <van-action-sheet v-model:show="sorting" title="排序" cancel-text="取消" close-on-click-action
                    :actions="sorts.map((s, i) => ({ name: s[1], color: i === sortIndex ? '#1989fa' : undefined }))"
                    @select="(_: unknown, index: number) => (sortIndex = index)" />
</template>

<style scoped>
.ca-bar { display: flex; align-items: center; justify-content: space-between; margin: 0 16px 8px; }
.ca-sort { padding: 10px 0 10px 12px; color: #1989fa; font-size: 13px; }
.ca-name { font-weight: 600; }
.ca-line { margin-top: 2px; line-height: 18px; }
</style>
