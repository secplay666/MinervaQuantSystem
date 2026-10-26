<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';

import { request } from '../../api';
import { color, pct } from '../../format';
import { can } from '../../store';

const router = useRouter();
const query = ref('');
const results = ref<{ board: string; name: string; symbol: string }[]>([]);
const indices = ref<{ change_pct: null | number; close: number; name: string; symbol: string }[]>([]);
const session = ref('');
let timer: ReturnType<typeof setTimeout> | undefined;

const ENTRIES = [
  { icon: 'bar-chart-o', path: '/market/overview', text: '市场概览' },
  { icon: 'chart-trending-o', path: '/chart/sh000300', text: '看盘' },
  { icon: 'gold-coin-o', path: '/etf', text: 'ETF 资金' },
  { icon: 'location-o', path: '/moneymap', text: '钱去哪地图' },
  { icon: 'cluster-o', path: '/breadth', text: '市场宽度' },
  { icon: 'cash-back-record-o', path: '/company-actions', text: '回购增持' },
  { icon: 'fire-o', path: '/big-money', text: '盘中大资金' },
  { icon: 'balance-list-o', path: '/options', text: '期权' },
];

function onInput() {
  if (timer) clearTimeout(timer);
  timer = setTimeout(async () => {
    const q = query.value.trim();
    results.value = q ? await request(`/instruments/search?q=${encodeURIComponent(q)}`).catch(() => []) : [];
  }, 250);
}

onMounted(async () => {
  if (!can('market:view')) return;
  const overview = await request('/market/overview').catch(() => null);
  indices.value = overview?.indices ?? [];
  session.value = overview?.session ?? '';
});

/** Indices open their chart, stocks their page. */
function open(row: { board: string; symbol: string }) {
  router.push(row.board === 'INDEX' ? `/chart/${row.symbol}` : `/instruments/${row.symbol}`);
}
</script>

<template>
  <van-nav-bar title="行情" />
  <van-search v-model="query" placeholder="代码、名称或拼音首字母" @update:model-value="onInput" />
  <van-cell-group v-if="results.length" inset>
    <van-cell v-for="r in results" :key="r.symbol" :title="r.name" :label="r.symbol" is-link
              :value="r.board === 'INDEX' ? '指数' : ''" @click="open(r)" />
  </van-cell-group>
  <template v-else-if="can('market:view')">
    <van-grid :column-num="4" square clickable style="margin-top: 8px">
      <van-grid-item v-for="e in ENTRIES" :key="e.path" :icon="e.icon" :text="e.text" @click="router.push(e.path)" />
    </van-grid>
    <van-cell-group v-if="indices.length" inset :title="`主要指数（${session}，点开看 K 线）`">
      <van-grid :column-num="3" :border="false" clickable>
        <van-grid-item v-for="index in indices" :key="index.symbol" @click="router.push(`/chart/${index.symbol}`)">
          <div class="muted">{{ index.name }}</div>
          <div :style="{ color: color(index.change_pct), fontWeight: 600 }">{{ index.close.toFixed(2) }}</div>
          <div :style="{ color: color(index.change_pct), fontSize: '12px' }">{{ pct(index.change_pct, 2, true) }}</div>
        </van-grid-item>
      </van-grid>
    </van-cell-group>
    <div class="note">看盘：任何股票或指数都可以在上面搜索后打开；K 线页可选股东户数、大股东/小股东持股等副图。手机上不提供画线。</div>
  </template>
  <van-empty v-else description="没有行情权限" />
</template>
