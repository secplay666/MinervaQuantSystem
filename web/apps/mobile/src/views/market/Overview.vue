<script setup lang="ts">
import type { EChartsOption } from 'echarts';

import type { Overview, SearchHit } from './market-api';

import { computed, onMounted, ref, watch } from 'vue';
import { useRouter } from 'vue-router';

import { showFailToast } from 'vant';

import { request } from '../../api';
import EChart from '../../components/EChart.vue';
import KChart from '../../components/KChart.vue';
import { big, color, DOWN, pct, UP } from '../../format';
import { BOARD, searchApi } from './market-api';

const router = useRouter();
const overview = ref<Overview>();
const loading = ref(false);
const indexSymbol = ref('sh000300');

async function load() {
  loading.value = true;
  try {
    overview.value = await request<Overview>('/market/overview');
  } catch (error: any) {
    showFailToast(error.message);
  } finally {
    loading.value = false;
  }
}

const sectorOption = computed<EChartsOption>(() => {
  const rows = [...(overview.value?.industries ?? [])].reverse();
  return {
    grid: { bottom: 20, containLabel: true, left: 4, right: 44, top: 4 },
    series: [{
      data: rows.map((r) => ({ itemStyle: { color: r.change_pct >= 0 ? UP : DOWN }, value: +(r.change_pct * 100).toFixed(2) })),
      label: { fontSize: 10, formatter: '{c}%', position: 'right', show: true },
      type: 'bar',
    }],
    tooltip: { formatter: (p: any) => `${p.name}：${p.value}%（${rows[p.dataIndex]?.count} 只）`, trigger: 'item' },
    xAxis: { axisLabel: { formatter: '{value}%' }, type: 'value' },
    yAxis: { axisLabel: { fontSize: 11 }, data: rows.map((r) => r.name), type: 'category' },
  };
});

// -- search --------------------------------------------------------------------------------------------------
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
function open(hit: SearchHit) {
  query.value = '';
  router.push(hit.board === 'INDEX' ? `/chart/${hit.symbol}` : `/instruments/${hit.symbol}`);
}

onMounted(load);
</script>

<template>
  <van-nav-bar title="市场" left-arrow @click-left="router.back()" />
  <van-search v-model="query" placeholder="输入代码或名称查看个股" />
  <van-cell-group v-if="hits.length" inset>
    <van-cell v-for="hit in hits" :key="hit.symbol" :title="`${hit.symbol} ${hit.name}`" :value="BOARD[hit.board] ?? hit.board"
              is-link @click="open(hit)" />
  </van-cell-group>

  <van-pull-refresh v-model="loading" @refresh="load">
    <template v-if="overview">
      <p class="note" style="margin-bottom: 0">交易日 {{ overview.session }}（对比 {{ overview.previous_session ?? '—' }}）</p>
      <van-grid :column-num="3" :border="false" class="indices">
        <van-grid-item v-for="index in overview.indices" :key="index.symbol" @click="router.push(`/chart/${index.symbol}`)">
          <div class="muted">{{ index.name }}</div>
          <div :style="{ color: color(index.change_pct), fontWeight: 600 }">{{ index.close.toFixed(2) }}</div>
          <div :style="{ color: color(index.change_pct), fontSize: '12px' }">{{ pct(index.change_pct, 2, true) }}</div>
        </van-grid-item>
      </van-grid>
      <div class="card">
        <div class="card-title">涨跌分布</div>
        <div class="tags">
          <van-tag type="danger" size="medium">上涨 {{ overview.breadth.up }}</van-tag>
          <van-tag type="success" size="medium">下跌 {{ overview.breadth.down }}</van-tag>
          <van-tag size="medium">平盘 {{ overview.breadth.flat }}</van-tag>
          <van-tag type="danger" plain size="medium">涨停 {{ overview.breadth.limit_up }}</van-tag>
          <van-tag type="success" plain size="medium">跌停 {{ overview.breadth.limit_down }}</van-tag>
        </div>
        <div class="muted" style="margin-top: 8px">全市场成交额 {{ big(overview.breadth.amount_cny) }}元，{{ overview.breadth.traded }} 只有成交</div>
      </div>
    </template>
    <van-skeleton v-else-if="loading" :row="6" style="margin-top: 12px" />
  </van-pull-refresh>

  <template v-if="overview">
    <div class="card" style="padding: 0; overflow: hidden">
      <div class="kline-head">
        <span class="card-title" style="margin: 0">指数 K 线</span>
        <van-button size="small" type="primary" plain @click="router.push(`/chart/${indexSymbol}`)">大图看盘</van-button>
      </div>
      <van-tabs v-model:active="indexSymbol" shrink line-height="2">
        <van-tab v-for="index in overview.indices" :key="index.symbol" :name="index.symbol" :title="index.name" />
      </van-tabs>
      <KChart :key="indexSymbol" :symbol="indexSymbol" :height="320" compact />
    </div>
    <div v-if="overview.industries.length" class="card">
      <div class="card-title">申万一级行业涨跌（等权平均）</div>
      <EChart :option="sectorOption" :height="overview.industries.length * 20 + 40" />
    </div>
  </template>
</template>

<style scoped>
.indices { margin: 12px; overflow: hidden; border-radius: 8px; }
.indices :deep(.van-grid-item__content) { padding: 10px 4px; }
.tags { display: flex; flex-wrap: wrap; gap: 6px; }
.kline-head { display: flex; align-items: center; justify-content: space-between; padding: 12px 12px 0; }
</style>
