<script lang="ts" setup>
import { computed, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';

import { AutoComplete, Button, Tag, Tooltip } from 'ant-design-vue';

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

/** The chart of ``code`` in this page, or in a new browser tab (Ctrl+Enter, the 新窗口 button). */
function open(code: string, newTab = false) {
  query.value = '';
  options.value = [];
  if (newTab) {
    const href = router.resolve({ path: '/chart', query: { symbol: code } }).href;
    window.open(href, '_blank', 'noopener');
    return;
  }
  router.push({ path: '/chart', query: { symbol: code } });
}

/** Enter picks the highlighted suggestion (AutoComplete); Ctrl+Enter opens it, or the typed code, in a new tab. */
function onKeydown(event: KeyboardEvent) {
  if (event.key !== 'Enter' || !(event.ctrlKey || event.metaKey)) return;
  event.preventDefault();
  event.stopPropagation();
  const typed = query.value.trim();
  const code = options.value[0]?.value ?? (/^(\d{6}|(sh|sz|bj)\d{6}|H\d{5})$/.test(typed) ? typed : '');
  if (code) open(code, true);
}

watch(symbol, async (code) => {
  localStorage.setItem(LAST_KEY, code);
  info.value = isIndexSymbol(code)
    ? (await indicesApi().catch(() => [])).find((i) => i.symbol === code)
    : await instrumentApi(code).catch(() => undefined);
  document.title = `${info.value?.name ?? code}（${code}）· 看盘`;
}, { immediate: true });
</script>

<template>
  <!-- No page header: the stock's name, tags and the search sit in the chart's first toolbar row. -->
  <Page auto-content-height content-class="p-2">
    <StockChart :key="'chart'" :symbol="symbol" height="100%">
      <template #header>
        <b class="text-base">{{ info?.name ?? symbol }}</b>
        <span class="text-muted-foreground">{{ symbol }}</span>
        <Tag v-if="info && isIndex" color="blue" class="mr-0">指数</Tag>
        <template v-else-if="info">
          <Tag class="mr-0">{{ BOARD[info.board] ?? info.board }}</Tag>
          <Tag v-if="info.industry" class="mr-0">{{ info.industry.l1_name }} / {{ info.industry.l2_name }}</Tag>
          <Tag v-if="info.risk_status !== 'normal'" color="error" class="mr-0">{{ info.risk_status }}</Tag>
        </template>
        <!-- Capture phase: the select handles Enter itself before a bubbling listener would see it. -->
        <Tooltip title="回车在本页打开，Ctrl+回车在新的浏览器标签打开">
          <span @keydown.capture="onKeydown">
            <AutoComplete v-model:value="query" :options="options" placeholder="代码或名称" size="small" style="width: 170px"
                          @search="search" @select="(v: any) => open(String(v))" />
          </span>
        </Tooltip>
        <Tooltip title="在新的浏览器标签打开这只股票">
          <Button size="small" @click="open(symbol, true)">新窗口</Button>
        </Tooltip>
        <Button v-if="!isIndex" size="small" @click="router.push(`/instruments/${symbol}`)">个股资料</Button>
        <span class="mx-1 h-4 border-l" />
      </template>
    </StockChart>
  </Page>
</template>
