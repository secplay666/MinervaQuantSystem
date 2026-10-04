<script lang="ts" setup>
import type { PmCampaign } from '#/api';

import { computed, onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';

import { Button, Card, Col, InputNumber, Row, Statistic, Table, Tag } from 'ant-design-vue';

import { pmCampaignsApi } from '#/api';
import { changeColor, pct } from '#/utils/format';

import { px } from './common';

const router = useRouter();
const data = ref<Awaited<ReturnType<typeof pmCampaignsApi>>>();
const loading = ref(false);
const leverage = ref(1);

const columns = [
  { key: 'item', title: '标的', width: 140 }, { key: 'round', title: '轮次', width: 70 },
  { key: 'segments', title: '剧本交易段（入场 → 离场）' }, { key: 'return', title: '本轮收益', width: 110 },
  { key: 'script', title: '颈线→目标', width: 100 },
];
const rows = computed(() => [...(data.value?.rows ?? [])].sort((a, b) =>
  (b.segments[0]?.entry_date ?? '').localeCompare(a.segments[0]?.entry_date ?? '')));
const scaled = (value: null | number | undefined) => (value === null || value === undefined ? null : value * leverage.value);

async function load() {
  loading.value = true;
  try {
    data.value = await pmCampaignsApi();
  } finally {
    loading.value = false;
  }
}

onMounted(load);
</script>

<template>
  <Page title="模拟战役总账" description="假定每一轮都按规则执行（满仓或半仓入场、按档减仓、止盈清仓），用收盘价算的收益；不是你的真实账户">
    <template #extra><Button :loading="loading" @click="load">刷新</Button></template>
    <Row :gutter="12">
      <Col :md="5" :xs="12"><Card size="small"><Statistic title="轮次（已结束 / 进行中）" :value="`${data?.totals.closed ?? 0} / ${data?.totals.open ?? 0}`" /></Card></Col>
      <Col :md="5" :xs="12"><Card size="small"><Statistic title="胜率（已结束的）" :value="pct(data?.totals.win_rate, 0)" /></Card></Col>
      <Col :md="5" :xs="12">
        <Card size="small">
          <Statistic title="平均每轮收益" :value="pct(scaled(data?.totals.average), 1, true)"
                     :value-style="{ color: changeColor(data?.totals.average) }" />
        </Card>
      </Col>
      <Col :md="9" :xs="24">
        <Card size="small" class="h-full">
          <div class="mb-1 text-sm text-gray-500">杠杆系数（只影响显示）</div>
          <InputNumber v-model:value="leverage" :min="0.1" :max="5" :step="0.1" class="!w-32" />
        </Card>
      </Col>
    </Row>
    <Card size="small" class="mt-3">
      <Table :columns="columns" :data-source="rows" :loading="loading" :row-key="(r: PmCampaign) => `${r.item_id}-${r.round_no}`"
             size="small" :pagination="{ pageSize: 30 }" :locale="{ emptyText: '还没有入过场的结构' }">
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'item'">
            <a @click="router.push(`/position/items/${record.item_id}`)">{{ record.name || record.symbol }}</a>
            <div class="text-xs text-gray-400">{{ record.symbol }}<Tag v-if="record.archived" class="ml-1">已归档</Tag></div>
          </template>
          <template v-else-if="column.key === 'round'">第 {{ record.round_no }} 轮</template>
          <template v-else-if="column.key === 'segments'">
            <div v-for="(seg, k) in record.segments" :key="k" class="text-sm">
              {{ record.round_no }}-{{ Number(k) + 1 }}：{{ seg.entry_date }} {{ px(seg.entry_close) }}（{{ pct(seg.entry_weight, 0) }}）
              → {{ seg.exit_date ? `${seg.exit_date} ${px(seg.exit_close)}` : `持有中 ${px(seg.exit_close)}` }}
              <span :style="{ color: changeColor(seg.return) }">{{ pct(scaled(seg.return), 1, true) }}</span>
            </div>
          </template>
          <template v-else-if="column.key === 'return'">
            <span :style="{ color: changeColor(record.round_return) }">{{ pct(scaled(record.round_return), 1, true) }}</span>
            <Tag v-if="record.open" class="ml-1" color="blue">进行中</Tag>
          </template>
          <template v-else-if="column.key === 'script'">{{ pct(record.script_return, 1, true) }}</template>
        </template>
      </Table>
    </Card>
  </Page>
</template>
