<script setup lang="ts">
import type { PmBoard, PmIndexBand, PmPool, PmSignal, PmTierItem } from './pm-api';

import { computed, onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';

import { showConfirmDialog, showDialog, showFailToast } from 'vant';

import { post, request } from '../../api';
import { color, pct } from '../../format';
import ItemCard from './ItemCard.vue';
import { GRADE_COLOR, labelColor, labelName, PM_LABELS, POOL_NAMES, POOLS, PRIORITY } from './pm-api';
import PmTag from './PmTag.vue';

const router = useRouter();
const board = ref<PmBoard>();
const loading = ref(false);
const pool = ref<'all' | PmPool>('all');
const allSignals = ref(false);
const restOpen = ref<string[]>([]);
const TIER_COLOR: Record<number, string> = { 1: 'red', 2: 'orange', 3: 'gold', 4: 'blue', 5: 'default' };
const ENTRIES = [
  { icon: 'bookmark-o', path: '/pm/library', text: '标的库' },
  { icon: 'upgrade', path: '/pm/breakouts', text: '突破确立' },
  { icon: 'warning-o', path: '/pm/tops', text: '头部确立' },
  { icon: 'balance-list-o', path: '/pm/campaigns', text: '战役总账' },
  { icon: 'bar-chart-o', path: '/pm/backtest', text: '阶段回测' },
  { icon: 'setting-o', path: '/pm/settings', text: '规则设置' },
];

const tiers = computed(() => (board.value?.tiers ?? []).filter((t) => t.tier < 5));
const rest = computed(() => board.value?.tiers.find((t) => t.tier === 5)?.items ?? []);
const quiet = computed(() => tiers.value.every((t) => !t.items.length));
const signals = computed(() => (allSignals.value ? board.value?.signals : board.value?.signals.slice(0, 8)) ?? []);
const rows = computed(() => {
  const items = board.value?.items ?? [];
  // Most urgent first: structures in progress, then by completion.
  const sorted = [...items].sort((a, b) => Number(b.phase === 'active' || b.phase === 'realized')
    - Number(a.phase === 'active' || a.phase === 'realized') || (b.completion ?? -1) - (a.completion ?? -1));
  return pool.value === 'all' ? sorted : sorted.filter((r) => r.pool === pool.value);
});

async function load() {
  loading.value = true;
  try {
    board.value = await request<PmBoard>('/pm/board');
  } catch (error: any) {
    showFailToast(error.message);
  } finally {
    loading.value = false;
  }
}

async function read(signal?: PmSignal) {
  try {
    const out = await post<{ marked: number; unread: number }>('/pm/signals/read', { ids: signal ? [signal.id] : null });
    if (!board.value) return;
    board.value.unread = out.unread;
    for (const s of board.value.signals) if (!signal || s.id === signal.id) s.read = true;
  } catch (error: any) {
    showFailToast(error.message);
  }
}

function open(signal: PmSignal) {
  if (!signal.read) void read(signal);
  router.push(`/pm/items/${signal.item_id}`);
}

function detailOf(item: PmTierItem, tier: number): string {
  if (tier === 1) return (item.messages ?? []).join('；');
  if (tier === 4) return `完成度 ${pct(item.completion_change, 0, true)}（一天内）· ${item.waiting_for}`;
  return `距触发 ${pct(item.distance, 1)} · ${item.waiting_for}`;
}

/** The PC tooltip of an index: whose label it is and why; an index in the library opens. */
function explainIndex(index: PmIndexBand) {
  const message = index.stage ? `${index.label_source === 'manual' ? '你的标签' : '系统观点'}：${index.stage.reason}`
    : `${labelName(index.label)}（${index.label_source === 'manual' ? '你的标签' : '系统观点'}）`;
  if (index.item_id) {
    showConfirmDialog({ cancelButtonText: '关闭', confirmButtonText: '查看结构', message, messageAlign: 'left', title: index.name })
      .then(() => router.push(`/pm/items/${index.item_id}`)).catch(() => undefined);
  } else {
    showDialog({ message, messageAlign: 'left', title: index.name });
  }
}

onMounted(load);
</script>

<template>
  <van-nav-bar title="仓位管家" />
  <van-pull-refresh v-model="loading" @refresh="load">
    <van-grid :column-num="6" :border="false" icon-size="22" class="pm-entries">
      <van-grid-item v-for="e in ENTRIES" :key="e.path" :icon="e.icon" :text="e.text" @click="router.push(e.path)" />
    </van-grid>
    <div class="note" style="margin-bottom: 0">
      {{ board?.latest ? `截至 ${board.latest} 收盘；` : '' }}标签由你确认，系统只给观点和剧本仓位
    </div>

    <van-skeleton v-if="!board" :row="8" style="margin-top: 16px" />
    <template v-else>
      <div v-if="board.indices.length" class="pm-indices">
        <div v-for="index in board.indices" :key="index.symbol" class="pm-index" @click="explainIndex(index)">
          <div class="muted">{{ index.name }}</div>
          <div :style="{ color: color(index.change), fontWeight: 600 }">{{ index.close.toFixed(2) }}</div>
          <div :style="{ color: color(index.change), fontSize: '12px' }">{{ pct(index.change, 2, true) }}</div>
          <PmTag :color="labelColor(index.label)" style="margin: 2px 0 0">
            {{ labelName(index.label) }}{{ index.label_source === 'system' ? '·观点' : '' }}
          </PmTag>
        </div>
      </div>

      <div class="card">
        <div class="card-title">操作提示（日报五档）</div>
        <div v-if="quiet" class="muted" style="padding: 4px 0">今日无操作提示 · 系统盯盘中</div>
        <template v-for="tier in tiers" :key="tier.tier">
          <div v-if="tier.items.length" style="margin-bottom: 8px">
            <div style="margin: 4px 0"><PmTag :color="TIER_COLOR[tier.tier]">{{ tier.tier }} {{ tier.name }}</PmTag>
              <span class="muted">{{ tier.items.length }} 只</span></div>
            <div v-for="item in tier.items" :key="item.id" class="pm-tier-row" @click="router.push(`/pm/items/${item.id}`)">
              <div>
                <b>{{ item.name || item.symbol }}</b>
                <span v-if="item.star" style="margin: 0 4px; color: #f59e0b">{{ '★'.repeat(item.star) }}</span>
                <PmTag :color="labelColor(item.label)" style="margin-left: 4px">{{ labelName(item.label) }}</PmTag>
                <PmTag v-if="item.priority" :color="PRIORITY[item.priority]?.color">{{ PRIORITY[item.priority]?.name }}</PmTag>
                <PmTag v-if="item.quality?.grade" :color="GRADE_COLOR[item.quality.grade]">{{ item.quality.grade }}</PmTag>
              </div>
              <div class="muted" style="line-height: 18px">{{ detailOf(item, tier.tier) }}</div>
            </div>
          </div>
        </template>
        <van-collapse v-if="rest.length" v-model="restOpen" :border="false">
          <van-collapse-item :title="`5 其余 ${rest.length} 只`" name="rest" :border="false" style="margin: 0 -12px">
            <span v-for="item in rest" :key="item.id" class="pm-rest" @click="router.push(`/pm/items/${item.id}`)">
              {{ item.name || item.symbol }}
            </span>
          </van-collapse-item>
        </van-collapse>
      </div>

      <div class="card">
        <div class="card-title" style="display: flex; justify-content: space-between; align-items: center">
          <span>提示 <van-badge v-if="board.unread" :content="board.unread" style="margin-left: 4px" /></span>
          <van-button v-if="board.unread" size="small" plain type="primary" @click="read()">全部已读</van-button>
        </div>
        <div v-if="!board.signals.length" class="muted">还没有提示：结构确认后，规则触发时会出现在这里</div>
        <div v-for="s in signals" :key="s.id" class="pm-signal" :style="{ opacity: s.read ? 0.6 : 1 }" @click="open(s)">
          <div style="display: flex; justify-content: space-between; align-items: center">
            <span :style="{ fontWeight: s.read ? 400 : 600 }">
              <van-badge v-if="!s.read" dot style="margin-right: 6px" />{{ s.name || s.symbol }}
            </span>
            <PmTag :color="PRIORITY[s.priority]?.color" style="margin: 0">{{ s.priority_name }}</PmTag>
          </div>
          <div class="muted" style="line-height: 18px">{{ s.trade_date }} · {{ s.message }}</div>
        </div>
        <van-button v-if="board.signals.length > 8" block size="small" style="margin-top: 8px; border: none" @click="allSignals = !allSignals">
          {{ allSignals ? '收起' : `显示全部 ${board.signals.length} 条` }}
        </van-button>
      </div>

      <div class="card" style="padding-bottom: 4px">
        <div class="card-title">态势</div>
        <PmTag v-for="l in PM_LABELS" :key="l.key" :color="l.color">{{ l.name }} {{ board.counts[l.key] ?? 0 }}</PmTag>
      </div>
      <van-tabs v-model:active="pool" shrink style="margin: 0 12px">
        <van-tab :title="`全部 ${board.items.length}`" name="all" />
        <van-tab v-for="key in POOLS" :key="key" :title="`${POOL_NAMES[key]} ${board.pools[key] ?? 0}`" :name="key" />
      </van-tabs>
      <van-empty v-if="!board.items.length" description="标的库是空的：在“标的库”里粘贴代码添加">
        <van-button type="primary" size="small" @click="router.push('/pm/library')">去添加</van-button>
      </van-empty>
      <van-empty v-else-if="!rows.length" description="这个池子里没有标的" />
      <ItemCard v-for="row in rows" :key="row.id" :row="row" />
    </template>
  </van-pull-refresh>
</template>

<style>
.pm-entries .van-grid-item__content { padding: 12px 2px; }
.pm-entries .van-grid-item__text { font-size: 12px; white-space: nowrap; }
.pm-indices { display: flex; gap: 8px; margin: 12px 12px 0; overflow-x: auto; -webkit-overflow-scrolling: touch; }
.pm-index { flex: 0 0 auto; min-width: 84px; padding: 8px 10px; background: #fff; border-radius: 8px; }
.pm-tier-row { padding: 8px 0 8px 8px; border-bottom: 1px solid #f5f6f8; font-size: 13px; }
.pm-rest { display: inline-block; padding: 8px 12px 8px 0; color: #1677ff; font-size: 13px; }
.pm-signal { padding: 8px 0; border-bottom: 1px solid #f5f6f8; font-size: 13px; }
</style>
