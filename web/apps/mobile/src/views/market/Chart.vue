<script setup lang="ts">
import type { Bar, PmChart, SearchHit } from './market-api';

import { computed, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import { showFailToast, showSuccessToast } from 'vant';

import { post, request } from '../../api';
import KChart from '../../components/KChart.vue';
import { big, color, pct } from '../../format';
import { can } from '../../store';
import { barsApi, BOARD, indexBarsApi, isIndexSymbol, PM_LABEL, pmChartApi, searchApi } from './market-api';

const route = useRoute();
const router = useRouter();
const symbol = computed(() => String(route.params.symbol));
const isIndex = computed(() => isIndexSymbol(symbol.value));
const info = ref<Record<string, any>>();
const last = ref<Bar>();
const prev = ref<Bar>();
const pm = ref<null | PmChart>(null);
const adding = ref(false);
const canPm = computed(() => can('position:use'));

const change = computed(() => {
  const bar = last.value;
  if (!bar) return undefined;
  if (bar.pct_change !== null && bar.pct_change !== undefined) return bar.pct_change / 100;
  return prev.value ? bar.close / prev.value.close - 1 : undefined;
});
const pmLabel = computed(() => (pm.value?.item_id && pm.value.label ? PM_LABEL[pm.value.label] : undefined));

async function loadPm(code = symbol.value) {
  pm.value = canPm.value ? await pmChartApi(code).catch(() => null) : null;
}

async function addToLibrary() {
  adding.value = true;
  try {
    const out = await post<{ added: string[]; errors: string[]; skipped: string[] }>('/pm/items', { text: symbol.value });
    if (out.errors.length) showFailToast(out.errors.join('；'));
    else showSuccessToast('已加入标的库');
    await loadPm();
  } catch (error: any) {
    showFailToast(error.message);
  } finally {
    adding.value = false;
  }
}

watch(symbol, async (code) => {
  info.value = undefined;
  last.value = undefined;
  pm.value = null;
  void loadPm(code);
  try {
    const [detail, bars] = await Promise.all([
      isIndexSymbol(code)
        ? request<{ name: string; symbol: string }[]>('/market/indices').then((rows) => rows.find((i) => i.symbol === code)).catch(() => undefined)
        : request(`/instruments/${code}`).catch(() => undefined),
      isIndexSymbol(code) ? indexBarsApi(code, 2) : barsApi(code, 'none', 2, 'day'),
    ]);
    if (code !== symbol.value) return;
    info.value = detail;
    last.value = bars.at(-1);
    prev.value = bars.length > 1 ? bars.at(-2) : undefined;
  } catch (error: any) {
    showFailToast(error.message);
  }
}, { immediate: true });

// -- search ------------------------------------------------------------------------------------------------
const searchOpen = ref(false);
const query = ref('');
const hits = ref<SearchHit[]>([]);
let timer: ReturnType<typeof setTimeout> | undefined;
watch(query, (text) => {
  clearTimeout(timer);
  if (!text.trim()) {
    hits.value = [];
    return;
  }
  timer = setTimeout(async () => {
    try {
      hits.value = await searchApi(text.trim());
    } catch (error: any) {
      showFailToast(error.message);
    }
  }, 250);
});
function open(code: string) {
  searchOpen.value = false;
  query.value = '';
  if (code !== symbol.value) router.replace(`/chart/${code}`);
}
</script>

<template>
  <div class="chart-page">
    <van-nav-bar :title="info?.name ? `${info.name} ${symbol}` : symbol" left-arrow @click-left="router.back()">
      <template #right><van-icon name="search" size="20" @click="searchOpen = true" /></template>
    </van-nav-bar>

    <div class="quote">
      <template v-if="last">
        <b :style="{ color: color(change), fontSize: '20px' }">{{ last.close.toFixed(2) }}</b>
        <span :style="{ color: color(change) }">{{ pct(change, 2, true) }}</span>
        <span class="muted">{{ last.trade_date }}<template v-if="last.amount"> · 额 {{ big(last.amount) }}</template></span>
      </template>
      <span class="spacer"></span>
      <van-tag v-if="isIndex" type="primary" plain>指数</van-tag>
      <template v-else-if="info">
        <van-tag plain>{{ BOARD[info.board] ?? info.board }}</van-tag>
        <van-tag v-if="info.industry" plain>{{ info.industry.l1_name }}</van-tag>
        <van-tag v-if="info.risk_status && info.risk_status !== 'normal'" type="danger">{{ info.risk_status }}</van-tag>
      </template>
    </div>

    <div v-if="pm || !isIndex" class="actions">
      <template v-if="pm">
        <van-tag v-if="pmLabel" :type="pmLabel.type" size="medium">{{ pmLabel.name }}</van-tag>
        <span v-if="pm.stage" class="muted ellipsis">观点{{ pm.stage.name }}</span>
        <van-button v-if="pm.item_id" size="small" plain type="primary" @click="router.push(`/pm/items/${pm.item_id}`)">仓位管家</van-button>
        <van-button v-else size="small" plain type="primary" :loading="adding" @click="addToLibrary">加入标的库</van-button>
      </template>
      <span class="spacer"></span>
      <van-button v-if="!isIndex" size="small" plain @click="router.push(`/instruments/${symbol}`)">个股资料</van-button>
    </div>

    <div class="chart-wrap">
      <KChart :symbol="symbol" :structure="pm" height="100%" />
    </div>

    <van-popup v-model:show="searchOpen" position="top" :style="{ maxHeight: '75vh' }">
      <van-search v-model="query" placeholder="代码或名称（股票、指数）" autofocus show-action @cancel="searchOpen = false" />
      <van-cell v-for="hit in hits" :key="hit.symbol" :title="`${hit.symbol} ${hit.name}`" :value="BOARD[hit.board] ?? hit.board"
                is-link @click="open(hit.symbol)" />
      <van-empty v-if="query.trim() && !hits.length" image-size="60" description="没有匹配的证券" />
    </van-popup>
  </div>
</template>

<style scoped>
.chart-page { display: flex; flex-direction: column; height: 100vh; height: 100dvh; padding-bottom: env(safe-area-inset-bottom); box-sizing: border-box; background: #fff; }
.quote, .actions { display: flex; flex: none; flex-wrap: wrap; gap: 4px 6px; align-items: center; padding: 6px 12px 0; font-size: 13px; }
.quote :deep(.van-tag) { white-space: nowrap; }
.actions { padding-bottom: 4px; }
.spacer { flex: 1; }
.ellipsis { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.chart-wrap { flex: 1; min-height: 0; }
.chart-wrap > * { height: 100%; }
</style>
