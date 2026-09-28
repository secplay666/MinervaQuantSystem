<script lang="ts" setup>
import type { Bar } from '#/api';

import { computed, onMounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';

import { Button, Card, Col, Descriptions, Empty, Row, Segmented, Table, Tag } from 'ant-design-vue';

import { barsApi, fundamentalsApi, instrumentApi } from '#/api';
import KlineChart from '#/components/kline-chart.vue';
import { bigYuan, changeColor } from '#/utils/format';

const route = useRoute();
const router = useRouter();
const symbol = computed(() => String(route.params.symbol));
const info = ref<Record<string, any>>();
const bars = ref<Bar[]>([]);
const adjust = ref('qfq');
const fundamentals = ref<Record<string, any>[]>([]);
const last = computed(() => bars.value.at(-1));

const BOARD: Record<string, string> = { BSE: '北交所', CHINEXT: '创业板', SSE_MAIN: '沪市主板', STAR: '科创板', SZSE_MAIN: '深市主板' };
const RISK: Record<string, string> = { '*ST': '*ST', DELISTING: '退市整理', normal: '正常', ST: 'ST' };

async function loadBars() {
  bars.value = await barsApi(symbol.value, adjust.value, 500);
}

async function load() {
  const [detail, rows] = await Promise.all([instrumentApi(symbol.value), fundamentalsApi(symbol.value)]);
  info.value = detail;
  fundamentals.value = rows;
  await loadBars();
}

const fundamentalColumns = [
  { dataIndex: 'report_date', title: '报告期' },
  { dataIndex: 'notice_date', title: '公告日' },
  { key: 'revenue', title: '营业总收入', align: 'right' as const },
  { key: 'parent', title: '归母净利润', align: 'right' as const },
  { key: 'deducted', title: '扣非归母净利润', align: 'right' as const },
  { dataIndex: 'basic_eps', title: '每股收益', align: 'right' as const },
];

watch(symbol, load);
onMounted(load);
</script>

<template>
  <Page :title="info ? `${info.name}（${symbol}）` : symbol">
    <template #extra><Button @click="router.back()">返回</Button></template>
    <Row :gutter="[16, 16]">
      <Col :xs="24" :lg="17">
        <Card size="small">
          <template #title>
            <span v-if="last" class="mr-3">
              <b :style="{ color: changeColor(last.pct_change) }">{{ last.close.toFixed(2) }}</b>
              <span :style="{ color: changeColor(last.pct_change) }" class="ml-2">
                {{ last.pct_change === null || last.pct_change === undefined ? '' : `${last.pct_change > 0 ? '+' : ''}${last.pct_change.toFixed(2)}%` }}
              </span>
              <span class="text-muted-foreground ml-2 text-xs">{{ last.trade_date }}</span>
            </span>
          </template>
          <template #extra>
            <Segmented v-model:value="adjust" size="small" :options="[{ label: '前复权', value: 'qfq' }, { label: '不复权', value: 'none' },
                                                                        { label: '后复权', value: 'hfq' }]" @change="loadBars" />
          </template>
          <KlineChart v-if="bars.length" :bars="bars" :ticker="symbol" :height="520" />
          <Empty v-else />
          <div class="text-muted-foreground mt-1 text-xs">前复权以最近一次除权为锚点，仅用于展示；收益计算使用后复权（ADR-004）。</div>
        </Card>
      </Col>
      <Col :xs="24" :lg="7">
        <Card v-if="info" size="small" title="基本信息">
          <Descriptions :column="1" size="small">
            <Descriptions.Item label="代码">{{ info.symbol }}</Descriptions.Item>
            <Descriptions.Item label="板块">{{ BOARD[info.board] ?? info.board }}</Descriptions.Item>
            <Descriptions.Item label="上市日期">{{ info.list_date }}</Descriptions.Item>
            <Descriptions.Item v-if="info.delist_date" label="退市日期">{{ info.delist_date }}</Descriptions.Item>
            <Descriptions.Item label="风险警示">
              <Tag :color="info.risk_status === 'normal' ? 'success' : 'error'">{{ RISK[info.risk_status] ?? info.risk_status }}</Tag>
            </Descriptions.Item>
            <Descriptions.Item label="申万行业">
              {{ info.industry ? `${info.industry.l1_name} / ${info.industry.l2_name} / ${info.industry.l3_name}` : '—' }}
            </Descriptions.Item>
            <Descriptions.Item v-if="last" label="成交额">{{ bigYuan(last.amount, false) }} 元</Descriptions.Item>
          </Descriptions>
        </Card>
      </Col>
      <Col :span="24">
        <Card size="small" title="财务（利润表，最近 8 期，累计值）">
          <Table :columns="fundamentalColumns" :data-source="fundamentals" row-key="report_date" size="small" :pagination="false">
            <template #bodyCell="{ column, record }">
              <template v-if="column.key === 'revenue'">{{ bigYuan(record.revenue ?? record.operate_income, false) }}</template>
              <template v-else-if="column.key === 'parent'">{{ bigYuan(record.parent_net_profit, false) }}</template>
              <template v-else-if="column.key === 'deducted'">{{ bigYuan(record.deducted_parent_net_profit, false) }}</template>
            </template>
          </Table>
          <div class="text-muted-foreground mt-1 text-xs">按公告日可见；同一报告期取最新版本。</div>
        </Card>
      </Col>
    </Row>
  </Page>
</template>
