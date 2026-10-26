<script setup lang="ts">
import type { PmCampaigns } from './pm-api';

import { computed, onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';

import { showFailToast } from 'vant';

import { request } from '../../api';
import { color, pct } from '../../format';
import { px } from './pm-api';
import PmTag from './PmTag.vue';

const PAGE = 30;
const router = useRouter();
const data = ref<PmCampaigns>();
const loading = ref(false);
const leverage = ref(1);
const limit = ref(PAGE);
const rows = computed(() => [...(data.value?.rows ?? [])].sort((a, b) =>
  (b.segments[0]?.entry_date ?? '').localeCompare(a.segments[0]?.entry_date ?? '')));
const scaled = (value: null | number | undefined) => (value === null || value === undefined ? null : value * leverage.value);

async function load() {
  loading.value = true;
  try {
    data.value = await request<PmCampaigns>('/pm/campaigns');
  } catch (error: any) {
    showFailToast(error.message);
  } finally {
    loading.value = false;
  }
}

onMounted(load);
</script>

<template>
  <van-nav-bar title="模拟战役总账" left-arrow @click-left="router.back()" />
  <van-pull-refresh v-model="loading" @refresh="load">
    <div class="note" style="margin-bottom: 0">假定每一轮都按规则执行（满仓或半仓入场、按档减仓、止盈清仓），用收盘价算的收益；不是你的真实账户</div>
    <div class="card">
      <van-grid :column-num="3" :border="false">
        <van-grid-item>
          <div class="muted">轮次（已结束 / 进行中）</div>
          <b style="font-size: 18px">{{ data?.totals.closed ?? 0 }} / {{ data?.totals.open ?? 0 }}</b>
        </van-grid-item>
        <van-grid-item>
          <div class="muted">胜率（已结束的）</div>
          <b style="font-size: 18px">{{ pct(data?.totals.win_rate, 0) }}</b>
        </van-grid-item>
        <van-grid-item>
          <div class="muted">平均每轮收益</div>
          <b :style="{ color: color(data?.totals.average), fontSize: '18px' }">{{ pct(scaled(data?.totals.average), 1, true) }}</b>
        </van-grid-item>
      </van-grid>
      <div style="display: flex; justify-content: space-between; align-items: center; margin-top: 8px">
        <span class="muted">杠杆系数（只影响显示）</span>
        <van-stepper v-model="leverage" :min="0.1" :max="5" :step="0.1" :decimal-length="1" input-width="48px" button-size="32px" />
      </div>
    </div>

    <van-skeleton v-if="!data" :row="8" />
    <van-empty v-else-if="!rows.length" description="还没有入过场的结构" />
    <div v-for="r in rows.slice(0, limit)" :key="`${r.item_id}-${r.round_no}`" class="card" style="font-size: 13px"
         @click="router.push(`/pm/items/${r.item_id}`)">
      <div style="display: flex; justify-content: space-between; align-items: flex-start">
        <div>
          <b>{{ r.name || r.symbol }}</b> <span class="muted">第 {{ r.round_no }} 轮</span>
          <div class="muted">{{ r.symbol }} <PmTag v-if="r.archived" style="margin-left: 4px">已归档</PmTag></div>
        </div>
        <div style="text-align: right">
          <b :style="{ color: color(r.round_return), fontSize: '15px' }">{{ pct(scaled(r.round_return), 1, true) }}</b>
          <PmTag v-if="r.open" color="blue" style="margin: 0 0 0 4px">进行中</PmTag>
          <div class="muted">颈线→目标 {{ pct(r.script_return, 1, true) }}</div>
        </div>
      </div>
      <div v-for="(seg, k) in r.segments" :key="k" style="margin-top: 6px; line-height: 18px">
        <span class="muted">{{ r.round_no }}-{{ k + 1 }}：</span>{{ seg.entry_date }} {{ px(seg.entry_close) }}（{{ pct(seg.entry_weight, 0) }}）
        → {{ seg.exit_date ? `${seg.exit_date} ${px(seg.exit_close)}` : `持有中 ${px(seg.exit_close)}` }}
        <span :style="{ color: color(seg.return) }">{{ pct(scaled(seg.return), 1, true) }}</span>
      </div>
    </div>
    <van-button v-if="rows.length > limit" block style="margin: 0 12px 12px; width: calc(100% - 24px)" @click="limit += PAGE">
      显示更多（还有 {{ rows.length - limit }} 轮）
    </van-button>
  </van-pull-refresh>
</template>
