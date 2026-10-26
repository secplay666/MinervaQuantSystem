<script setup lang="ts">
import type { EChartsOption } from 'echarts';

import type { Bar, HolderCount } from './market/market-api';

import { computed, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import { showFailToast } from 'vant';

import { request } from '../api';
import EChart from '../components/EChart.vue';
import KChart from '../components/KChart.vue';
import { big, color, DOWN, pct, UP } from '../format';
import { can } from '../store';
import { barsApi, BOARD, FAMILY_LABEL, FAMILY_ORDER, holdersApi, RISK } from './market/market-api';

interface Signals {
  history: { account_id: string; rank: null | number; score: null | number; target_weight: number; trade_date: string }[];
  row: null | Record<string, any>;
  run_id: null | string;
  scored: number;
  trade_date: null | string;
}

const route = useRoute();
const router = useRouter();
const symbol = computed(() => String(route.params.symbol));
const info = ref<Record<string, any>>();
const last = ref<Bar>();
const fundamentals = ref<Record<string, any>[]>([]);
const signals = ref<Signals>();
const holders = ref<HolderCount[]>([]);

const families = computed(() => FAMILY_ORDER
  .map((key) => ({ label: FAMILY_LABEL[key] ?? key, value: signals.value?.row?.[`f_${key}`] as null | number | undefined }))
  .filter((f) => f.value !== undefined));
const scoreOption = computed<EChartsOption>(() => {
  const rows = [...families.value].reverse();
  return {
    grid: { bottom: 20, containLabel: true, left: 8, right: 16, top: 8 },
    series: [{ data: rows.map((f) => ({ itemStyle: { color: (f.value ?? 0) >= 0 ? UP : DOWN }, value: f.value ?? null })), type: 'bar' }],
    tooltip: { trigger: 'axis', valueFormatter: (v: any) => (typeof v === 'number' ? v.toFixed(3) : '—') },
    xAxis: { type: 'value' },
    yAxis: { data: rows.map((f) => f.label), type: 'category' },
  };
});

/** The latest count and the latest report stake (periodic reports only). */
const latestCount = computed(() => [...holders.value].reverse().find((r) => r.holders !== null));
const latestStake = computed(() => [...holders.value].reverse().find((r) => r.top10_pct !== null));
const recentCounts = computed(() => holders.value.filter((r) => r.holders !== null).slice(-6).reverse());

const wan = (v?: null | number) => (v === null || v === undefined ? '—' : `${(v / 1e4).toFixed(2)} 万`);

async function load(code: string) {
  info.value = undefined;
  last.value = undefined;
  signals.value = undefined;
  try {
    const [detail, rows, bars, holderRows] = await Promise.all([
      request(`/instruments/${code}`),
      request<Record<string, any>[]>(`/instruments/${code}/fundamentals`).catch(() => []),
      barsApi(code, 'none', 2, 'day').catch(() => [] as Bar[]),
      holdersApi(code).catch(() => [] as HolderCount[]),
    ]);
    if (code !== symbol.value) return;
    info.value = detail;
    fundamentals.value = rows;
    last.value = bars.at(-1);
    holders.value = holderRows;
    if (can('decision:view')) signals.value = await request<Signals>(`/instruments/${code}/signals`).catch(() => undefined);
  } catch (error: any) {
    showFailToast(error.message);
  }
}

watch(symbol, load, { immediate: true });
</script>

<template>
  <van-nav-bar :title="info ? `${info.name} ${symbol}` : symbol" left-arrow @click-left="router.back()" />

  <div class="quote">
    <template v-if="last">
      <b :style="{ color: color(last.pct_change), fontSize: '22px' }">{{ last.close.toFixed(2) }}</b>
      <span :style="{ color: color(last.pct_change) }">
        {{ last.pct_change === null || last.pct_change === undefined ? '' : `${last.pct_change > 0 ? '+' : ''}${last.pct_change.toFixed(2)}%` }}
      </span>
      <span class="muted">{{ last.trade_date }} · 成交额 {{ big(last.amount) }}</span>
    </template>
    <span style="flex: 1"></span>
    <van-button size="small" type="primary" plain style="white-space: nowrap" @click="router.push(`/chart/${symbol}`)">大图看K线</van-button>
  </div>
  <KChart :key="symbol" :symbol="symbol" :height="320" compact />
  <p class="note" style="margin-top: 4px">前复权以最近一次除权为锚点，仅用于展示；收益计算使用后复权。</p>

  <van-cell-group v-if="info" inset title="基本信息">
    <van-cell title="板块" :value="BOARD[info.board] ?? info.board" />
    <van-cell title="申万行业" :value="info.industry ? `${info.industry.l1_name} / ${info.industry.l2_name} / ${info.industry.l3_name}` : '—'" />
    <van-cell title="风险警示">
      <template #value>
        <van-tag :type="info.risk_status === 'normal' ? 'success' : 'danger'">{{ RISK[info.risk_status] ?? info.risk_status }}</van-tag>
      </template>
    </van-cell>
    <van-cell title="上市日期" :value="info.list_date" />
    <van-cell v-if="info.delist_date" title="退市日期" :value="info.delist_date" />
  </van-cell-group>

  <van-cell-group v-if="holders.length" inset title="股东">
    <van-cell title="股东户数" :label="latestCount ? `统计日 ${latestCount.end_date} · 公告 ${latestCount.notice_date}` : ''">
      <template #value>
        {{ wan(latestCount?.holders) }}{{ latestCount?.approximate ? '（约）' : '' }}
        <span v-if="latestCount?.change !== null && latestCount?.change !== undefined" :style="{ color: color(latestCount.change) }">
          {{ pct(latestCount.change, 1, true) }}
        </span>
      </template>
    </van-cell>
    <van-cell v-if="latestStake" title="前十大股东 / 其余股东" :label="`报告期 ${latestStake.end_date}`"
              :value="`${latestStake.top10_pct!.toFixed(1)}% / ${(100 - latestStake.top10_pct!).toFixed(1)}%`" />
    <van-cell v-if="latestStake?.top10_free_pct !== null && latestStake?.top10_free_pct !== undefined" title="十大流通股东"
              :value="`${latestStake.top10_free_pct.toFixed(1)}%（占流通股）`" />
    <div v-if="recentCounts.length > 1" class="table-wrap" style="padding: 4px 16px 12px">
      <table class="table">
        <thead><tr><th>统计日</th><th>股东户数</th><th>较上期</th><th>公告日</th></tr></thead>
        <tbody>
          <tr v-for="r in recentCounts" :key="r.end_date">
            <td>{{ r.end_date }}</td>
            <td>{{ wan(r.holders) }}</td>
            <td :style="{ color: color(r.change) }">{{ pct(r.change, 1, true) }}</td>
            <td>{{ r.notice_date.slice(5) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </van-cell-group>

  <div v-if="signals?.run_id" class="card">
    <div class="card-title">策略得分（{{ signals.trade_date }} 调仓）</div>
    <template v-if="signals.row">
      <van-cell title="综合得分" :value="signals.row.score?.toFixed(3) ?? '—'" :border="false" />
      <van-cell title="股票池内排名" :value="signals.row.rank ? `${signals.row.rank} / ${signals.scored}` : '不在股票池'" :border="false" />
      <EChart v-if="families.length" :option="scoreOption" :height="Math.max(160, families.length * 26 + 30)" />
      <div class="muted">各类因子的截面标准分（类内等权平均）；正值表示在该类上更有利。</div>
    </template>
    <van-empty v-else image-size="60" description="该股票不在股票池内，也没有得分" />
    <template v-if="signals.history.length">
      <div class="card-title" style="margin-top: 12px">近期入选目标组合</div>
      <div v-for="h in signals.history.slice(0, 6)" :key="`${h.trade_date}-${h.account_id}`" class="muted" style="line-height: 20px">
        {{ h.trade_date }} · {{ h.account_id }} · 第 {{ h.rank }} 名 · {{ pct(h.target_weight) }}
      </div>
    </template>
  </div>

  <div v-if="fundamentals.length" class="card">
    <div class="card-title">财务（利润表，最近 8 期，累计值）</div>
    <div class="table-wrap">
      <table class="table">
        <thead><tr><th>报告期</th><th>营业总收入</th><th>归母净利润</th><th>扣非净利润</th><th>每股收益</th></tr></thead>
        <tbody>
          <tr v-for="r in fundamentals" :key="r.report_date">
            <td>{{ r.report_date }}</td>
            <td>{{ big(r.revenue ?? r.operate_income) }}</td>
            <td :style="{ color: (r.parent_net_profit ?? 0) < 0 ? DOWN : undefined }">{{ big(r.parent_net_profit) }}</td>
            <td :style="{ color: (r.deducted_parent_net_profit ?? 0) < 0 ? DOWN : undefined }">{{ big(r.deducted_parent_net_profit) }}</td>
            <td>{{ r.basic_eps ?? '—' }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <div class="muted" style="margin-top: 6px">按公告日可见；同一报告期取最新版本。</div>
  </div>
  <div style="height: 16px"></div>
</template>

<style scoped>
.quote { display: flex; gap: 8px; align-items: center; padding: 8px 12px; font-size: 13px; background: #fff; }
</style>
