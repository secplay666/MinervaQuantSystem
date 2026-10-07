<script lang="ts" setup>
import type { PmRow } from '#/api';

import { useRouter } from 'vue-router';

import { Button, Progress, Table, Tag, Tooltip } from 'ant-design-vue';

import { GRADE_COLOR, PHASE } from '#/api';
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
  { key: 'quality', title: '质地', width: 56 },
  { key: 'industry', title: '行业拥挤度', width: 110 },
  { key: 'close', title: '收盘', width: 84 },
];

/** The nearest active sentinel of a row, for the "what it waits for" cell. */
function nearestSentinel(row: PmRow) {
  return row.sentinels.filter((s) => s.status === 'active' && s.distance !== null)
    .sort((a, b) => Math.abs(a.distance!) - Math.abs(b.distance!))[0];
}
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
        <div class="text-xs text-gray-400">{{ record.pool_name }}<template v-if="record.round_no > 1"> · 第 {{ record.round_no }} 轮</template></div>
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
        <div v-if="nearestSentinel(record as PmRow)" class="text-xs text-blue-500">
          ⚓ 距哨兵 {{ pct(nearestSentinel(record as PmRow)!.distance, 1, true) }}
          <template v-if="nearestSentinel(record as PmRow)!.days"> · 约 {{ nearestSentinel(record as PmRow)!.days }} 天</template>
        </div>
      </template>
      <template v-else-if="column.key === 'quality'">
        <Tooltip v-if="record.quality?.grade" :title="`质地 ${record.quality.score} 分（只用于排序，不触发信号）`">
          <Tag :color="GRADE_COLOR[record.quality.grade]">{{ record.quality.grade }}</Tag>
        </Tooltip>
        <span v-else class="text-gray-300">—</span>
      </template>
      <template v-else-if="column.key === 'industry'">
        <Tooltip v-if="record.industry"
                 :title="`${record.industry.name}：拥挤度 ${record.industry.c.toFixed(2)}，${record.industry.zone_name ?? '—'}（${record.industry.as_of}）。升破 ${record.industry.high} 提醒，回落到 ${record.industry.low} 以下解除；点击看钱去哪地图`">
          <a class="text-xs" @click="router.push('/moneymap')">
            {{ record.industry.name }} {{ record.industry.c.toFixed(2) }}
            <Tag v-if="record.industry.zone === 'crowded'" color="red" class="ml-1">拥挤</Tag>
          </a>
        </Tooltip>
        <span v-else class="text-gray-300">—</span>
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
