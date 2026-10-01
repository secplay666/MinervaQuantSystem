<script lang="ts" setup>
import type { Dayjs } from 'dayjs';

import type { PmJob, PmSettings } from '#/api';

import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue';

import { Page } from '@vben/common-ui';

import { Alert, Button, Card, Col, DatePicker, Form, InputNumber, Radio, Row, Statistic, Table, Tag } from 'ant-design-vue';

import { pmJobApi, pmSettingsApi, pmStartBacktestApi } from '#/api';
import { changeColor, pct } from '#/utils/format';

const settings = ref<PmSettings>();
const form = reactive<{ end?: Dayjs; preset: string; start?: Dayjs; values: Record<string, number> }>({ preset: 'steady', values: {} });
const job = ref<PmJob>();
const busy = computed(() => job.value?.status === 'queued' || job.value?.status === 'running');
const result = computed(() => (job.value?.status === 'done' ? job.value.result : null) as null | Record<string, any>);
let timer: ReturnType<typeof setTimeout> | undefined;

const EDITABLE = [
  { key: 'ma', name: '均线周期', pct: false }, { key: 'slope', name: '斜率回看', pct: false },
  { key: 'flat', name: '个股走平阈值', pct: true }, { key: 'index_flat', name: '指数走平阈值', pct: true },
  { key: 'confirm', name: '确认天数', pct: false },
];
const presetValues = computed(() => settings.value?.presets.find((p) => p.key === form.preset)?.params ?? {});

const statColumns = (first: string, key: string) => [
  { dataIndex: key, key, title: first },
  { dataIndex: 'count', key: 'count', title: '样本（股·日）' },
  ...[20, 60, 120].map((h) => ({ dataIndex: `excess_${h}`, key: `excess_${h}`, title: `${h} 日超额` })),
  { dataIndex: 'return_60', key: 'return_60', title: '60 日收益' },
  { dataIndex: 'beat_60', key: 'beat_60', title: '60 日跑赢比例' },
];
const yearColumns = [
  { dataIndex: 'year', key: 'year', title: '年份' }, { dataIndex: 'market', key: 'market', title: '全市场等权' },
  { dataIndex: 'stage2', key: 'stage2', title: '第二阶段组合' }, { dataIndex: 'stage2_gated', key: 'stage2_gated', title: '加大盘闸门' },
  { key: 'index', title: '沪深300 各阶段占比' },
];
const STAGE_SHORT: Record<string, string> = { advance: '右', base: '底', decline: '左', top: '顶' };

function choosePreset(key: string) {
  form.preset = key;
  form.values = Object.fromEntries(EDITABLE.map((f) => [f.key, settings.value?.presets.find((p) => p.key === key)?.params[f.key] ?? 0]));
}

async function poll() {
  if (!job.value) return;
  job.value = await pmJobApi(job.value.id);
  if (busy.value) timer = setTimeout(poll, 1500);
}

async function run() {
  const params = Object.fromEntries(EDITABLE.filter((f) => Math.abs((form.values[f.key] ?? 0) - (presetValues.value[f.key] ?? 0)) > 1e-9)
    .map((f) => [f.key, form.values[f.key]!]));
  job.value = await pmStartBacktestApi({ end: form.end?.format('YYYY-MM-DD'), params, preset: form.preset,
                                         start: form.start?.format('YYYY-MM-DD') });
  clearTimeout(timer);
  if (busy.value) timer = setTimeout(poll, 1500);
}

function isRate(key: string) {
  return key.startsWith('excess_') || key.startsWith('return_') || ['market', 'stage2', 'stage2_gated'].includes(key);
}

onMounted(async () => {
  settings.value = await pmSettingsApi();
  choosePreset(settings.value.stage_preset);
});
onBeforeUnmount(() => clearTimeout(timer));
</script>

<template>
  <Page title="阶段回测" description="四阶段在全市场（含已退市股票）上的表现：各阶段之后的收益、大盘闸门和筑底判据的过滤效果">
    <Card size="small">
      <Form layout="inline">
        <Form.Item class="!mb-2" label="方案">
          <Radio.Group :value="form.preset" button-style="solid" @change="(e: any) => choosePreset(e.target.value)">
            <Radio.Button v-for="p in settings?.presets ?? []" :key="p.key" :value="p.key">{{ p.name }}</Radio.Button>
          </Radio.Group>
        </Form.Item>
        <Form.Item v-for="f in EDITABLE" :key="f.key" class="!mb-2" :label="f.name">
          <InputNumber :value="f.pct ? Math.round((form.values[f.key] ?? 0) * 10000) / 100 : form.values[f.key]" class="!w-24"
                       :step="f.pct ? 0.1 : 1" :addon-after="f.pct ? '%' : undefined"
                       @change="(v: any) => (form.values[f.key] = f.pct ? v / 100 : v)" />
        </Form.Item>
        <Form.Item class="!mb-2" label="区间">
          <DatePicker v-model:value="form.start" placeholder="开始（默认最早）" class="!w-36" />
          <DatePicker v-model:value="form.end" placeholder="结束（默认最新）" class="ml-2 !w-36" />
        </Form.Item>
        <Form.Item class="!mb-2"><Button type="primary" :loading="busy" @click="run">运行</Button></Form.Item>
      </Form>
      <div class="text-xs text-gray-400">加载全市场日线需要十几秒到一分钟；相同参数和数据的结果会直接复用。</div>
    </Card>

    <Alert v-if="job?.status === 'failed'" class="mt-3" show-icon type="error" :message="`回测失败：${job.error}`" />
    <Alert v-else-if="busy" class="mt-3" show-icon type="info" :message="job?.status === 'queued' ? '排队中（同一时间只跑一个回测）' : '计算中…'" />

    <template v-if="result">
      <Row :gutter="12" class="mt-3">
        <Col :md="8" :xs="24">
          <Card size="small" title="全市场等权（基准）">
            <Statistic :value="pct(result.overall.market.total, 1, true)" title="区间收益" />
            <div class="text-xs text-gray-500">最大回撤 {{ pct(result.overall.market.max_drawdown, 1) }}</div>
          </Card>
        </Col>
        <Col v-for="[key, name] in [['stage2', '持有第二阶段（右侧）个股'], ['stage2_gated', '同上，且沪深300 在右侧']]" :key="key" :md="8" :xs="24">
          <Card size="small" :title="name">
            <Statistic :value="pct(result.overall[key].total, 1, true)" title="区间收益"
                       :value-style="{ color: changeColor(result.overall[key].total - result.overall.market.total) }" />
            <div class="text-xs text-gray-500">最大回撤 {{ pct(result.overall[key].max_drawdown, 1) }} · 平均持有 {{ Math.round(result.overall[key].avg_names) }} 只</div>
          </Card>
        </Col>
      </Row>
      <div class="mt-1 text-xs text-gray-400">{{ result.start }} 至 {{ result.end }}，{{ result.stocks }} 只股票；超额 = 相对全市场等权的后续收益；只看收盘，不计费用。</div>

      <Card v-for="[key, title, first] in [['stages', '各阶段之后的表现', '阶段'], ['transitions', '阶段切换之后', '切换'],
                                          ['gate', '筑底→右侧时的大盘闸门（沪深300 所处阶段）', '沪深300'],
                                          ['filters', '筑底→右侧的过滤条件', '条件']]" :key="key" size="small" class="mt-3" :title="title">
        <Table :columns="statColumns(first!, 'name')" :data-source="result[key!]" row-key="name" size="small" :pagination="false">
          <template #bodyCell="{ column, record, text }">
            <template v-if="column.key === 'name'">
              {{ text }}<span v-if="record.share !== undefined" class="ml-1 text-xs text-gray-400">占 {{ pct(record.share, 0) }}</span>
            </template>
            <template v-else-if="column.key === 'count'">{{ text?.toLocaleString() }}</template>
            <template v-else-if="column.key === 'beat_60'">{{ pct(text, 0) }}</template>
            <span v-else-if="isRate(String(column.key))" :style="{ color: String(column.key).startsWith('excess') ? changeColor(text) : undefined }">
              {{ pct(text, 2, true) }}
            </span>
          </template>
        </Table>
      </Card>

      <Card size="small" class="mt-3" title="分年">
        <Table :columns="yearColumns" :data-source="result.yearly" row-key="year" size="small" :pagination="false">
          <template #bodyCell="{ column, record, text }">
            <template v-if="column.key === 'index'">
              <Tag v-for="(share, stage) in record.index_stages" :key="stage">{{ STAGE_SHORT[stage] ?? stage }} {{ pct(share as number, 0) }}</Tag>
            </template>
            <span v-else-if="isRate(String(column.key))" :style="{ color: changeColor(text) }">{{ pct(text, 1, true) }}</span>
          </template>
        </Table>
      </Card>
    </template>
  </Page>
</template>
