<script lang="ts" setup>
import type { PmBoard, PmPool, PmSignal, PmTierItem } from '#/api';

import { computed, onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';

import { Badge, Button, Card, Col, Collapse, Empty, List, Row, Segmented, Space, Tag, Tooltip } from 'ant-design-vue';

import { GRADE_COLOR, PM_LABELS, pmBoardApi, pmReadSignalsApi, POOL_NAMES, PRIORITY } from '#/api';
import { changeColor, pct } from '#/utils/format';

import { labelColor, labelName } from './common';
import ItemTable from './item-table.vue';

const router = useRouter();
const board = ref<PmBoard>();
const loading = ref(false);
const pool = ref<'all' | PmPool>('all');
const TIER_COLOR: Record<number, string> = { 1: 'red', 2: 'orange', 3: 'gold', 4: 'blue', 5: 'default' };

const rows = computed(() => {
  const items = board.value?.items ?? [];
  // Most urgent first: structures in progress, then by completion.
  const sorted = [...items].sort((a, b) => Number(b.phase === 'active' || b.phase === 'realized') -
    Number(a.phase === 'active' || a.phase === 'realized') || (b.completion ?? -1) - (a.completion ?? -1));
  return pool.value === 'all' ? sorted : sorted.filter((r) => r.pool === pool.value);
});
const poolOptions = computed(() => [
  { label: `全部 ${board.value?.items.length ?? 0}`, value: 'all' },
  ...(['hold', 'ready', 'buyback', 'watch'] as const).map((key) => ({
    label: `${POOL_NAMES[key]} ${board.value?.pools[key] ?? 0}`, value: key })),
]);
const tiers = computed(() => (board.value?.tiers ?? []).filter((t) => t.tier < 5));
const rest = computed(() => board.value?.tiers.find((t) => t.tier === 5)?.items ?? []);
const quiet = computed(() => tiers.value.every((t) => !t.items.length));

async function load() {
  loading.value = true;
  try {
    board.value = await pmBoardApi();
  } finally {
    loading.value = false;
  }
}

async function read(signal?: PmSignal) {
  const out = await pmReadSignalsApi(signal ? [signal.id] : undefined);
  if (!board.value) return;
  board.value.unread = out.unread;
  for (const s of board.value.signals) if (!signal || s.id === signal.id) s.read = true;
}

function open(signal: PmSignal) {
  if (!signal.read) void read(signal);
  router.push(`/position/items/${signal.item_id}`);
}

function detailOf(item: PmTierItem, tier: number): string {
  if (tier === 1) return (item.messages ?? []).join('；');
  if (tier === 4) return `完成度 ${pct(item.completion_change, 0, true)}（一天内）· ${item.waiting_for}`;
  return `距触发 ${pct(item.distance, 1)} · ${item.waiting_for}`;
}

onMounted(load);
</script>

<template>
  <Page title="仓位管家" :description="board?.latest ? `截至 ${board.latest} 收盘；标签由你确认，系统只给观点和剧本仓位` : '标签由你确认，系统只给观点和剧本仓位'">
    <template #extra>
      <Space>
        <Button @click="router.push('/position/library')">管理标的库</Button>
        <Button :loading="loading" @click="load">刷新</Button>
      </Space>
    </template>

    <Row :gutter="[12, 12]">
      <Col v-for="index in board?.indices ?? []" :key="index.symbol" :lg="4" :md="8" :xs="12">
        <Card size="small" class="h-full">
          <div class="flex items-center justify-between">
            <span class="font-medium">{{ index.name }}</span>
            <Tooltip :title="index.stage ? `${index.label_source === 'manual' ? '你的标签' : '系统观点'}：${index.stage.reason}` : undefined">
              <Tag :color="labelColor(index.label)" class="!mr-0">
                {{ labelName(index.label) }}{{ index.label_source === 'system' ? '·观点' : '' }}
              </Tag>
            </Tooltip>
          </div>
          <div class="mt-1 text-lg">{{ index.close.toFixed(2) }}
            <span class="text-sm" :style="{ color: changeColor(index.change) }">{{ pct(index.change, 2, true) }}</span>
          </div>
        </Card>
      </Col>
    </Row>

    <Row :gutter="12" class="mt-3">
      <Col :lg="17" :xs="24">
        <Card size="small" title="操作提示（日报五档）">
          <div v-if="quiet" class="py-2 text-gray-400">今日无操作提示 · 系统盯盘中</div>
          <div v-for="tier in tiers" v-show="tier.items.length" :key="tier.tier" class="mb-2">
            <div class="mb-1 text-sm font-medium"><Tag :color="TIER_COLOR[tier.tier]">{{ tier.tier }} {{ tier.name }}</Tag>{{ tier.items.length }} 只</div>
            <div v-for="item in tier.items" :key="item.id" class="flex cursor-pointer items-baseline gap-2 py-0.5 pl-2 hover:bg-gray-500/10"
                 @click="router.push(`/position/items/${item.id}`)">
              <span class="w-24 shrink-0 font-medium">{{ item.name || item.symbol }}</span>
              <Tag :color="labelColor(item.label)" class="!mr-0 shrink-0">{{ labelName(item.label) }}</Tag>
              <Tag v-if="item.priority" :color="PRIORITY[item.priority]?.color" class="!mr-0 shrink-0">{{ PRIORITY[item.priority]?.name }}</Tag>
              <span class="text-sm text-gray-500">{{ detailOf(item, tier.tier) }}</span>
              <Tag v-if="item.quality?.grade" :color="GRADE_COLOR[item.quality.grade]" class="!mr-0 ml-auto shrink-0">{{ item.quality.grade }}</Tag>
            </div>
          </div>
          <Collapse v-if="rest.length" ghost size="small">
            <Collapse.Panel key="rest" :header="`5 其余 ${rest.length} 只`">
              <span v-for="item in rest" :key="item.id" class="mr-3 cursor-pointer text-sm" @click="router.push(`/position/items/${item.id}`)">
                {{ item.name || item.symbol }}
              </span>
            </Collapse.Panel>
          </Collapse>
        </Card>
      </Col>
      <Col :lg="7" :xs="24">
        <Card size="small">
          <template #title><Badge :count="board?.unread ?? 0" :offset="[10, 0]">提示</Badge></template>
          <template #extra><a v-if="board?.unread" @click="read()">全部已读</a></template>
          <List :data-source="board?.signals ?? []" size="small" :locale="{ emptyText: '还没有提示：结构确认后，规则触发时会出现在这里' }">
            <template #renderItem="{ item }">
              <List.Item class="cursor-pointer" :class="item.read ? 'opacity-60' : ''" @click="open(item)">
                <div class="w-full">
                  <div class="flex items-center justify-between">
                    <span :class="item.read ? '' : 'font-medium'">{{ item.name || item.symbol }}</span>
                    <Tag :color="PRIORITY[item.priority]?.color" class="!mr-0">{{ item.priority_name }}</Tag>
                  </div>
                  <div class="text-xs text-gray-500">{{ item.trade_date }} · {{ item.message }}</div>
                </div>
              </List.Item>
            </template>
          </List>
        </Card>
      </Col>
    </Row>

    <Card size="small" class="mt-3">
      <template #title>
        <Space wrap>
          <span>态势</span>
          <Tag v-for="l in PM_LABELS" :key="l.key" :color="l.color">{{ l.name }} {{ board?.counts[l.key] ?? 0 }}</Tag>
        </Space>
      </template>
      <template #extra><Segmented v-model:value="pool" size="small" :options="poolOptions" /></template>
      <Empty v-if="board && !board.items.length" description="标的库是空的：在“标的库”里粘贴代码添加">
        <Button type="primary" @click="router.push('/position/library')">去添加</Button>
      </Empty>
      <ItemTable v-else :loading="loading" :rows="rows" />
    </Card>
  </Page>
</template>
