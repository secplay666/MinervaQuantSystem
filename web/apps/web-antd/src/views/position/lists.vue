<script lang="ts" setup>
import type { PmRow } from '#/api';

import { computed, onMounted, ref } from 'vue';

import { Page } from '@vben/common-ui';

import { Button, Card, Segmented } from 'ant-design-vue';

import { pmItemsApi } from '#/api';

import ItemTable from './item-table.vue';

const props = defineProps<{ kind: 'breakouts' | 'tops' }>();
const rows = ref<PmRow[]>([]);
const loading = ref(false);
const range = ref<number | string>(20);

const TEXT = {
  breakouts: { description: '收盘站上颈线、结构已激活的标的，按激活日从近到远', title: '突破确立' },
  tops: { description: '标为顶部、或完成度已进入量度满足区（默认 90%）仍有剧本仓位的标的', title: '头部确立' },
};

function sessionsAgo(row: PmRow): number {
  // Calendar approximation for the filter (weekends excluded), enough to pick recent breakouts.
  if (!row.activated_on || !row.latest) return Infinity;
  const days = (Date.parse(row.latest) - Date.parse(row.activated_on)) / 86_400_000;
  return Math.round((days * 5) / 7);
}

const shown = computed(() => {
  if (props.kind === 'tops') {
    return rows.value.filter((r) => r.label === 'top' || r.top_watch)
      .sort((a, b) => (b.completion ?? 0) - (a.completion ?? 0));
  }
  const limit = range.value === 'all' ? Infinity : Number(range.value);
  return rows.value.filter((r) => r.activated_on && r.phase !== 'exhausted' && sessionsAgo(r) <= limit)
    .sort((a, b) => (b.activated_on ?? '').localeCompare(a.activated_on ?? ''));
});

async function load() {
  loading.value = true;
  try {
    rows.value = (await pmItemsApi()).items;
  } finally {
    loading.value = false;
  }
}

onMounted(load);
</script>

<template>
  <Page :title="TEXT[kind].title" :description="TEXT[kind].description">
    <template #extra><Button :loading="loading" @click="load">刷新</Button></template>
    <Card size="small">
      <template v-if="kind === 'breakouts'" #title>
        <Segmented v-model:value="range" :options="[{ label: '近 5 日', value: 5 }, { label: '近 20 日', value: 20 },
                                                     { label: '近 60 日', value: 60 }, { label: '全部', value: 'all' }]" />
      </template>
      <ItemTable :loading="loading" :rows="shown" />
    </Card>
  </Page>
</template>
