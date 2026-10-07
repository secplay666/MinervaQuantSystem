<script lang="ts" setup>
import type { PmLabel, PmPool, PmRow } from '#/api';

import { computed, onMounted, reactive, ref } from 'vue';
import { useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';

import { AutoComplete, Button, Card, Form, Input, message, Modal, Popconfirm, Rate, Select, Space, Table, Tag, Tooltip } from 'ant-design-vue';

import { GRADE_COLOR, PM_LABELS, pmAddItemsApi, pmItemsApi, pmLabelApi, pmPatchItemApi, POOL_NAMES } from '#/api';

import { labelColor, openChart } from './common';

const KIND: Record<string, string> = { etf: 'ETF', index: '指数', stock: '个股' };
const INDICES = [
  { label: '默认（按板块和成分）', value: '' }, { label: '上证指数', value: 'sh000001' }, { label: '沪深300', value: 'sh000300' },
  { label: '中证500', value: 'sh000905' }, { label: '中证1000', value: 'sh000852' }, { label: '创业板指', value: 'sz399006' },
  { label: '科创50', value: 'sh000688' }, { label: '上证50', value: 'sh000016' }, { label: '中证A500', value: 'sh000510' },
];

const router = useRouter();
const rows = ref<PmRow[]>([]);
const groups = ref<string[]>([]);
const labelMode = ref<'manual' | 'suggest'>('suggest');
const loading = ref(false);
const adding = reactive({ busy: false, group: '', text: '' });
const filter = reactive<{ group?: string; label?: PmLabel; pool?: PmPool; text: string }>({ text: '' });
const editing = reactive<{ groups: string[]; id?: number; note: string; open: boolean; primary_index: string; title: string }>({
  groups: [], note: '', open: false, primary_index: '', title: '',
});

const shown = computed(() => rows.value.filter((r) =>
  (!filter.group || r.groups.includes(filter.group)) && (!filter.label || r.label === filter.label)
  && (!filter.pool || r.pool === filter.pool)
  && (!filter.text || `${r.symbol}${r.name ?? ''}`.includes(filter.text.trim()))));

const columns = [
  { key: 'item', title: '标的', width: 160 },
  { key: 'kind', title: '类型', width: 60 },
  { key: 'groups', title: '分组', width: 160 },
  { key: 'star', title: '星标', width: 110 },
  { key: 'label', title: '方向标签', width: 200 },
  { key: 'stage', title: '系统观点' },
  { key: 'index', title: '主指数', width: 100 },
  { key: 'quality', title: '质地', width: 60 },
  { key: 'industry', title: '行业拥挤度', width: 120 },
  { key: 'actions', title: '', width: 150 },
];

async function load() {
  loading.value = true;
  try {
    const out = await pmItemsApi();
    rows.value = out.items;
    groups.value = out.groups;
    labelMode.value = out.label_mode;
  } finally {
    loading.value = false;
  }
}

async function add() {
  if (!adding.text.trim()) return;
  adding.busy = true;
  try {
    const out = await pmAddItemsApi(adding.text, adding.group.trim() || undefined);
    const parts = [`添加 ${out.added.length} 个`];
    if (out.skipped.length) parts.push(`已在库中 ${out.skipped.length} 个`);
    if (out.errors.length) parts.push(`无法识别：${out.errors.join('；')}`);
    (out.errors.length ? message.warning : message.success)(parts.join('，'));
    adding.text = '';
    await load();
  } finally {
    adding.busy = false;
  }
}

async function setLabel(row: PmRow, label?: PmLabel) {
  const detail = await pmLabelApi(row.id, label ? { label } : { use_view: true });
  Object.assign(row, detail.item);
  message.success(`${row.name || row.symbol}：标签改为 ${detail.item.label_name}${label ? '' : '（采纳系统观点）'}`);
}

async function setStar(row: PmRow, star: number) {
  await pmPatchItemApi(row.id, { star });
  row.star = star;
}

function edit(row: PmRow) {
  Object.assign(editing, { groups: [...row.groups], id: row.id, note: row.note ?? '', open: true,
                           primary_index: row.main_index && row.kind !== 'index' ? row.main_index : '', title: row.name || row.symbol });
}

async function saveEdit() {
  if (!editing.id) return;
  await pmPatchItemApi(editing.id, { groups: editing.groups, note: editing.note, primary_index: editing.primary_index });
  editing.open = false;
  await load();
}

async function archive(row: PmRow) {
  await pmPatchItemApi(row.id, { archived: true });
  message.success(`${row.name || row.symbol} 已移出标的库（重新添加即可恢复，结构和历史都保留）`);
  await load();
}

onMounted(load);
</script>

<template>
  <Page title="标的库" description="个股、指数和 ETF；每个人的标的库互相独立">
    <Card size="small" title="添加">
      <Form layout="inline">
        <Form.Item class="!mb-2 w-[460px]">
          <Input.TextArea v-model:value="adding.text" :auto-size="{ maxRows: 6, minRows: 2 }"
                          placeholder="粘贴代码，空格、逗号或换行分隔：600000、600000.SH、sh000300、510300" />
        </Form.Item>
        <Form.Item class="!mb-2" label="放入分组">
          <AutoComplete v-model:value="adding.group" class="!w-40" allow-clear :options="groups.map((g) => ({ value: g }))"
                        placeholder="可不填" />
        </Form.Item>
        <Form.Item class="!mb-2">
          <Button type="primary" :loading="adding.busy" @click="add">添加</Button>
        </Form.Item>
      </Form>
    </Card>

    <Card size="small" class="mt-3">
      <template #title>
        <Space wrap>
          <Select v-model:value="filter.group" class="!w-36" allow-clear placeholder="全部分组" :options="groups.map((g) => ({ label: g, value: g }))" />
          <Select v-model:value="filter.label" class="!w-32" allow-clear placeholder="全部标签"
                  :options="PM_LABELS.map((l) => ({ label: l.name, value: l.key }))" />
          <Select v-model:value="filter.pool" class="!w-32" allow-clear placeholder="全部池子"
                  :options="(['hold', 'ready', 'buyback', 'watch'] as const).map((k) => ({ label: POOL_NAMES[k], value: k }))" />
          <Input v-model:value="filter.text" class="!w-40" allow-clear placeholder="代码或名称" />
          <span class="text-xs font-normal text-gray-400">{{ shown.length }} / {{ rows.length }}</span>
        </Space>
      </template>
      <template #extra>
        <span class="text-xs text-gray-400">{{ labelMode === 'manual' ? '手工模式：不显示系统观点（在“规则设置”里切换）' : '系统给观点，你确认标签' }}</span>
      </template>
      <Table :columns="columns" :data-source="shown" :loading="loading" row-key="id" size="small" :pagination="{ pageSize: 50 }">
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'item'">
            <a @click="router.push(`/position/items/${record.id}`)">{{ record.name || record.symbol }}</a>
            <div class="text-xs text-gray-400">{{ record.symbol }}</div>
          </template>
          <template v-else-if="column.key === 'kind'">{{ KIND[record.kind] ?? record.kind }}</template>
          <template v-else-if="column.key === 'groups'">
            <Tag v-for="g in record.groups" :key="g">{{ g }}</Tag>
            <span v-if="!record.groups.length" class="text-gray-300">—</span>
          </template>
          <template v-else-if="column.key === 'star'">
            <Rate :count="3" :value="record.star" class="!text-sm" @change="(v: number) => setStar(record as PmRow, v)" />
          </template>
          <template v-else-if="column.key === 'label'">
            <Select :value="record.label" class="!w-24" size="small" @change="(v: any) => setLabel(record as PmRow, v)">
              <Select.Option v-for="l in PM_LABELS" :key="l.key" :value="l.key">
                <Tooltip :title="l.rule" placement="left"><Tag :color="l.color">{{ l.name }}</Tag></Tooltip>
              </Select.Option>
            </Select>
            <span class="ml-1 text-xs text-gray-400">{{ record.label_source === 'system' ? '采纳' : record.label_source === 'manual' ? '手工' : '' }}</span>
          </template>
          <template v-else-if="column.key === 'stage'">
            <template v-if="record.stage">
              <Tooltip :title="record.stage.reason">
                <Tag :color="labelColor(record.stage.label)">{{ record.stage.name }}</Tag>
                <span class="text-xs text-gray-500">{{ record.stage.days }} 个交易日</span>
              </Tooltip>
              <Button v-if="record.stage.label !== record.label && record.stage.stage !== 'unknown'" size="small" type="link"
                      @click="setLabel(record as PmRow)">采纳</Button>
            </template>
            <span v-else class="text-gray-300">—</span>
          </template>
          <template v-else-if="column.key === 'index'">{{ record.main_index_name ?? record.main_index ?? '—' }}</template>
          <template v-else-if="column.key === 'quality'">
            <Tag v-if="record.quality?.grade" :color="GRADE_COLOR[record.quality.grade]">{{ record.quality.grade }}</Tag>
            <span v-else class="text-gray-300">—</span>
          </template>
          <template v-else-if="column.key === 'industry'">
            <a v-if="record.industry" class="text-xs" :title="`${record.industry.zone_name ?? ''}（${record.industry.as_of}），点击看钱去哪地图`"
               @click="router.push('/moneymap')">
              {{ record.industry.name }} {{ record.industry.c.toFixed(2) }}
              <Tag v-if="record.industry.zone === 'crowded'" color="red" class="ml-1">拥挤</Tag>
            </a>
            <span v-else class="text-gray-300">—</span>
          </template>
          <template v-else-if="column.key === 'actions'">
            <Space :size="4">
              <Button size="small" type="link" @click="openChart(router, record.symbol)">K 线</Button>
              <Button size="small" type="link" @click="edit(record as PmRow)">编辑</Button>
              <Popconfirm title="移出标的库？结构和历史会保留" @confirm="archive(record as PmRow)">
                <Button danger size="small" type="link">移出</Button>
              </Popconfirm>
            </Space>
          </template>
        </template>
      </Table>
    </Card>

    <Modal v-model:open="editing.open" :title="`编辑：${editing.title}`" @ok="saveEdit">
      <Form layout="vertical">
        <Form.Item label="分组">
          <Select v-model:value="editing.groups" mode="tags" :options="groups.map((g) => ({ value: g }))" placeholder="输入后回车新建分组" />
        </Form.Item>
        <Form.Item label="主指数（入场闸门看它）">
          <Select v-model:value="editing.primary_index" :options="INDICES" />
        </Form.Item>
        <Form.Item label="备注">
          <Input.TextArea v-model:value="editing.note" :maxlength="500" :rows="3" />
        </Form.Item>
      </Form>
    </Modal>
  </Page>
</template>
