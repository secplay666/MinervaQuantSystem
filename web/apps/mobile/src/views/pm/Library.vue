<script setup lang="ts">
import type { PmDetail, PmLabel, PmRow } from './pm-api';

import { computed, onMounted, reactive, ref, watch } from 'vue';
import { useRouter } from 'vue-router';

import { showConfirmDialog, showFailToast, showSuccessToast, showToast } from 'vant';

import { patch, post, put, request } from '../../api';
import ItemCard from './ItemCard.vue';
import { INDICES, PM_LABELS, POOL_NAMES, POOLS, TAG_COLORS } from './pm-api';

const PAGE = 50;
const router = useRouter();
const rows = ref<PmRow[]>([]);
const groups = ref<string[]>([]);
const labelMode = ref<'manual' | 'suggest'>('suggest');
const loading = ref(false);
const loaded = ref(false);
const limit = ref(PAGE);
const adding = reactive({ busy: false, group: '', open: false, text: '' });
const filter = reactive({ group: '', label: '', pool: '', text: '' });
const sheet = reactive<{ open: '' | 'actions' | 'index' | 'label' | 'star'; row: null | PmRow }>({ open: '', row: null });
const editing = reactive({ groups: [] as string[], newGroup: '', note: '', open: false, primary_index: '', saving: false });

const shown = computed(() => rows.value.filter((r) =>
  (!filter.group || r.groups.includes(filter.group)) && (!filter.label || r.label === filter.label)
  && (!filter.pool || r.pool === filter.pool)
  && (!filter.text || `${r.symbol}${r.name ?? ''}`.includes(filter.text.trim()))));
const groupOptions = computed(() => [{ text: '全部分组', value: '' }, ...groups.value.map((g) => ({ text: g, value: g }))]);
const labelOptions = [{ text: '全部标签', value: '' }, ...PM_LABELS.map((l) => ({ text: l.name, value: l.key }))];
const poolOptions = [{ text: '全部池子', value: '' }, ...POOLS.map((k) => ({ text: POOL_NAMES[k], value: k }))];
const editGroups = computed(() => [...new Set([...groups.value, ...editing.groups])]);

const actions = computed(() => {
  const row = sheet.row;
  if (!row) return [];
  const list: { color?: string; key: string; name: string; subname?: string }[] = [{ key: 'label', name: '改方向标签', subname: `现在：${row.label_name}` }];
  if (row.stage && row.stage.label !== row.label && row.stage.stage !== 'unknown') {
    list.push({ key: 'adopt', name: `采纳系统观点：${row.stage.name}`, subname: row.stage.reason });
  }
  list.push({ key: 'star', name: '星标', subname: row.star ? '★'.repeat(row.star) : '未加星' },
            { key: 'edit', name: '编辑分组、主指数、备注' }, { key: 'chart', name: '看 K 线' },
            { color: '#e5484d', key: 'archive', name: '移出标的库', subname: '结构和历史会保留' });
  return list;
});
const labelActions = computed(() => PM_LABELS.map((l) => ({
  color: TAG_COLORS[l.color]![1], key: l.key, name: `${l.name}${sheet.row?.label === l.key ? '（当前）' : ''}`, subname: l.rule })));
const starActions = [0, 1, 2, 3].map((n) => ({ key: n, name: n ? '★'.repeat(n) : '不加星' }));
const indexActions = INDICES.map((i) => ({ key: i.value, name: i.text }));

async function load() {
  loading.value = true;
  try {
    const out = await request<{ groups: string[]; items: PmRow[]; label_mode: 'manual' | 'suggest' }>('/pm/items');
    rows.value = out.items;
    groups.value = out.groups;
    labelMode.value = out.label_mode;
    loaded.value = true;
  } catch (error: any) {
    showFailToast(error.message);
  } finally {
    loading.value = false;
  }
}

async function add() {
  if (!adding.text.trim()) return;
  adding.busy = true;
  try {
    const out = await post<{ added: string[]; errors: string[]; skipped: string[] }>('/pm/items',
      { group: adding.group.trim() || undefined, text: adding.text });
    const parts = [`添加 ${out.added.length} 个`];
    if (out.skipped.length) parts.push(`已在库中 ${out.skipped.length} 个`);
    if (out.errors.length) parts.push(`无法识别：${out.errors.join('；')}`);
    showToast({ duration: out.errors.length ? 4000 : 2000, message: parts.join('，') });
    adding.text = '';
    adding.open = false;
    await load();
  } catch (error: any) {
    showFailToast(error.message);
  } finally {
    adding.busy = false;
  }
}

function more(row: PmRow) {
  sheet.row = row;
  sheet.open = 'actions';
}

async function setLabel(row: PmRow, label?: PmLabel) {
  try {
    const detail = await put<PmDetail>(`/pm/items/${row.id}/label`, label ? { label } : { use_view: true });
    Object.assign(row, detail.item);
    showSuccessToast(`标签改为 ${detail.item.label_name}${label ? '' : '（采纳系统观点）'}`);
  } catch (error: any) {
    showFailToast(error.message);
  }
}

async function onAction(action: { key: string }) {
  const row = sheet.row!;
  sheet.open = '';
  if (action.key === 'label') sheet.open = 'label';
  else if (action.key === 'star') sheet.open = 'star';
  else if (action.key === 'adopt') await setLabel(row);
  else if (action.key === 'chart') router.push(`/chart/${row.symbol}`);
  else if (action.key === 'edit') {
    Object.assign(editing, { groups: [...row.groups], newGroup: '', note: row.note ?? '', open: true,
                             primary_index: row.main_index && row.kind !== 'index' ? row.main_index : '' });
  } else if (action.key === 'archive') {
    try {
      await showConfirmDialog({ message: `把 ${row.name || row.symbol} 移出标的库？结构和历史会保留，重新添加即可恢复。`, title: '移出标的库' });
    } catch {
      return;
    }
    try {
      await patch(`/pm/items/${row.id}`, { archived: true });
      showSuccessToast('已移出');
      await load();
    } catch (error: any) {
      showFailToast(error.message);
    }
  }
}

async function setStar(action: { key: number }) {
  const row = sheet.row!;
  sheet.open = '';
  try {
    await patch(`/pm/items/${row.id}`, { star: action.key });
    row.star = action.key;
  } catch (error: any) {
    showFailToast(error.message);
  }
}

function toggleGroup(group: string) {
  editing.groups = editing.groups.includes(group) ? editing.groups.filter((g) => g !== group) : [...editing.groups, group];
}

function addGroup() {
  const group = editing.newGroup.trim().slice(0, 32);
  if (group && !editing.groups.includes(group)) editing.groups.push(group);
  editing.newGroup = '';
}

async function saveEdit() {
  const row = sheet.row;
  if (!row) return;
  addGroup();
  editing.saving = true;
  try {
    await patch(`/pm/items/${row.id}`, { groups: editing.groups, note: editing.note, primary_index: editing.primary_index });
    editing.open = false;
    await load();
  } catch (error: any) {
    showFailToast(error.message);
  } finally {
    editing.saving = false;
  }
}

watch(filter, () => (limit.value = PAGE));
onMounted(load);
</script>

<template>
  <van-nav-bar title="标的库" left-arrow @click-left="router.back()">
    <template #right><van-icon name="plus" size="20" @click="adding.open = !adding.open" /></template>
  </van-nav-bar>
  <van-pull-refresh v-model="loading" @refresh="load">
    <div class="note" style="margin-bottom: 0">个股、指数和 ETF；每个人的标的库互相独立。
      {{ labelMode === 'manual' ? '手工模式：不显示系统观点（在“规则设置”里切换）' : '系统给观点，你确认标签' }}</div>

    <div v-if="adding.open || (loaded && !rows.length)" class="card">
      <div class="card-title">添加</div>
      <van-field v-model="adding.text" type="textarea" rows="3" autosize border
                 placeholder="粘贴代码，空格、逗号或换行分隔：600000、600000.SH、sh000300、510300" style="padding: 8px; background: #f7f8fa; border-radius: 4px" />
      <van-field v-model="adding.group" label="放入分组" placeholder="可不填" style="padding: 8px 0" />
      <div v-if="groups.length" style="margin-bottom: 8px">
        <van-tag v-for="g in groups" :key="g" :plain="adding.group !== g" type="primary" class="pm-chip" @click="adding.group = adding.group === g ? '' : g">{{ g }}</van-tag>
      </div>
      <van-button type="primary" block :loading="adding.busy" @click="add">添加</van-button>
    </div>

    <van-search v-model="filter.text" placeholder="代码或名称" shape="round" />
    <van-dropdown-menu>
      <van-dropdown-item v-model="filter.group" :options="groupOptions" />
      <van-dropdown-item v-model="filter.label" :options="labelOptions" />
      <van-dropdown-item v-model="filter.pool" :options="poolOptions" />
    </van-dropdown-menu>
    <div class="muted" style="margin: 8px 16px 0">{{ shown.length }} / {{ rows.length }} 个</div>

    <van-skeleton v-if="!loaded" :row="8" style="margin-top: 16px" />
    <van-empty v-else-if="!shown.length" :description="rows.length ? '没有符合条件的标的' : '标的库是空的：在上面粘贴代码添加'" />
    <ItemCard v-for="row in shown.slice(0, limit)" :key="row.id" :row="row" manage @more="more(row)" @adopt="setLabel(row)" />
    <van-button v-if="shown.length > limit" block style="margin: 0 12px 12px; width: calc(100% - 24px)" @click="limit += PAGE">
      显示更多（还有 {{ shown.length - limit }} 个）
    </van-button>
  </van-pull-refresh>

  <van-action-sheet :show="sheet.open === 'actions'" :actions="actions" cancel-text="取消" close-on-click-action
                    :description="sheet.row ? `${sheet.row.name || sheet.row.symbol} ${sheet.row.symbol}` : ''"
                    @select="onAction" @cancel="sheet.open = ''" @click-overlay="sheet.open = ''" />
  <van-action-sheet :show="sheet.open === 'label'" :actions="labelActions" cancel-text="取消" description="方向标签：标签决定哪些规则生效"
                    @select="(a: any) => { sheet.open = ''; setLabel(sheet.row!, a.key); }" @cancel="sheet.open = ''" @click-overlay="sheet.open = ''" />
  <van-action-sheet :show="sheet.open === 'star'" :actions="starActions" cancel-text="取消" description="星标（同档内排序靠前）"
                    @select="setStar" @cancel="sheet.open = ''" @click-overlay="sheet.open = ''" />
  <van-action-sheet :show="sheet.open === 'index'" :actions="indexActions" cancel-text="取消" description="主指数（入场闸门看它）"
                    @select="(a: any) => { editing.primary_index = a.key; sheet.open = ''; }" @cancel="sheet.open = ''" @click-overlay="sheet.open = ''" />

  <van-popup v-model:show="editing.open" position="bottom" round closeable :style="{ maxHeight: '85%' }">
    <div style="padding: 16px 16px 8px; font-weight: 600">编辑：{{ sheet.row?.name || sheet.row?.symbol }}</div>
    <van-cell-group>
      <van-field label="分组" readonly>
        <template #input>
          <div>
            <van-tag v-for="g in editGroups" :key="g" :plain="!editing.groups.includes(g)" type="primary" class="pm-chip" @click="toggleGroup(g)">{{ g }}</van-tag>
            <span v-if="!editGroups.length" class="muted">还没有分组</span>
          </div>
        </template>
      </van-field>
      <van-field v-model="editing.newGroup" label="新分组" placeholder="输入后点添加" maxlength="32">
        <template #button><van-button size="small" @click="addGroup">添加</van-button></template>
      </van-field>
      <van-field label="主指数" readonly is-link :model-value="INDICES.find((i) => i.value === editing.primary_index)?.text ?? editing.primary_index"
                 @click="sheet.open = 'index'" />
      <van-field v-model="editing.note" label="备注" type="textarea" rows="3" autosize maxlength="500" show-word-limit />
    </van-cell-group>
    <div style="padding: 12px 16px 20px">
      <van-button type="primary" block :loading="editing.saving" @click="saveEdit">保存</van-button>
    </div>
  </van-popup>
</template>
