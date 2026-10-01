<script lang="ts" setup>
import type { Dayjs } from 'dayjs';

import type { LevelEntry, PmDetail, PmLabel, PmLevelKind } from '#/api';

import { computed, onMounted, reactive, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';

import { Alert, Button, Card, Checkbox, Col, DatePicker, Descriptions, Empty, Form, Input, InputNumber, message, Progress,
         Radio, Row, Select, Space, Table, Tag, Timeline, Tooltip } from 'ant-design-vue';

import { LEVEL_NAMES, PHASE, PM_LABELS, pmItemApi, pmLabelApi, pmLevelApi, PRIORITY } from '#/api';
import { pct } from '#/utils/format';

import { labelColor, labelName, openChart, progress, px } from './common';

const route = useRoute();
const router = useRouter();
const detail = ref<PmDetail>();
const loading = ref(false);
const id = computed(() => Number(route.params.id));
const item = computed(() => detail.value?.item);
const roundLevels = computed(() => (detail.value?.levels ?? []).filter((l) => l.round_no === detail.value?.round_no));
const olderLevels = computed(() => (detail.value?.levels ?? []).filter((l) => l.round_no !== detail.value?.round_no));
const form = reactive<{ effective?: Dayjs; kind: PmLevelKind; lower?: number; new_round: boolean; note: string; price?: number;
                        top_mode: 'confirmed' | 'observe' }>({ kind: 'neckline', new_round: false, note: '', top_mode: 'observe' });
const saving = ref(false);

const levelColumns = [
  { dataIndex: 'kind', key: 'kind', title: '价位' },
  { key: 'price', title: '价格（前复权）' },
  { dataIndex: 'effective_date', key: 'effective_date', title: '生效日' },
  { key: 'version', title: '版本' },
  { key: 'source', title: '来源' },
  { dataIndex: 'note', key: 'note', title: '备注' },
];
const ladderColumns = [
  { key: 'completion', title: '完成度' }, { key: 'price', title: '价格' }, { key: 'keep', title: '留仓' },
  { key: 'done', title: '状态' },
];
const segmentColumns = [
  { dataIndex: 'entry_date', key: 'entry_date', title: '入场日' }, { key: 'entry', title: '入场价 / 仓位' },
  { key: 'exit', title: '离场' }, { key: 'return', title: '收益' },
];
const SOURCE: Record<string, string> = { drawing: '画线', manual: '手工', pattern: '自动形态' };

async function load() {
  loading.value = true;
  try {
    detail.value = await pmItemApi(id.value);
  } finally {
    loading.value = false;
  }
}

async function setLabel(label?: PmLabel) {
  detail.value = await pmLabelApi(id.value, label ? { label } : { use_view: true });
  message.success(`标签改为 ${detail.value.item.label_name}，从 ${detail.value.item.latest} 起生效`);
}

async function saveLevel() {
  if (!form.price) {
    message.warning('请填写价格');
    return;
  }
  const body: LevelEntry = { kind: form.kind, new_round: form.new_round, note: form.note || undefined, price: form.price };
  if (form.kind === 'base_zone' && form.lower) body.lower = form.lower;
  if (form.kind === 'top_neckline') body.top_mode = form.top_mode;
  if (form.effective) body.effective_date = form.effective.format('YYYY-MM-DD');
  saving.value = true;
  try {
    detail.value = await pmLevelApi(id.value, body);
    message.success(`${LEVEL_NAMES[form.kind]} ${form.price} 已确认`);
    Object.assign(form, { lower: undefined, new_round: false, note: '', price: undefined });
  } finally {
    saving.value = false;
  }
}

watch(id, load);
onMounted(load);
</script>

<template>
  <Page :title="item ? `${item.name || item.symbol}（${item.symbol}）` : '标的结构'"
        description="价位按前复权显示；系统按后复权保存，除权后自动对齐">
    <template #extra>
      <Space>
        <Button v-if="item" @click="openChart(router, item.symbol)">在看盘中画线</Button>
        <Button @click="router.push('/position/library')">返回标的库</Button>
      </Space>
    </template>
    <template v-if="detail && item">
      <Card size="small" title="方向标签">
        <Space wrap>
          <Select :value="item.label" class="!w-28" @change="(v: any) => setLabel(v)">
            <Select.Option v-for="l in PM_LABELS" :key="l.key" :value="l.key"><Tag :color="l.color">{{ l.name }}</Tag></Select.Option>
          </Select>
          <span class="text-gray-500">{{ PM_LABELS.find((l) => l.key === item!.label)?.rule }}</span>
          <template v-if="item.stage">
            <span class="ml-4">系统观点：</span>
            <Tag :color="labelColor(item.stage.label)">{{ item.stage.name }}</Tag>
            <span class="text-xs text-gray-500">{{ item.stage.reason }}</span>
            <Button v-if="item.stage.label !== item.label && item.stage.stage !== 'unknown'" size="small" type="primary" ghost
                    @click="setLabel()">采纳</Button>
          </template>
          <span v-else-if="detail.label_mode === 'manual'" class="text-xs text-gray-400">手工模式，不显示系统观点</span>
        </Space>
      </Card>

      <Row :gutter="12" class="mt-3">
        <Col :lg="14" :xs="24">
          <Card size="small" :title="`结构（第 ${detail.round_no} 轮）`">
            <Table :columns="levelColumns" :data-source="roundLevels" row-key="id" size="small" :pagination="false"
                   :locale="{ emptyText: '还没有确认的价位：在看盘中选中画线“设为颈线/目标”，或在下面直接填写' }">
              <template #bodyCell="{ column, record }">
                <template v-if="column.key === 'kind'">
                  {{ LEVEL_NAMES[record.kind as PmLevelKind] ?? record.kind }}
                  <Tag v-if="record.top_mode" :color="record.top_mode === 'confirmed' ? 'red' : 'orange'">
                    {{ record.top_mode === 'confirmed' ? '已确认' : '观察' }}
                  </Tag>
                </template>
                <template v-else-if="column.key === 'price'">
                  {{ record.lower ? `${px(record.lower)} – ` : '' }}{{ px(record.price) }}
                  <Tooltip v-if="record.basis === 'qfq' && Math.abs(record.entered_price - record.price) > 0.005"
                           :title="`确认时输入 ${record.entered_price}（前复权），之后有除权，已按复权因子换算`">
                    <span class="text-xs text-gray-400">（输入 {{ record.entered_price }}）</span>
                  </Tooltip>
                </template>
                <template v-else-if="column.key === 'version'">v{{ record.version }}</template>
                <template v-else-if="column.key === 'source'">{{ SOURCE[record.source] ?? record.source }} · {{ record.created_by }}</template>
              </template>
            </Table>
            <Form layout="inline" class="mt-3">
              <Form.Item class="!mb-2">
                <Select v-model:value="form.kind" class="!w-28"
                        :options="Object.entries(LEVEL_NAMES).map(([value, label]) => ({ label, value }))" />
              </Form.Item>
              <Form.Item v-if="form.kind === 'base_zone'" class="!mb-2" label="下沿">
                <InputNumber v-model:value="form.lower" :min="0" :step="0.01" class="!w-24" />
              </Form.Item>
              <Form.Item class="!mb-2" :label="form.kind === 'base_zone' ? '上沿' : '价格'">
                <InputNumber v-model:value="form.price" :min="0" :step="0.01" class="!w-24" />
              </Form.Item>
              <Form.Item v-if="form.kind === 'top_neckline'" class="!mb-2">
                <Radio.Group v-model:value="form.top_mode" size="small">
                  <Radio.Button value="observe">先观察（跌破再清仓）</Radio.Button>
                  <Radio.Button value="confirmed">立即确认（清仓）</Radio.Button>
                </Radio.Group>
              </Form.Item>
              <Form.Item class="!mb-2">
                <DatePicker v-model:value="form.effective" placeholder="生效日（默认最新）" class="!w-40" />
              </Form.Item>
              <Form.Item class="!mb-2">
                <Checkbox v-model:checked="form.new_round">新一轮结构</Checkbox>
              </Form.Item>
              <Form.Item class="!mb-2"><Input v-model:value="form.note" placeholder="备注" class="!w-40" /></Form.Item>
              <Form.Item class="!mb-2"><Button type="primary" :loading="saving" @click="saveLevel">确认</Button></Form.Item>
            </Form>
            <div v-if="olderLevels.length" class="mt-2 text-xs text-gray-400">
              之前的轮次：{{ olderLevels.map((l) => `第 ${l.round_no} 轮${LEVEL_NAMES[l.kind]} ${px(l.price)}`).join('；') }}
            </div>
          </Card>
        </Col>
        <Col :lg="10" :xs="24">
          <Card size="small" title="剧本">
            <template v-if="item.completion !== null">
              <Descriptions :column="2" size="small">
                <Descriptions.Item label="结构">
                  <Tag :color="PHASE[item.phase ?? '']?.color">{{ item.phase_name }}</Tag>
                  <span v-if="detail.activated_on" class="text-xs text-gray-500">{{ detail.activated_on }} 激活</span>
                </Descriptions.Item>
                <Descriptions.Item label="剧本仓位">{{ pct(item.weight, 0) }}</Descriptions.Item>
                <Descriptions.Item label="完成度" :span="2">
                  <Progress :percent="progress(item.completion)" size="small" :format="() => pct(item!.completion, 0)" />
                </Descriptions.Item>
                <Descriptions.Item label="在等什么" :span="2">{{ item.waiting_for }}</Descriptions.Item>
                <Descriptions.Item label="主指数" :span="2">
                  {{ item.main_index_name ?? item.main_index }}
                  <Tag v-if="detail.index_today" class="ml-1" :color="labelColor(detail.index_today.label)">{{ labelName(detail.index_today.label) }}</Tag>
                  <span v-if="detail.index_today?.completion !== null && detail.index_today?.completion !== undefined" class="text-xs text-gray-500">
                    完成度 {{ pct(detail.index_today.completion, 0) }}
                  </span>
                </Descriptions.Item>
                <Descriptions.Item label="本轮收益">{{ pct(item.round_return, 1, true) }}</Descriptions.Item>
                <Descriptions.Item label="颈线→目标">{{ pct(item.script_return, 1, true) }}</Descriptions.Item>
              </Descriptions>
              <Table :columns="ladderColumns" :data-source="detail.ladder" row-key="completion" size="small" :pagination="false" class="mt-2">
                <template #bodyCell="{ column, record }">
                  <template v-if="column.key === 'completion'">{{ record.completion >= 1 ? '目标' : pct(record.completion, 0) }}</template>
                  <template v-else-if="column.key === 'price'">{{ px(record.price) }}</template>
                  <template v-else-if="column.key === 'keep'">{{ pct(record.keep, 0) }}{{ record.completion >= 1 ? '（按主指数）' : '' }}</template>
                  <template v-else-if="column.key === 'done'">
                    <Tag v-if="record.done_on" color="green">{{ record.done_on }}</Tag><span v-else class="text-gray-400">未到</span>
                  </template>
                </template>
              </Table>
            </template>
            <Empty v-else :description="item.waiting_for" />
          </Card>
        </Col>
      </Row>

      <Row :gutter="12" class="mt-3">
        <Col :lg="14" :xs="24">
          <Card size="small" title="规则事件">
            <Alert v-if="item.label === 'undecided' || item.label === 'left'" class="mb-2" show-icon type="info"
                   :message="`${labelName(item.label)}：规则休眠，确认右侧后才会入场`" />
            <Timeline v-if="detail.events.length">
              <Timeline.Item v-for="(e, k) in detail.events" :key="k" :color="PRIORITY[e.priority]?.color === 'default' ? 'gray' : PRIORITY[e.priority]?.color">
                <span class="text-gray-500">{{ e.trade_date }}</span>
                <Tag class="ml-2" :color="PRIORITY[e.priority]?.color">{{ e.priority_name }}</Tag>
                {{ e.message }}
                <span class="text-xs text-gray-400">收盘 {{ px(e.close) }} · 仓位 {{ pct(e.weight, 0) }}</span>
              </Timeline.Item>
            </Timeline>
            <Empty v-else description="还没有事件" />
          </Card>
          <Card v-if="detail.segments.length" size="small" title="剧本交易段" class="mt-3">
            <Table :columns="segmentColumns" :data-source="detail.segments" row-key="entry_date" size="small" :pagination="false">
              <template #bodyCell="{ column, record }">
                <template v-if="column.key === 'entry'">{{ px(record.entry_close) }} / {{ pct(record.entry_weight, 0) }}</template>
                <template v-else-if="column.key === 'exit'">{{ record.exit_date ? `${record.exit_date} ${px(record.exit_close)}` : `持有中（${px(record.exit_close)}）` }}</template>
                <template v-else-if="column.key === 'return'">{{ pct(record.return, 1, true) }}</template>
              </template>
            </Table>
          </Card>
        </Col>
        <Col :lg="10" :xs="24">
          <Card size="small" title="筑底提示">
            <div v-for="(p, k) in detail.prompts" :key="k" class="mb-1 text-sm">
              <span class="text-gray-500">{{ p.trade_date }}</span> {{ p.message }}
            </div>
            <Empty v-if="!detail.prompts.length" :description="item.label === 'base' ? '筑底期间还没有命中判据' : '标签为筑底时才检查筑底判据'" />
          </Card>
          <Card size="small" title="标签历史" class="mt-3">
            <div v-for="(h, k) in detail.label_history" :key="k" class="mb-1 text-sm">
              <span class="text-gray-500">{{ h.effective_date }}</span>
              <Tag class="ml-2" :color="labelColor(h.label)">{{ h.label_name }}</Tag>
              {{ h.source === 'system' ? '采纳系统观点' : '手工' }} · {{ h.created_by }}
              <div v-if="h.reason" class="text-xs text-gray-400">{{ h.reason }}</div>
            </div>
            <Empty v-if="!detail.label_history.length" description="还没有设置过标签" />
          </Card>
        </Col>
      </Row>
    </template>
  </Page>
</template>
