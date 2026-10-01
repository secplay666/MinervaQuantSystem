<script lang="ts" setup>
import type { PmRow } from '#/api';

import { useRouter } from 'vue-router';

import { Button, Progress, Table, Tag, Tooltip } from 'ant-design-vue';

import { PHASE } from '#/api';
import { changeColor, pct } from '#/utils/format';

import { labelColor, labelName, openChart, progress, px } from './common';

defineProps<{ loading?: boolean; pageSize?: number; rows: PmRow[] }>();
const router = useRouter();

const columns = [
  { key: 'item', title: '标的', width: 120 },
  { key: 'label', title: '标签', width: 96 },
  { key: 'phase', title: '结构', width: 76 },
  { key: 'completion', title: '完成度', width: 170 },
  { key: 'weight', title: '仓位', width: 56 },
  { key: 'waiting', title: '在等什么 / 下一价位', width: 230 },
  { key: 'index', title: '主指数', width: 96 },
  { key: 'close', title: '收盘', width: 84 },
];
</script>

<template>
  <Table :columns="columns" :data-source="rows" :loading="loading" row-key="id" size="small"
         :pagination="rows.length > (pageSize ?? 30) ? { pageSize: pageSize ?? 30 } : false">
    <template #bodyCell="{ column, record }">
      <template v-if="column.key === 'item'">
        <a @click="router.push(`/position/items/${record.id}`)">{{ record.name || record.symbol }}</a>
        <div class="text-xs text-gray-400">
          {{ record.symbol }}
          <Button size="small" type="link" class="!h-auto !p-0 !text-xs" @click="openChart(router, record.symbol)">K 线</Button>
          <span v-if="record.star" class="ml-1 text-amber-500">{{ '★'.repeat(record.star) }}</span>
        </div>
      </template>
      <template v-else-if="column.key === 'label'">
        <Tag :color="labelColor(record.label)">{{ labelName(record.label) }}</Tag>
        <Tooltip v-if="record.stage && record.stage.label !== record.label" :title="`系统观点：${record.stage.name}。${record.stage.reason}`">
          <div class="text-xs text-gray-400">观点：{{ record.stage.name }}</div>
        </Tooltip>
      </template>
      <template v-else-if="column.key === 'phase'">
        <Tag v-if="record.phase" :color="PHASE[record.phase]?.color">{{ PHASE[record.phase]?.name ?? record.phase }}</Tag>
        <span v-else class="text-gray-400">未画</span>
      </template>
      <template v-else-if="column.key === 'completion'">
        <template v-if="record.completion !== null">
          <Progress :percent="progress(record.completion)" size="small" :status="record.top_watch ? 'exception' : 'normal'"
                    :format="() => pct(record.completion, 0)" />
          <div class="text-xs text-gray-400">颈线 {{ px(record.neckline) }} → 目标 {{ px(record.target) }}</div>
        </template>
        <span v-else class="text-gray-400">—</span>
      </template>
      <template v-else-if="column.key === 'weight'">{{ record.completion !== null ? pct(record.weight, 0) : '—' }}</template>
      <template v-else-if="column.key === 'waiting'">
        <span :class="record.top_watch ? 'text-orange-500' : ''">{{ record.waiting_for }}</span>
        <div v-if="record.next_price" class="text-xs text-gray-400">下一步：{{ record.next_step }} @ {{ px(record.next_price) }}</div>
      </template>
      <template v-else-if="column.key === 'index'">
        <template v-if="record.main_index">
          {{ record.main_index_name ?? record.main_index }}
          <Tag v-if="record.main_index_label" class="ml-1" :color="labelColor(record.main_index_label)">{{ labelName(record.main_index_label) }}</Tag>
        </template>
        <span v-else class="text-gray-400">—</span>
      </template>
      <template v-else-if="column.key === 'close'">
        {{ px(record.close) }}
        <div class="text-xs" :style="{ color: changeColor(record.change) }">{{ pct(record.change, 2, true) }}</div>
      </template>
    </template>
  </Table>
</template>
