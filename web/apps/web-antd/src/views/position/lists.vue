<script lang="ts" setup>
import type { PmTopRow } from '#/api';

import { onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';

import { Button, Card, Progress, Statistic, Table, Tag } from 'ant-design-vue';

import { GRADE_COLOR, pmBreakoutsApi, pmTopsApi } from '#/api';
import { changeColor, pct } from '#/utils/format';

import { labelColor, labelName, openChart, progress, px } from './common';

const props = defineProps<{ kind: 'breakouts' | 'tops' }>();
const router = useRouter();
const loading = ref(false);
const breakouts = ref<Awaited<ReturnType<typeof pmBreakoutsApi>>>();
const tops = ref<Awaited<ReturnType<typeof pmTopsApi>>>();

const TEXT = {
  breakouts: { description: '收盘站上颈线、结构已激活的标的：已共振的已入场，待共振的在等主指数', title: '突破确立' },
  tops: { description: '量度满足区里留意头部、挂着头部颈线观察、已确认头部的标的；已确立的记下确认后的走势，作为看顶准确率', title: '头部确立' },
};
const breakoutColumns = [
  { key: 'item', title: '标的', width: 120 }, { dataIndex: 'activated_on', key: 'activated_on', title: '突破日', width: 100 },
  { key: 'breakout', title: '突破价', width: 80 }, { key: 'close', title: '现价', width: 90 },
  { key: 'from', title: '距突破位', width: 80 }, { key: 'completion', title: '完成度', width: 150 },
  { key: 'index', title: '主指数', width: 110 }, { key: 'script', title: '剧本收益', width: 80 },
  { key: 'quality', title: '质地', width: 56 }, { key: 'waiting', title: '在等什么' },
];
const removedColumns = [
  { key: 'item', title: '标的' }, { dataIndex: 'removed_on', key: 'removed_on', title: '移出日' },
  { dataIndex: 'reason', key: 'reason', title: '原因' },
];
const topColumns = [
  { key: 'item', title: '标的', width: 120 }, { key: 'close', title: '现价', width: 90 },
  { key: 'completion', title: '完成度', width: 150 }, { key: 'detail', title: '情况' },
];

async function load() {
  loading.value = true;
  try {
    if (props.kind === 'breakouts') breakouts.value = await pmBreakoutsApi();
    else tops.value = await pmTopsApi();
  } finally {
    loading.value = false;
  }
}

function topDetail(row: PmTopRow): string {
  if (row.state === 'observing') return `头部颈线 ${px(row.top_neckline)}，距离 ${pct(row.distance, 1, true)}；收盘跌破即清仓`;
  if (row.state === 'watching') return row.label === 'top' ? '标签为顶部：只做退出' : '完成度已进入量度满足区，留意头部形态';
  return `${row.confirmed_on} ${row.how}，当时 ${px(row.confirmed_close)}，之后 ${pct(row.change, 1, true)}`;
}

onMounted(load);
</script>

<template>
  <Page :title="TEXT[kind].title" :description="TEXT[kind].description">
    <template #extra><Button :loading="loading" @click="load">刷新</Button></template>

    <template v-if="kind === 'breakouts' && breakouts">
      <Card v-for="[key, title] in [['resonant', '已共振（已入场）'], ['waiting', '待共振（已突破，等主指数）']]" :key="key" size="small"
            class="mb-3" :title="`${title} ${breakouts[key as 'resonant'].length}`">
        <Table :columns="breakoutColumns" :data-source="breakouts[key as 'resonant']" :loading="loading" row-key="id" size="small"
               :pagination="false" :locale="{ emptyText: key === 'resonant' ? '没有已入场的突破' : '没有在等主指数的突破' }">
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'item'">
              <a @click="router.push(`/position/items/${record.id}`)">{{ record.name || record.symbol }}</a>
              <div class="text-xs text-gray-400">{{ record.symbol }}
                <a class="ml-1" @click="openChart(router, record.symbol)">K 线</a></div>
            </template>
            <template v-else-if="column.key === 'breakout'">{{ px(record.breakout_close) }}</template>
            <template v-else-if="column.key === 'close'">
              {{ px(record.close) }} <span class="text-xs" :style="{ color: changeColor(record.change) }">{{ pct(record.change, 2, true) }}</span>
            </template>
            <template v-else-if="column.key === 'from'"><span :style="{ color: changeColor(record.from_breakout) }">{{ pct(record.from_breakout, 1, true) }}</span></template>
            <template v-else-if="column.key === 'completion'">
              <Progress :percent="progress(record.completion)" size="small" :format="() => pct(record.completion, 0)" />
              <div v-if="record.half_entry" class="text-xs text-orange-500">指数晚期·半仓入场</div>
            </template>
            <template v-else-if="column.key === 'index'">
              {{ record.main_index_name ?? record.main_index ?? '—' }}
              <Tag v-if="record.main_index_label" class="ml-1" :color="labelColor(record.main_index_label)">{{ labelName(record.main_index_label) }}</Tag>
            </template>
            <template v-else-if="column.key === 'script'">{{ pct(record.script_return, 1, true) }}</template>
            <template v-else-if="column.key === 'quality'">
              <Tag v-if="record.quality?.grade" :color="GRADE_COLOR[record.quality.grade]">{{ record.quality.grade }}</Tag>
              <span v-else class="text-gray-300">—</span>
            </template>
            <template v-else-if="column.key === 'waiting'">{{ record.waiting_for }}</template>
          </template>
        </Table>
      </Card>
      <Card size="small" :title="`近 7 天移出 ${breakouts.removed.length}`">
        <Table :columns="removedColumns" :data-source="breakouts.removed" row-key="id" size="small" :pagination="false"
               :locale="{ emptyText: '近 7 天没有移出的' }">
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'item'">
              <a @click="router.push(`/position/items/${record.id}`)">{{ record.name || record.symbol }}</a>
              <span class="ml-1 text-xs text-gray-400">{{ record.symbol }}</span>
            </template>
          </template>
        </Table>
      </Card>
    </template>

    <template v-if="kind === 'tops' && tops">
      <Card size="small" class="mb-3" title="看顶准确率（已确立的，确认后涨跌带 ±1% 中性带）">
        <div class="flex gap-12">
          <Statistic title="判对（之后跌超 1%）" :value="tops.accuracy.right" />
          <Statistic title="判早（之后涨超 1%）" :value="tops.accuracy.early" />
          <Statistic title="中性" :value="tops.accuracy.neutral" />
        </div>
      </Card>
      <Card v-for="[key, title] in [['watching', '留意头部'], ['observing', '观察中（已挂头部颈线）'], ['confirmed', '已确立']]" :key="key"
            size="small" class="mb-3" :title="`${title} ${tops[key as 'watching'].length}`">
        <Table :columns="topColumns" :data-source="tops[key as 'watching']" row-key="id" size="small" :pagination="false"
               :locale="{ emptyText: '没有' }">
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'item'">
              <a @click="router.push(`/position/items/${record.id}`)">{{ record.name || record.symbol }}</a>
              <div class="text-xs text-gray-400">{{ record.symbol }}</div>
            </template>
            <template v-else-if="column.key === 'close'">{{ px(record.close) }}</template>
            <template v-else-if="column.key === 'completion'">
              <Progress v-if="record.completion !== null" :percent="progress(record.completion)" size="small"
                        :format="() => pct(record.completion, 0)" />
              <span v-else class="text-gray-300">—</span>
            </template>
            <template v-else-if="column.key === 'detail'">
              {{ topDetail(record as PmTopRow) }}
              <Tag v-if="record.verdict" class="ml-2" :color="record.verdict === '判对' ? 'green' : record.verdict === '判早' ? 'orange' : 'default'">
                {{ record.verdict }}
              </Tag>
            </template>
          </template>
        </Table>
      </Card>
    </template>
  </Page>
</template>
