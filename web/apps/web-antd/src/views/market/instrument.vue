<script lang="ts" setup>
import type { EchartsUIType } from '@vben/plugins/echarts';

import type { Bar, Signals } from '#/api';

import { computed, nextTick, onMounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import { useAccess } from '@vben/access';
import { Page } from '@vben/common-ui';
import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import { Button, Card, Col, Descriptions, Empty, Row, Table, Tag } from 'ant-design-vue';

import { barsApi, fundamentalsApi, instrumentApi, signalsApi } from '#/api';
import StockChart from '#/components/stock-chart/stock-chart.vue';
import { bigYuan, changeColor, DOWN_COLOR, FAMILY_LABEL, FAMILY_ORDER, pct, UP_COLOR } from '#/utils/format';

const route = useRoute();
const router = useRouter();
const symbol = computed(() => String(route.params.symbol));
const info = ref<Record<string, any>>();
const bars = ref<Bar[]>([]);
const fundamentals = ref<Record<string, any>[]>([]);
const last = computed(() => bars.value.at(-1));
const { hasAccessByCodes } = useAccess();
const signals = ref<Signals>();
const scoreChart = ref<EchartsUIType>();
const { renderEcharts } = useEcharts(scoreChart);
const families = computed(() => FAMILY_ORDER
  .map((key) => ({ label: FAMILY_LABEL[key] ?? key, value: signals.value?.row?.[`f_${key}`] as null | number | undefined }))
  .filter((f) => f.value !== undefined));

function drawScores() {
  const rows = [...families.value].reverse();
  renderEcharts({
    grid: { bottom: 20, left: 60, right: 30, top: 10 },
    series: [{ data: rows.map((f) => ({ itemStyle: { color: (f.value ?? 0) >= 0 ? UP_COLOR : DOWN_COLOR },
                                        value: f.value ?? null })), type: 'bar' }],
    tooltip: { trigger: 'axis' },
    xAxis: { type: 'value' },
    yAxis: { data: rows.map((f) => f.label), type: 'category' },
  });
}

async function loadSignals() {
  if (!hasAccessByCodes(['decision:view'])) return;
  signals.value = await signalsApi(symbol.value);
  if (signals.value.row) {
    await nextTick();
    drawScores();
  }
}

const BOARD: Record<string, string> = { BSE: '北交所', CHINEXT: '创业板', SSE_MAIN: '沪市主板', STAR: '科创板', SZSE_MAIN: '深市主板' };
const RISK: Record<string, string> = { '*ST': '*ST', DELISTING: '退市整理', normal: '正常', ST: 'ST' };

async function loadBars() {
  bars.value = await barsApi(symbol.value, 'none', 2); // the latest price for the header; the chart loads its own
}

async function load() {
  const [detail, rows] = await Promise.all([instrumentApi(symbol.value), fundamentalsApi(symbol.value)]);
  info.value = detail;
  fundamentals.value = rows;
  await Promise.all([loadBars(), loadSignals()]);
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
      <Col :xs="24" :lg="24">
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
            <Button size="small" type="primary" ghost @click="router.push(`/chart/${symbol}`)">大图看盘</Button>
          </template>
          <StockChart :symbol="symbol" :height="680" />
          <div class="text-muted-foreground mt-1 text-xs">前复权以最近一次除权为锚点，仅用于展示；收益计算使用后复权（ADR-004）。</div>
        </Card>
      </Col>
      <Col :xs="24" :lg="10">
        <Card v-if="info" size="small" title="基本信息" class="h-full">
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
      <Col v-if="signals?.run_id" :xs="24" :lg="14">
        <Card v-if="signals?.run_id" size="small" :title="`策略得分（${signals.trade_date} 调仓）`">
          <template v-if="signals.row">
            <Descriptions :column="1" size="small">
              <Descriptions.Item label="综合得分">{{ signals.row.score?.toFixed(3) ?? '—' }}</Descriptions.Item>
              <Descriptions.Item label="股票池内排名">
                {{ signals.row.rank ? `${signals.row.rank} / ${signals.scored}` : '不在股票池' }}
              </Descriptions.Item>
            </Descriptions>
            <EchartsUI ref="scoreChart" height="240px" />
            <div class="text-muted-foreground text-xs">各类因子的截面标准分（类内等权平均）；正值表示在该类上更有利。</div>
          </template>
          <Empty v-else description="该股票不在股票池内，也没有得分" />
          <div v-if="signals.history.length" class="mt-2">
            <div class="mb-1 text-xs font-medium">近期入选目标组合</div>
            <div v-for="h in signals.history.slice(0, 6)" :key="`${h.trade_date}-${h.account_id}`" class="text-xs">
              {{ h.trade_date }} · {{ h.account_id }} · 第 {{ h.rank }} 名 · {{ pct(h.target_weight) }}
            </div>
          </div>
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
