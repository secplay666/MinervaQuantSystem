<script lang="ts" setup>
import type { PmChart, PmLevelKind } from '#/api';

import { computed, reactive, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import { useAccess } from '@vben/access';
import { Page } from '@vben/common-ui';

import { AutoComplete, Button, Checkbox, Form, Input, InputNumber, message, Modal, Radio, Tag, Tooltip } from 'ant-design-vue';

import { indicesApi, instrumentApi, isIndexSymbol, LEVEL_NAMES, PM_LABEL, pmAddItemsApi, pmAddSentinelApi, pmChartApi,
         pmLabelApi, pmLevelApi, pmMoveSentinelApi, searchApi } from '#/api';
import StockChart from '#/components/stock-chart/stock-chart.vue';

const LAST_KEY = 'minerva.chart.last';
const route = useRoute();
const router = useRouter();
const { hasAccessByCodes } = useAccess();
const symbol = computed(() => String(route.query.symbol || localStorage.getItem(LAST_KEY) || '600000'));
const info = ref<Record<string, any>>();
const isIndex = computed(() => isIndexSymbol(symbol.value));
const query = ref('');
const options = ref<{ label: string; value: string }[]>([]);
const canPm = computed(() => hasAccessByCodes(['position:use']));
const pm = ref<null | PmChart>(null);

const BOARD: Record<string, string> = { BSE: '北交所', CHINEXT: '创业板', SSE_MAIN: '沪市主板', STAR: '科创板', SZSE_MAIN: '深市主板' };

async function search(q: string) {
  options.value = q.trim()
    ? (await searchApi(q.trim())).map((r) => ({ label: `${r.symbol} ${r.name}${r.board === 'INDEX' ? '（指数）' : ''}`,
                                               value: r.symbol }))
    : [];
}

/** The chart of ``code`` in this page, or in a new browser tab (Ctrl+Enter, the 新窗口 button). */
function open(code: string, newTab = false) {
  query.value = '';
  options.value = [];
  if (newTab) {
    const href = router.resolve({ path: '/chart', query: { symbol: code } }).href;
    window.open(href, '_blank', 'noopener');
    return;
  }
  router.push({ path: '/chart', query: { symbol: code } });
}

/** Enter picks the highlighted suggestion (AutoComplete); Ctrl+Enter opens it, or the typed code, in a new tab. */
function onKeydown(event: KeyboardEvent) {
  if (event.key !== 'Enter' || !(event.ctrlKey || event.metaKey)) return;
  event.preventDefault();
  event.stopPropagation();
  const typed = query.value.trim();
  const code = options.value[0]?.value ?? (/^(\d{6}|(sh|sz|bj)\d{6}|H\d{5})$/.test(typed) ? typed : '');
  if (code) open(code, true);
}

// -- 仓位管家: the stage view, the label and the structure of this code ---------------------------
async function loadPm(code = symbol.value) {
  pm.value = canPm.value ? await pmChartApi(code).catch(() => null) : null;
}

async function ensureItem(): Promise<null | number> {
  if (pm.value?.item_id) return pm.value.item_id;
  const out = await pmAddItemsApi(symbol.value);
  if (out.errors.length) {
    message.error(out.errors.join('；'));
    return null;
  }
  await loadPm();
  return pm.value?.item_id ?? null;
}

async function addToLibrary() {
  if (await ensureItem()) message.success('已加入标的库');
}

async function adoptView() {
  const id = await ensureItem();
  if (!id) return;
  const detail = await pmLabelApi(id, { use_view: true });
  message.success(`标签改为 ${detail.item.label_name}（采纳系统观点）`);
  await loadPm();
}

const confirm = reactive<{ basis: string; kind: PmLevelKind; lower?: number; new_round: boolean; note: string; open: boolean;
                           pattern: string; price?: number; saving: boolean; target?: number; top_mode: 'confirmed' | 'observe' }>({
  basis: 'qfq', kind: 'neckline', new_round: false, note: '', open: false, pattern: '', saving: false, top_mode: 'observe',
});

function onSetLevel(value: { basis: string; kind: PmLevelKind; lower?: number; price: number }) {
  Object.assign(confirm, { ...value, new_round: false, note: '', open: true, pattern: '', target: undefined, top_mode: 'observe' });
}

function onAdoptPattern(value: { basis: string; name: string; neckline: number; target: number }) {
  Object.assign(confirm, { basis: value.basis, kind: 'neckline', lower: undefined, new_round: false, note: `采纳自动形态：${value.name}`,
                           open: true, pattern: value.name, price: value.neckline, target: value.target });
}

async function saveLevels() {
  if (!confirm.price || (confirm.pattern && !confirm.target)) return;
  confirm.saving = true;
  try {
    const id = await ensureItem();
    if (!id) return;
    const basis = confirm.basis as 'hfq' | 'none' | 'qfq';
    const note = confirm.note || undefined;
    if (confirm.pattern) {
      await pmLevelApi(id, { basis, kind: 'neckline', new_round: confirm.new_round, note, price: confirm.price, source: 'pattern' });
      await pmLevelApi(id, { basis, kind: 'target', note, price: confirm.target!, source: 'pattern' });
    } else {
      await pmLevelApi(id, { basis, kind: confirm.kind, lower: confirm.kind === 'base_zone' ? confirm.lower : undefined,
                             new_round: confirm.new_round, note, price: confirm.price, source: 'drawing',
                             top_mode: confirm.kind === 'top_neckline' ? confirm.top_mode : undefined });
    }
    message.success(confirm.pattern ? '已采纳为结构（颈线和目标）' : `已确认${LEVEL_NAMES[confirm.kind]}`);
    confirm.open = false;
    await loadPm();
  } finally {
    confirm.saving = false;
  }
}

// -- sentinels: dragged lines are saved at once; a new one is confirmed first --------------------------
const sentinelDialog = reactive<{ basis: string; direction: 'down' | 'up'; open: boolean; price?: number; saving: boolean;
                                  source_ref: string }>({ basis: 'qfq', direction: 'up', open: false, saving: false, source_ref: '手工' });

async function onMoveSentinel(value: { basis: string; id: number; price: number }) {
  try {
    await pmMoveSentinelApi(value.id, { basis: value.basis as 'qfq', price: value.price });
    message.success(`哨兵移到 ${value.price}`);
  } finally {
    await loadPm();  // a refused move puts the line back
  }
}

function onSetSentinel(value: { basis: string; price: number }) {
  const close = pm.value?.close;
  Object.assign(sentinelDialog, { basis: value.basis, direction: close != null && value.price < close ? 'down' : 'up',
                                  open: true, price: value.price, source_ref: '手工' });
}

async function saveSentinel() {
  if (!sentinelDialog.price) return;
  sentinelDialog.saving = true;
  try {
    const id = await ensureItem();
    if (!id) return;
    await pmAddSentinelApi(id, { basis: sentinelDialog.basis, direction: sentinelDialog.direction, price: sentinelDialog.price,
                                 source_ref: sentinelDialog.source_ref || undefined });
    message.success('哨兵已设置');
    sentinelDialog.open = false;
    await loadPm();
  } finally {
    sentinelDialog.saving = false;
  }
}

watch(symbol, async (code) => {
  localStorage.setItem(LAST_KEY, code);
  pm.value = null;
  void loadPm(code);
  info.value = isIndexSymbol(code)
    ? (await indicesApi().catch(() => [])).find((i) => i.symbol === code)
    : await instrumentApi(code).catch(() => undefined);
  document.title = `${info.value?.name ?? code}（${code}）· 看盘`;
}, { immediate: true });
</script>

<template>
  <!-- No page header: the stock's name, tags and the search sit in the chart's first toolbar row. -->
  <Page auto-content-height content-class="p-2">
    <StockChart :key="'chart'" :symbol="symbol" :structure="pm" height="100%" @adopt-pattern="onAdoptPattern"
                @move-sentinel="onMoveSentinel" @set-level="onSetLevel" @set-sentinel="onSetSentinel">
      <template #header>
        <b class="text-base">{{ info?.name ?? symbol }}</b>
        <span class="text-muted-foreground">{{ symbol }}</span>
        <Tag v-if="info && isIndex" color="blue" class="mr-0">指数</Tag>
        <template v-else-if="info">
          <Tag class="mr-0">{{ BOARD[info.board] ?? info.board }}</Tag>
          <Tooltip v-if="info.industry" :title="`${info.industry.l1_name} / ${info.industry.l2_name}`">
            <Tag class="mr-0">{{ info.industry.l1_name }}</Tag>
          </Tooltip>
          <Tag v-if="info.risk_status !== 'normal'" color="error" class="mr-0">{{ info.risk_status }}</Tag>
        </template>
        <template v-if="pm">
          <!-- One tag: the user's label (click: the item's structure page) and the system's view (hover: why). -->
          <Tooltip :title="pm.stage ? `系统观点：${pm.stage.name}。${pm.stage.reason}` : '手工模式，不显示系统观点'">
            <Tag :color="PM_LABEL[pm.label ?? pm.stage?.label ?? 'undecided']?.color" class="mr-0"
                 :class="pm.item_id ? 'cursor-pointer' : ''" @click="pm.item_id && router.push(`/position/items/${pm.item_id}`)">
              {{ pm.item_id && pm.label ? PM_LABEL[pm.label]?.name : '未入库' }}{{ pm.stage ? ` · 观点${pm.stage.name}` : '' }}
            </Tag>
          </Tooltip>
          <Button v-if="pm.stage && pm.stage.stage !== 'unknown' && pm.stage.label !== pm.label" size="small" type="link"
                  class="!px-0" @click="adoptView">采纳</Button>
          <Button v-if="!pm.item_id" size="small" type="link" class="!px-0" @click="addToLibrary">入库</Button>
        </template>
        <!-- Capture phase: the select handles Enter itself before a bubbling listener would see it. -->
        <Tooltip title="回车在本页打开，Ctrl+回车在新的浏览器标签打开">
          <span @keydown.capture="onKeydown">
            <AutoComplete v-model:value="query" :options="options" placeholder="代码或名称" size="small" style="width: 170px"
                          @search="search" @select="(v: any) => open(String(v))" />
          </span>
        </Tooltip>
        <Tooltip title="在新的浏览器标签打开这只股票">
          <Button size="small" @click="open(symbol, true)">新窗口</Button>
        </Tooltip>
        <Button v-if="!isIndex" size="small" @click="router.push(`/instruments/${symbol}`)">个股资料</Button>
        <span class="mx-1 h-4 border-l" />
      </template>
    </StockChart>

    <Modal v-model:open="confirm.open" :confirm-loading="confirm.saving" :title="confirm.pattern ? `采纳为结构：${confirm.pattern}` : `确认${LEVEL_NAMES[confirm.kind]}`"
           ok-text="确认" @ok="saveLevels">
      <Form :label-col="{ style: { width: '90px' } }">
        <template v-if="confirm.pattern">
          <Form.Item label="颈线"><InputNumber v-model:value="confirm.price" :min="0" :step="0.01" class="!w-40" /></Form.Item>
          <Form.Item label="量度目标"><InputNumber v-model:value="confirm.target" :min="0" :step="0.01" class="!w-40" /></Form.Item>
        </template>
        <template v-else>
          <Form.Item v-if="confirm.kind === 'base_zone'" label="下沿"><InputNumber v-model:value="confirm.lower" :min="0" :step="0.01" class="!w-40" /></Form.Item>
          <Form.Item :label="confirm.kind === 'base_zone' ? '上沿' : '价格'">
            <InputNumber v-model:value="confirm.price" :min="0" :step="0.01" class="!w-40" />
          </Form.Item>
          <Form.Item v-if="confirm.kind === 'top_neckline'" label="方式">
            <Radio.Group v-model:value="confirm.top_mode">
              <Radio value="observe">先观察（跌破再清仓）</Radio>
              <Radio value="confirmed">立即确认（清仓）</Radio>
            </Radio.Group>
          </Form.Item>
        </template>
        <Form.Item label="新一轮"><Checkbox v-model:checked="confirm.new_round">上一轮结构已结束，这是新的一轮</Checkbox></Form.Item>
        <Form.Item label="备注"><Input v-model:value="confirm.note" :maxlength="500" /></Form.Item>
      </Form>
      <div class="text-xs text-gray-500">
        价格按当前图上的口径（{{ confirm.basis === 'qfq' ? '前复权' : confirm.basis === 'hfq' ? '后复权' : '不复权' }}）理解，系统换算成后复权保存；
        从今天（最新交易日）起生效，规则不回看更早的历史。{{ pm?.item_id ? '' : '这只还不在标的库中，确认时会自动加入。' }}
      </div>
    </Modal>
    <Modal v-model:open="sentinelDialog.open" :confirm-loading="sentinelDialog.saving" title="设置哨兵" ok-text="设置" @ok="saveSentinel">
      <Form :label-col="{ style: { width: '90px' } }">
        <Form.Item label="价格"><InputNumber v-model:value="sentinelDialog.price" :min="0" :step="0.01" class="!w-40" /></Form.Item>
        <Form.Item label="方向">
          <Radio.Group v-model:value="sentinelDialog.direction">
            <Radio value="up">向上站上</Radio>
            <Radio value="down">向下到达</Radio>
          </Radio.Group>
        </Form.Item>
        <Form.Item label="来源"><Input v-model:value="sentinelDialog.source_ref" :maxlength="24" placeholder="如 前高、颈线、起涨区上沿" /></Form.Item>
      </Form>
      <div class="text-xs text-gray-500">哨兵只提醒：收盘穿越时提示你考虑更新标签，不改标签。每个标的最多 2 条；在图上拖动可以改价。</div>
    </Modal>
  </Page>
</template>
