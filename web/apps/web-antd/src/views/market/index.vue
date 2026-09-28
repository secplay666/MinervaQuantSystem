<script lang="ts" setup>
import type { EchartsUIType } from '@vben/plugins/echarts';

import type { Bar, Overview } from '#/api';

import { nextTick, onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';
import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import { AutoComplete, Card, Col, Row, Segmented, Space, Statistic, Tag } from 'ant-design-vue';

import { indexBarsApi, overviewApi, searchApi } from '#/api';
import KlineChart from '#/components/kline-chart.vue';
import { bigYuan, changeColor, DOWN_COLOR, pct, UP_COLOR } from '#/utils/format';

const router = useRouter();
const overview = ref<Overview>();
const indexSymbol = ref('sh000300');
const indexBars = ref<Bar[]>([]);
const sectorChart = ref<EchartsUIType>();
const { renderEcharts } = useEcharts(sectorChart);
const query = ref('');
const options = ref<{ label: string; value: string }[]>([]);

async function loadIndex() {
  indexBars.value = await indexBarsApi(indexSymbol.value, 300);
}

function drawSectors() {
  if (!overview.value) return;
  const rows = [...overview.value.industries].reverse();
  renderEcharts({
    grid: { bottom: 20, left: 90, right: 60, top: 10 },
    series: [{
      data: rows.map((r) => ({ itemStyle: { color: r.change_pct >= 0 ? UP_COLOR : DOWN_COLOR }, value: (r.change_pct * 100).toFixed(2) })),
      label: { formatter: '{c}%', position: 'right', show: true },
      type: 'bar',
    }],
    tooltip: { formatter: (p: any) => `${p.name}：${p.value}%（${rows[p.dataIndex]?.count} 只）`, trigger: 'item' },
    xAxis: { axisLabel: { formatter: '{value}%' }, type: 'value' },
    yAxis: { data: rows.map((r) => r.name), type: 'category' },
  });
}

let timer: ReturnType<typeof setTimeout> | undefined;
function onSearch(text: string) {
  clearTimeout(timer);
  if (!text.trim()) {
    options.value = [];
    return;
  }
  timer = setTimeout(async () => {
    const rows = await searchApi(text.trim());
    options.value = rows.map((r) => ({ label: `${r.symbol} ${r.name}`, value: r.symbol }));
  }, 250);
}

onMounted(async () => {
  overview.value = await overviewApi();
  await Promise.all([loadIndex(), nextTick()]);
  drawSectors();
});
</script>

<template>
  <Page title="市场" :description="overview ? `交易日 ${overview.session}（对比 ${overview.previous_session}）` : ''">
    <template #extra>
      <AutoComplete v-model:value="query" :options="options" placeholder="输入代码或名称查看个股" style="width: 280px"
                    @search="onSearch" @select="(value: any) => router.push(`/instruments/${String(value)}`)" />
    </template>
    <template v-if="overview">
      <Row :gutter="[16, 16]" class="mb-4">
        <Col v-for="index in overview.indices" :key="index.symbol" :xs="12" :md="4">
          <Card size="small" class="cursor-pointer" @click="indexSymbol = index.symbol; loadIndex()">
            <Statistic :title="index.name" :value="index.close.toFixed(2)" :value-style="{ color: changeColor(index.change_pct) }" />
            <span :style="{ color: changeColor(index.change_pct) }">{{ pct(index.change_pct, 2, true) }}</span>
          </Card>
        </Col>
      </Row>
      <Row :gutter="[16, 16]">
        <Col :xs="24" :lg="15">
          <Card size="small">
            <template #title>
              <Space>
                指数 K 线
                <Segmented v-model:value="indexSymbol" size="small" :options="overview.indices.map((i) => ({ label: i.name, value: i.symbol }))"
                           @change="loadIndex" />
              </Space>
            </template>
            <KlineChart v-if="indexBars.length" :bars="indexBars" :ticker="indexSymbol" :height="440" :indicators="['VOL']" />
          </Card>
        </Col>
        <Col :xs="24" :lg="9">
          <Card size="small" title="涨跌分布" class="mb-4">
            <Space wrap>
              <Tag color="red">上涨 {{ overview.breadth.up }}</Tag>
              <Tag color="green">下跌 {{ overview.breadth.down }}</Tag>
              <Tag>平盘 {{ overview.breadth.flat }}</Tag>
              <Tag color="red">涨停 {{ overview.breadth.limit_up }}</Tag>
              <Tag color="green">跌停 {{ overview.breadth.limit_down }}</Tag>
            </Space>
            <div class="mt-2">全市场成交额 {{ bigYuan(overview.breadth.amount_cny, false) }} 元，{{ overview.breadth.traded }} 只有成交</div>
          </Card>
          <Card size="small" title="申万一级行业涨跌（等权平均）">
            <EchartsUI ref="sectorChart" height="620px" />
          </Card>
        </Col>
      </Row>
    </template>
  </Page>
</template>
