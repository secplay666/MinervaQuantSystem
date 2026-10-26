<script setup lang="ts">
import type { PmBreakoutRow, PmBreakouts, PmTopRow, PmTops } from './pm-api';

import { computed, onMounted, ref, watch } from 'vue';
import { useRouter } from 'vue-router';

import { showFailToast } from 'vant';

import { request } from '../../api';
import { color, pct } from '../../format';
import { GRADE_COLOR, labelColor, labelName, progress, px } from './pm-api';
import PmTag from './PmTag.vue';

const props = defineProps<{ kind: 'breakouts' | 'tops' }>();
const router = useRouter();
const loading = ref(false);
const breakouts = ref<PmBreakouts>();
const tops = ref<PmTops>();
const TEXT = {
  breakouts: { description: '收盘站上颈线、结构已激活的标的：已共振的已入场，待共振的在等主指数', title: '突破确立' },
  tops: { description: '量度满足区里留意头部、挂着头部颈线观察、已确认头部的标的；已确立的记下确认后的走势，作为看顶准确率', title: '头部确立' },
};
const BREAKOUT_GROUPS = [['resonant', '已共振（已入场）', '没有已入场的突破'], ['waiting', '待共振（已突破，等主指数）', '没有在等主指数的突破']] as const;
const TOP_GROUPS = [['watching', '留意头部'], ['observing', '观察中（已挂头部颈线）'], ['confirmed', '已确立']] as const;
const VERDICT: Record<string, string> = { 中性: 'default', 判早: 'orange', 判对: 'green' };
const loaded = computed(() => (props.kind === 'breakouts' ? breakouts.value : tops.value) !== undefined);

async function load() {
  loading.value = true;
  try {
    if (props.kind === 'breakouts') breakouts.value = await request<PmBreakouts>('/pm/lists/breakouts');
    else tops.value = await request<PmTops>('/pm/lists/tops');
  } catch (error: any) {
    showFailToast(error.message);
  } finally {
    loading.value = false;
  }
}

function topDetail(row: PmTopRow): string {
  if (row.state === 'observing') return `头部颈线 ${px(row.top_neckline)}，距离 ${pct(row.distance, 1, true)}；收盘跌破即清仓`;
  if (row.state === 'watching') return row.label === 'top' ? '标签为顶部：只做退出' : '完成度已进入量度满足区，留意头部形态';
  return `${row.confirmed_on} ${row.how}，当时 ${px(row.confirmed_close)}，之后 ${pct(row.change, 1, true)}`;
}

const open = (row: { id: number }) => router.push(`/pm/items/${row.id}`);
const rowsOf = (key: 'resonant' | 'waiting'): PmBreakoutRow[] => breakouts.value?.[key] ?? [];

watch(() => props.kind, load);
onMounted(load);
</script>

<template>
  <van-nav-bar :title="TEXT[kind].title" left-arrow @click-left="router.back()" />
  <van-pull-refresh v-model="loading" @refresh="load">
    <div class="note" style="margin-bottom: 0">{{ TEXT[kind].description }}</div>
    <van-skeleton v-if="!loaded" :row="8" style="margin-top: 16px" />

    <template v-if="kind === 'breakouts' && breakouts">
      <template v-for="[key, title, empty] in BREAKOUT_GROUPS" :key="key">
        <div class="pm-group">{{ title }} {{ rowsOf(key).length }}</div>
        <div v-if="!rowsOf(key).length" class="card muted">{{ empty }}</div>
        <div v-for="row in rowsOf(key)" :key="row.id" class="card pm-list-card" @click="open(row)">
          <div class="pm-list-head">
            <div>
              <b>{{ row.name || row.symbol }}</b>
              <PmTag v-if="row.quality?.grade" :color="GRADE_COLOR[row.quality.grade]" style="margin-left: 6px">{{ row.quality.grade }}</PmTag>
              <div class="muted">{{ row.symbol }} · 突破日 {{ row.activated_on ?? '—' }}</div>
            </div>
            <div style="text-align: right">
              <div :style="{ color: color(row.change), fontWeight: 600 }">{{ px(row.close) }}</div>
              <div :style="{ color: color(row.change), fontSize: '12px' }">{{ pct(row.change, 2, true) }}</div>
            </div>
          </div>
          <div class="pm-list-grid">
            <div><span class="muted">突破价</span> {{ px(row.breakout_close) }}</div>
            <div><span class="muted">距突破位</span> <span :style="{ color: color(row.from_breakout) }">{{ pct(row.from_breakout, 1, true) }}</span></div>
            <div><span class="muted">剧本收益</span> {{ pct(row.script_return, 1, true) }}</div>
            <div><span class="muted">主指数</span> {{ row.main_index_name ?? row.main_index ?? '—' }}
              <PmTag v-if="row.main_index_label" :color="labelColor(row.main_index_label)">{{ labelName(row.main_index_label) }}</PmTag></div>
          </div>
          <div class="pm-list-progress">
            <van-progress :percentage="progress(row.completion)" :show-pivot="false" stroke-width="6" style="flex: 1" />
            <span>完成度 {{ pct(row.completion, 0) }}</span>
          </div>
          <div v-if="row.half_entry" style="color: #d46b08; font-size: 12px">指数晚期·半仓入场</div>
          <div class="muted" style="margin-top: 4px; line-height: 18px">{{ row.waiting_for }}</div>
          <div class="pm-list-link" @click.stop="router.push(`/chart/${row.symbol}`)">K 线 ›</div>
        </div>
      </template>

      <div class="pm-group">近 7 天移出 {{ breakouts.removed.length }}</div>
      <div class="card" style="padding-top: 4px; padding-bottom: 4px">
        <div v-if="!breakouts.removed.length" class="muted" style="padding: 8px 0">近 7 天没有移出的</div>
        <div v-for="row in breakouts.removed" :key="`${row.id}-${row.removed_on}`" class="pm-removed" @click="open(row)">
          <div><b>{{ row.name || row.symbol }}</b> <span class="muted">{{ row.symbol }}</span></div>
          <div style="text-align: right"><div>{{ row.reason }}</div><div class="muted">{{ row.removed_on }}</div></div>
        </div>
      </div>
    </template>

    <template v-if="kind === 'tops' && tops">
      <div class="card">
        <div class="card-title">看顶准确率（已确立的，确认后涨跌带 ±1% 中性带）</div>
        <van-grid :column-num="3" :border="false">
          <van-grid-item><div class="muted">判对</div><b style="font-size: 20px">{{ tops.accuracy.right }}</b><div class="muted">之后跌超 1%</div></van-grid-item>
          <van-grid-item><div class="muted">判早</div><b style="font-size: 20px">{{ tops.accuracy.early }}</b><div class="muted">之后涨超 1%</div></van-grid-item>
          <van-grid-item><div class="muted">中性</div><b style="font-size: 20px">{{ tops.accuracy.neutral }}</b><div class="muted">±1% 以内</div></van-grid-item>
        </van-grid>
      </div>
      <template v-for="[key, title] in TOP_GROUPS" :key="key">
        <div class="pm-group">{{ title }} {{ tops[key].length }}</div>
        <div v-if="!tops[key].length" class="card muted">没有</div>
        <div v-for="row in tops[key]" :key="row.id" class="card pm-list-card" @click="open(row)">
          <div class="pm-list-head">
            <div><b>{{ row.name || row.symbol }}</b><div class="muted">{{ row.symbol }}</div></div>
            <div style="text-align: right; font-weight: 600">{{ px(row.close) }}</div>
          </div>
          <div v-if="row.completion !== null" class="pm-list-progress">
            <van-progress :percentage="progress(row.completion)" :show-pivot="false" stroke-width="6" style="flex: 1" />
            <span>完成度 {{ pct(row.completion, 0) }}</span>
          </div>
          <div style="margin-top: 4px; line-height: 18px">
            {{ topDetail(row) }}
            <PmTag v-if="row.verdict" :color="VERDICT[row.verdict] ?? 'default'" style="margin-left: 4px">{{ row.verdict }}</PmTag>
          </div>
        </div>
      </template>
    </template>
  </van-pull-refresh>
</template>

<style>
.pm-group { margin: 16px 16px 0; color: #646566; font-size: 14px; font-weight: 600; }
.pm-list-card { font-size: 13px; }
.pm-list-head { display: flex; justify-content: space-between; align-items: flex-start; }
.pm-list-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 4px 12px; margin-top: 8px; }
.pm-list-progress { display: flex; align-items: center; gap: 8px; margin-top: 8px; font-size: 12px; }
.pm-list-link { margin: 4px -12px -12px; padding: 10px 12px; color: #1677ff; text-align: right; border-top: 1px solid #f5f6f8; }
.pm-removed { display: flex; justify-content: space-between; align-items: center; min-height: 44px; border-bottom: 1px solid #f5f6f8; font-size: 13px; }
.pm-removed:last-child { border-bottom: none; }
</style>
