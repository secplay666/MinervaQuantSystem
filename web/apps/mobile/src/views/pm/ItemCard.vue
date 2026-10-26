<script setup lang="ts">
/**
 * One item of the library as a card (the PC item table's row): name, label,
 * close, structure and completion, what it waits for, sentinel, index, quality
 * and the company / holders / industry tags.  A tap opens the item page;
 * ``manage`` adds the library's groups, view and the ⋯ button.
 */
import type { PmRow } from './pm-api';

import { computed } from 'vue';
import { useRouter } from 'vue-router';

import { color, pct } from '../../format';
import { GRADE_COLOR, KIND_NAMES, labelColor, labelName, nearestSentinel, PHASE, progress, px } from './pm-api';
import PmTag from './PmTag.vue';
import Tags from './Tags.vue';

const props = defineProps<{ manage?: boolean; row: PmRow }>();
const emit = defineEmits<{ adopt: []; more: [] }>();
const router = useRouter();
const sentinel = computed(() => nearestSentinel(props.row));
const canAdopt = computed(() => props.row.stage && props.row.stage.label !== props.row.label && props.row.stage.stage !== 'unknown');
const SOURCE: Record<string, string> = { auto: '自动', manual: '手工', system: '采纳' };
</script>

<template>
  <div class="card pm-card" @click="router.push(`/pm/items/${row.id}`)">
    <div class="pm-card-head">
      <div style="min-width: 0">
        <b>{{ row.name || row.symbol }}</b>
        <span v-if="row.star" style="margin-left: 4px; color: #f59e0b">{{ '★'.repeat(row.star) }}</span>
        <div class="muted">
          {{ row.symbol }}<template v-if="manage"> · {{ KIND_NAMES[row.kind] ?? row.kind }}</template> · {{ row.pool_name }}
          <template v-if="row.round_no > 1"> · 第 {{ row.round_no }} 轮</template>
        </div>
      </div>
      <div style="text-align: right; white-space: nowrap">
        <div :style="{ color: color(row.change), fontWeight: 600 }">{{ px(row.close) }}</div>
        <div :style="{ color: color(row.change), fontSize: '12px' }">{{ pct(row.change, 2, true) }}</div>
      </div>
      <van-icon v-if="manage" name="ellipsis" size="20" class="pm-more" @click.stop="emit('more')" />
    </div>

    <div style="margin-top: 6px">
      <PmTag :color="labelColor(row.label)">{{ labelName(row.label) }}</PmTag>
      <span v-if="manage && row.label_source" class="muted" style="margin-right: 6px">{{ SOURCE[row.label_source] ?? '' }}</span>
      <PmTag v-if="row.phase" :color="PHASE[row.phase]?.color">{{ PHASE[row.phase]?.name ?? row.phase }}</PmTag>
      <PmTag v-if="row.quality?.grade" :color="GRADE_COLOR[row.quality.grade]">质地 {{ row.quality.grade }}</PmTag>
      <Tags :company="row.company" :holders="row.holders" :industry="row.industry" :show-industry="manage" />
    </div>

    <div v-if="row.stage && (manage || row.stage.label !== row.label)" class="muted" style="margin-top: 4px">
      系统观点：<PmTag :color="labelColor(row.stage.label)">{{ row.stage.name }}</PmTag>{{ row.stage.days }} 个交易日
      <van-button v-if="manage && canAdopt" size="small" type="primary" plain style="margin-left: 6px" @click.stop="emit('adopt')">采纳</van-button>
    </div>

    <template v-if="row.completion !== null">
      <div class="pm-progress">
        <van-progress :percentage="progress(row.completion)" :show-pivot="false" stroke-width="6"
                      :color="row.top_watch ? '#e5484d' : '#1677ff'" style="flex: 1" />
        <span>{{ pct(row.completion, 0) }}</span>
        <span class="muted">仓位 {{ pct(row.weight, 0) }}</span>
      </div>
      <div class="muted">颈线 {{ px(row.neckline) }} → 目标 {{ px(row.target) }}</div>
    </template>

    <div class="pm-wait" :style="{ color: row.top_watch ? '#d46b08' : undefined }">{{ row.waiting_for }}</div>
    <div v-if="row.next_price" class="muted">下一步：{{ row.next_step }} @ {{ px(row.next_price) }}</div>
    <div v-if="sentinel" class="muted" style="color: #1677ff">
      ⚓ 距哨兵 {{ pct(sentinel.distance, 1, true) }}<template v-if="sentinel.days"> · 约 {{ sentinel.days }} 天</template>
    </div>
    <div v-if="row.main_index" class="muted">
      主指数 {{ row.main_index_name ?? row.main_index }}
      <PmTag v-if="row.main_index_label" :color="labelColor(row.main_index_label)">{{ labelName(row.main_index_label) }}</PmTag>
    </div>
    <div v-if="manage && (row.groups.length || row.note)" style="margin-top: 4px">
      <van-tag v-for="g in row.groups" :key="g" plain style="margin-right: 4px">{{ g }}</van-tag>
      <span v-if="row.note" class="muted">{{ row.note }}</span>
    </div>
  </div>
</template>

<style>
.pm-card { font-size: 13px; }
.pm-card-head { display: flex; gap: 8px; align-items: flex-start; justify-content: space-between; }
.pm-more { padding: 10px; margin: -10px -10px -10px 0; color: #969799; }
.pm-progress { display: flex; gap: 8px; align-items: center; margin-top: 8px; font-size: 12px; }
.pm-wait { margin-top: 4px; line-height: 18px; }
</style>
