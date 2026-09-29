<script lang="ts" setup>
import { computed, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';

import { AutoComplete, Button, Tag } from 'ant-design-vue';

import { indicesApi, instrumentApi, isIndexSymbol, searchApi } from '#/api';
import StockChart from '#/components/stock-chart/stock-chart.vue';

const LAST_KEY = 'minerva.chart.last';
const route = useRoute();
const router = useRouter();
const symbol = computed(() => String(route.query.symbol || localStorage.getItem(LAST_KEY) || '600000'));
const info = ref<Record<string, any>>();
const isIndex = computed(() => isIndexSymbol(symbol.value));
const query = ref('');
const options = ref<{ label: string; value: string }[]>([]);

const BOARD: Record<string, string> = { BSE: '北交所', CHINEXT: '创业板', SSE_MAIN: '沪市主板', STAR: '科创板', SZSE_MAIN: '深市主板' };

async function search(q: string) {
  options.value = q.trim()
    ? (await searchApi(q.trim())).map((r) => ({ label: `${r.symbol} ${r.name}${r.board === 'INDEX' ? '（指数）' : ''}`,
                                               value: r.symbol }))
    : [];
}

function open(code: string) {
  query.value = '';
  options.value = [];
  router.push({ path: '/chart', query: { symbol: code } });
}

watch(symbol, async (code) => {
  localStorage.setItem(LAST_KEY, code);
  info.value = isIndexSymbol(code)
    ? (await indicesApi().catch(() => [])).find((i) => i.symbol === code)
    : await instrumentApi(code).catch(() => undefined);
}, { immediate: true });
</script>

<template>
  <Page auto-content-height :title="info ? `${info.name}（${symbol}）` : symbol">
    <template #description>
      <span v-if="info && isIndex"><Tag color="blue">指数</Tag></span>
      <span v-else-if="info">
        <Tag>{{ BOARD[info.board] ?? info.board }}</Tag>
        <Tag v-if="info.industry">{{ info.industry.l1_name }} / {{ info.industry.l2_name }}</Tag>
        <Tag v-if="info.risk_status !== 'normal'" color="error">{{ info.risk_status }}</Tag>
      </span>
    </template>
    <template #extra>
      <AutoComplete v-model:value="query" :options="options" placeholder="代码或名称，如 600000、上证" style="width: 240px"
                    @search="search" @select="(v: any) => open(String(v))" />
      <Button v-if="!isIndex" class="ml-2" @click="router.push(`/instruments/${symbol}`)">个股资料</Button>
    </template>
    <StockChart :key="'chart'" :symbol="symbol" height="calc(100vh - 190px)" />
  </Page>
</template>
