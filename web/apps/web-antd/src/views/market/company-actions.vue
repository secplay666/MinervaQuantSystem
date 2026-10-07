<script lang="ts" setup>
import type { BuybackRow, CompanyIndustryRow, HolderChangeRow } from '#/api';

import { computed, onMounted, ref, watch } from 'vue';
import { useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';

import { Alert, Card, Select, Spin, Table, Tabs, Tag, Tooltip } from 'ant-design-vue';

import { buybacksApi, companyIndustriesApi, holderChangesApi } from '#/api';
import { pct } from '#/utils/format';

const router = useRouter();
const tab = ref<'buybacks' | 'decrease' | 'increase' | 'industries'>('buybacks');
const days = ref(90);
const industry = ref<string>();
const kind = ref<string>();
const progress = ref<string>();
const minPct = ref<number>();
const loading = ref(false);
const failed = ref('');
const asOf = ref('');
const buybacks = ref<BuybackRow[]>([]);
const holders = ref<HolderChangeRow[]>([]);
const industries = ref<CompanyIndustryRow[]>([]);

const SW_L1 = ['农林牧渔', '基础化工', '钢铁', '有色金属', '电子', '家用电器', '食品饮料', '纺织服饰', '轻工制造', '医药生物',
  '公用事业', '交通运输', '房地产', '商贸零售', '社会服务', '综合', '建筑材料', '建筑装饰', '电力设备', '国防军工', '计算机',
  '传媒', '通信', '银行', '非银金融', '汽车', '机械设备', '煤炭', '石油石化', '环保', '美容护理'];
const PROGRESS = [['004', '实施中'], ['006', '已完成'], ['001', '董事会通过'], ['002', '股东会通过'], ['007', '待股东会'], ['005', '已停止']];
const KIND_COLOR: Record<string, string> = { cancel: 'blue', incentive: 'purple', other: 'default' };
const PROGRESS_COLOR: Record<string, string> = { '004': 'processing', '006': 'success', '005': 'default' };

const missing = (value?: null | number) => value === null || value === undefined || Number.isNaN(value);
const yi = (value?: null | number) => (missing(value) ? '—' : `${(value! / 1e8).toFixed(2)} 亿`);
const ratio = (value?: null | number, digits = 2) => (missing(value) ? '—' : `${value!.toFixed(digits)}%`);
/** "0.80 ~ 1.00 亿" / "2.66 ~ 3.33%": a planned range with one unit. */
function range(lower: null | number | undefined, upper: null | number | undefined, scale: number, unit: string) {
  if (missing(lower) && missing(upper)) return '—';
  const text = (v?: null | number) => (missing(v) ? '?' : (v! / scale).toFixed(2));
  return missing(upper) || lower === upper ? `${text(lower)}${unit}` : `${text(lower)} ~ ${text(upper)}${unit}`;
}
const chart = (symbol: string) => router.push({ path: '/chart', query: { symbol } });

async function load() {
  loading.value = true;
  failed.value = '';
  try {
    if (tab.value === 'buybacks') {
      const result = await buybacksApi({ days: days.value, industry: industry.value, kind: kind.value,
                                         min_pct: minPct.value, progress: progress.value });
      buybacks.value = result.rows;
      asOf.value = result.as_of;
    } else if (tab.value === 'industries') {
      const result = await companyIndustriesApi(days.value);
      industries.value = result.rows;
      asOf.value = result.as_of;
    } else {
      const result = await holderChangesApi({ days: days.value, direction: tab.value === 'increase' ? '增持' : '减持',
                                              industry: industry.value, min_pct: minPct.value });
      holders.value = result.rows;
      asOf.value = result.as_of;
    }
  } catch (error: any) {
    failed.value = error?.response?.data?.detail?.message ?? '回购增持数据暂不可用';
  } finally {
    loading.value = false;
  }
}

/**
 * Click-to-sort on one field of the rows (all rows, across pages).  Numbers start from the largest, dates from
 * the newest, text from A; empty values stay at the bottom either way.  ``tip`` names the field when a cell
 * shows more than one value.
 */
function sortBy(field: string, tip?: string, first: 'ascend' | 'descend' = 'descend') {
  const empty = (v: unknown) => v === null || v === undefined || (typeof v === 'number' && Number.isNaN(v));
  return {
    sorter: (a: Record<string, any>, b: Record<string, any>, order?: null | string) => {
      const x = a[field];
      const y = b[field];
      if (empty(x) || empty(y)) return empty(x) && empty(y) ? 0 : (empty(x) ? 1 : -1) * (order === 'descend' ? -1 : 1);
      return typeof x === 'string' ? x.localeCompare(y, 'zh-CN') : x - y;
    },
    sortDirections: first === 'descend' ? ['descend', 'ascend'] : ['ascend', 'descend'],
    ...(tip ? { showSorterTooltip: { title: tip } } : {}),
  };
}

const buybackColumns = [
  { dataIndex: 'latest_notice_date', title: '最新公告', width: 110, ...sortBy('latest_notice_date') },
  { key: 'stock', title: '股票', width: 120 },
  { dataIndex: 'industry', title: '行业', width: 100, ...sortBy('industry', undefined, 'ascend') },
  { key: 'kind', title: '用途', width: 140 },
  { key: 'progress', title: '进度', width: 100 },
  { key: 'plan', title: '计划金额 / 占市值', align: 'right', width: 160, ...sortBy('plan_pct_lower', '按计划占市值（下限）排序') },
  { key: 'done', title: '已回购 / 占市值', align: 'right', width: 135, ...sortBy('done_pct', '按已回购占市值排序') },
  { key: 'price', title: '回购均价 / 现价', align: 'right', width: 140, ...sortBy('close_vs_avg', '按现价 ÷ 均价排序', 'ascend') },
  { key: 'period', title: '首次公告 / 期限', width: 180, ...sortBy('notice_date', '按首次公告日排序') },
];
const holderColumns = [
  { dataIndex: 'notice_date', title: '公告日', width: 100, ...sortBy('notice_date') },
  { key: 'stock', title: '股票', width: 120 },
  { dataIndex: 'industry', title: '行业', width: 100, ...sortBy('industry', undefined, 'ascend') },
  { dataIndex: 'holder', title: '股东', ellipsis: true },
  { key: 'shares', title: '变动股数', align: 'right', ...sortBy('change_shares') },
  { key: 'pct', title: '占总股本', align: 'right', ...sortBy('change_pct_total') },
  { key: 'amount', title: '约合金额', align: 'right', ...sortBy('amount') },
  { key: 'hold', title: '变动后持股', align: 'right', ...sortBy('hold_pct_after') },
  { dataIndex: 'channel', title: '方式', width: 110, ...sortBy('channel', undefined, 'ascend') },
  { key: 'period', title: '变动期间', width: 180, ...sortBy('end_date', '按变动结束日排序') },
];
const industryColumns = [
  { dataIndex: 'industry', title: '申万一级行业', ...sortBy('industry', undefined, 'ascend') },
  { dataIndex: 'buyback_companies', title: '在回购的公司', align: 'right', ...sortBy('buyback_companies') },
  { dataIndex: 'cancel_companies', title: '其中注销', align: 'right', ...sortBy('cancel_companies') },
  { key: 'buyback_amount', title: '回购计划金额', align: 'right', ...sortBy('buyback_amount') },
  { dataIndex: 'increase_companies', title: '股东增持（家）', align: 'right', ...sortBy('increase_companies') },
  { key: 'increase_amount', title: '增持金额', align: 'right', ...sortBy('increase_amount') },
  { dataIndex: 'decrease_companies', title: '股东减持（家）', align: 'right', ...sortBy('decrease_companies') },
  { key: 'decrease_amount', title: '减持金额', align: 'right', ...sortBy('decrease_amount') },
];
const pctOptions = computed(() => (tab.value === 'buybacks' ? [0.5, 1, 2] : [0.5, 1, 5]));

watch([tab], () => {
  minPct.value = undefined;
  void load();
});
watch([days, industry, kind, progress, minPct], () => void load());
onMounted(load);
</script>

<template>
  <Page title="回购增持" :description="asOf ? `公司回购和大股东增减持的公告，数据截至 ${asOf}。只是信息，不作为买入信号` : ''">
    <Alert v-if="failed" type="warning" show-icon :message="failed" class="mb-3" />
    <Card size="small">
      <Tabs v-model:active-key="tab" size="small">
        <Tabs.TabPane key="buybacks" tab="回购" />
        <Tabs.TabPane key="increase" tab="股东增持" />
        <Tabs.TabPane key="decrease" tab="股东减持" />
        <Tabs.TabPane key="industries" tab="按行业汇总" />
      </Tabs>
      <div class="mb-3 flex flex-wrap items-center gap-2">
        <Select v-model:value="days" size="small" class="!w-28"
                :options="[30, 90, 180, 365].map((d) => ({ label: `近 ${d} 天`, value: d }))" />
        <Select v-if="tab !== 'industries'" v-model:value="industry" size="small" class="!w-32" allow-clear placeholder="全部行业"
                :options="SW_L1.map((name) => ({ label: name, value: name }))" />
        <template v-if="tab === 'buybacks'">
          <Select v-model:value="kind" size="small" class="!w-40" allow-clear placeholder="全部用途"
                  :options="[{ label: '注销', value: 'cancel' }, { label: '股权激励/员工持股', value: 'incentive' }, { label: '其他', value: 'other' }]" />
          <Select v-model:value="progress" size="small" class="!w-32" allow-clear placeholder="全部进度"
                  :options="PROGRESS.map(([value, label]) => ({ label, value }))" />
        </template>
        <Select v-if="tab !== 'industries'" v-model:value="minPct" size="small" class="!w-36" allow-clear placeholder="占比不限"
                :options="pctOptions.map((v) => ({ label: `${tab === 'buybacks' ? '计划' : '变动'} ≥ ${v}%`, value: v }))" />
      </div>
      <Spin :spinning="loading">
        <Table v-if="tab === 'buybacks'" size="small" row-key="plan_id" :columns="buybackColumns as any" :data-source="buybacks"
               :pagination="{ pageSize: 30, showTotal: (n: number) => `共 ${n} 个计划` }">
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'stock'">
              <a @click="chart(record.symbol)">{{ record.name || record.symbol }}</a>
              <div class="text-xs text-gray-400">{{ record.symbol }}</div>
            </template>
            <template v-else-if="column.key === 'kind'">
              <Tooltip :title="record.purpose"><Tag :color="KIND_COLOR[record.kind]">{{ record.kind_name }}</Tag></Tooltip>
            </template>
            <template v-else-if="column.key === 'progress'">
              <Tag :color="PROGRESS_COLOR[record.progress] ?? 'warning'">{{ record.progress_name }}</Tag>
            </template>
            <template v-else-if="column.key === 'plan'">
              {{ range(record.amount_lower, record.amount_upper, 1e8, ' 亿') }}
              <div class="text-xs text-gray-400">{{ range(record.plan_pct_lower, record.plan_pct_upper, 1, '%') }}</div>
            </template>
            <template v-else-if="column.key === 'done'">
              <template v-if="record.done_amount">
                {{ yi(record.done_amount) }}
                <div class="text-xs text-gray-400">{{ ratio(record.done_pct) }}</div>
              </template>
              <span v-else class="text-gray-400">—</span>
            </template>
            <template v-else-if="column.key === 'price'">
              {{ record.done_avg_price?.toFixed(2) ?? '—' }} / {{ record.close?.toFixed(2) ?? '—' }}
              <div v-if="record.close_vs_avg" class="text-xs text-gray-400">现价 ÷ 均价 {{ record.close_vs_avg.toFixed(2) }}</div>
            </template>
            <template v-else-if="column.key === 'period'">
              {{ record.notice_date }}
              <div v-if="record.start_date || record.end_date" class="text-xs text-gray-400">
                {{ record.start_date ?? '—' }} ~ {{ record.end_date ?? '—' }}
              </div>
            </template>
          </template>
        </Table>
        <Table v-else-if="tab === 'industries'" size="small" row-key="industry" :columns="industryColumns as any"
               :data-source="industries" :pagination="false">
          <template #bodyCell="{ column, record }">
            <template v-if="String(column.key).endsWith('_amount')">{{ yi(record[String(column.key)]) }}</template>
          </template>
        </Table>
        <Table v-else size="small" row-key="change_key" :columns="holderColumns as any" :data-source="holders"
               :pagination="{ pageSize: 30, showTotal: (n: number) => `共 ${n} 条` }">
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'stock'">
              <a @click="chart(record.symbol)">{{ record.name || record.symbol }}</a>
              <div class="text-xs text-gray-400">{{ record.symbol }}</div>
            </template>
            <template v-else-if="column.key === 'shares'">{{ record.change_shares ? `${(record.change_shares / 1e4).toFixed(2)} 万股` : '—' }}</template>
            <template v-else-if="column.key === 'pct'">{{ ratio(record.change_pct_total) }}</template>
            <template v-else-if="column.key === 'amount'">{{ yi(record.amount) }}</template>
            <template v-else-if="column.key === 'hold'">{{ ratio(record.hold_pct_after) }}</template>
            <template v-else-if="column.key === 'period'">{{ record.start_date ?? '—' }} ~ {{ record.end_date ?? '—' }}</template>
          </template>
        </Table>
      </Spin>
      <div class="text-muted-foreground mt-2 text-xs leading-5">
        数据来自东方财富数据中心，每晚更新。回购"计划占市值"用计划金额除以最新总市值；"进度"是按各计划的状态推断的名称。
        股东增减持的"约合金额"按成交均价估算，没有均价时用最新收盘价。
        回测（2016 年起，公告后下一交易日开盘买入、持有半年）：随便买一只股票的胜率 {{ pct(0.481, 0) }}；全部回购 {{ pct(0.563, 0) }}；
        回购注销且业绩不差、计划不低于市值 1% 约 {{ pct(0.6, 0) }}，但相对全市场只多 0.4%；大股东增持之后 {{ pct(0.476, 0) }}，减持之后 {{ pct(0.443, 0) }}。
        所以这里只是信息，不作为买入信号。
      </div>
    </Card>
  </Page>
</template>
