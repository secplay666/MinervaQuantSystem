<script setup lang="ts">
import type { LevelEntry, PmDetail, PmLabel, PmLevelKind, PmSentinel } from './pm-api';

import { computed, onMounted, reactive, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import { showConfirmDialog, showFailToast, showSuccessToast, showToast } from 'vant';

import { del, patch, post, put, request } from '../../api';
import { color, pct } from '../../format';
import DateField from './DateField.vue';
import { BASIS_NAMES, GRADE_COLOR, labelColor, labelName, LEVEL_NAMES, num, PATH_COLOR, PHASE, PM_LABEL, PM_LABELS,
         PRIORITY, progress, px, round2, TAG_COLORS } from './pm-api';
import PmTag from './PmTag.vue';
import Tags from './Tags.vue';

type Basis = 'hfq' | 'none' | 'qfq';
const route = useRoute();
const router = useRouter();
const detail = ref<PmDetail>();
const loading = ref(false);
const labelSheet = ref(false);
const allEvents = ref(false);
const id = computed(() => Number(route.params.id));
const item = computed(() => detail.value?.item);
const isStock = computed(() => item.value?.kind === 'stock');
const roundLevels = computed(() => (detail.value?.levels ?? []).filter((l) => l.round_no === detail.value?.round_no));
const olderLevels = computed(() => (detail.value?.levels ?? []).filter((l) => l.round_no !== detail.value?.round_no));
const events = computed(() => (allEvents.value ? detail.value?.events : detail.value?.events.slice(0, 10)) ?? []);
const canAdopt = computed(() => item.value?.stage && item.value.stage.label !== item.value.label && item.value.stage.stage !== 'unknown');
const labelActions = computed(() => PM_LABELS.map((l) => ({
  color: TAG_COLORS[l.color]![1], key: l.key, name: `${l.name}${item.value?.label === l.key ? '（当前）' : ''}`, subname: l.rule })));
const SOURCE: Record<string, string> = { drawing: '画线', manual: '手工', pattern: '自动形态' };
const KINDS = Object.entries(LEVEL_NAMES) as [PmLevelKind, string][];

// -- levels: typed prices instead of chart drawings ------------------------------------------------
const form = reactive<{ basis: Basis; effective?: string; fraction: string; kind: PmLevelKind; lower: string; new_round: boolean;
                        note: string; open: boolean; price: string; saving: boolean; top_mode: 'confirmed' | 'observe' }>({
  basis: 'qfq', fraction: '50', kind: 'neckline', lower: '', new_round: false, note: '', open: false, price: '', saving: false,
  top_mode: 'observe' });
const isZone = computed(() => form.kind === 'base_zone' || form.kind === 'buyback_zone');

function newLevel(kind: PmLevelKind = 'neckline') {
  Object.assign(form, { basis: 'qfq', effective: undefined, fraction: '50', kind, lower: '', new_round: false, note: '', open: true,
                        price: '', top_mode: 'observe' });
}

async function saveLevel() {
  const price = num(form.price);
  const lower = num(form.lower);
  if (!price) {
    showFailToast(isZone.value ? '请填写上沿' : '请填写价格');
    return;
  }
  if (form.kind === 'buyback_zone' && !lower) {
    showFailToast('回撤买入区须填下沿');
    return;
  }
  if (lower && lower > price) {
    showFailToast('下沿不能高于上沿');
    return;
  }
  const body: LevelEntry = { basis: isStock.value ? form.basis : undefined, kind: form.kind, new_round: form.new_round,
                             note: form.note || undefined, price };
  if (isZone.value && lower) body.lower = lower;
  if (form.kind === 'buyback_zone') body.fraction = Math.min(100, Math.max(1, Number(form.fraction) || 50)) / 100;
  if (form.kind === 'top_neckline') body.top_mode = form.top_mode;
  if (form.effective) body.effective_date = form.effective;
  form.saving = true;
  try {
    detail.value = await post<PmDetail>(`/pm/items/${id.value}/levels`, body);
    showSuccessToast(`${LEVEL_NAMES[form.kind]} ${price} 已确认`);
    form.open = false;
  } catch (error: any) {
    showFailToast(error.message);
  } finally {
    form.saving = false;
  }
}

/** Pre-fill the level form with the path suggestion (design §11.1); the user still confirms. */
function usePath() {
  const path = detail.value?.path;
  if (!path) return;
  newLevel();
  if (path.path === 'A') {
    const [lower, upper] = path.levels.buyback_zone as number[];
    Object.assign(form, { fraction: String(Math.round(path.levels.fraction * 100)), kind: 'buyback_zone',
                          lower: String(round2(lower!)), price: String(round2(upper!)) });
  } else if (path.path === 'B') {
    const [lower, upper] = path.levels.base_zone as number[];
    Object.assign(form, { kind: 'base_zone', lower: String(round2(lower!)), price: String(round2(upper!)) });
  } else {
    Object.assign(form, { kind: 'neckline', new_round: true, price: String(round2(path.levels.neckline)) });
  }
  showToast('已按建议填好，核对后点“确认”');
}

// -- sentinels (design §11.3) ---------------------------------------------------------------------
const sentinel = reactive<{ basis: Basis; direction: 'down' | 'up'; editing?: number; open: boolean; price: string; saving: boolean;
                            source_ref: string }>({ basis: 'qfq', direction: 'up', open: false, price: '', saving: false, source_ref: '手工' });
/** Prices worth a sentinel: the current round's levels, the zone edges, the round's peak and the failure line. */
const sentinelRefs = computed(() => {
  const refs: { label: string; price: number }[] = [];
  for (const lv of roundLevels.value) {
    if (lv.kind === 'base_zone' || lv.kind === 'buyback_zone') {
      refs.push({ label: `${LEVEL_NAMES[lv.kind]}上沿`, price: lv.price });
      if (lv.lower) refs.push({ label: `${LEVEL_NAMES[lv.kind]}下沿`, price: lv.lower });
    } else {
      refs.push({ label: LEVEL_NAMES[lv.kind], price: lv.price });
      if (lv.kind === 'neckline') refs.push({ label: '失效线', price: Math.round(lv.price * 95) / 100 });
    }
  }
  if (detail.value?.peak_close) refs.push({ label: '前高', price: detail.value.peak_close });
  return refs;
});

function sideOf(price?: number): 'down' | 'up' {
  const close = item.value?.close;
  return close != null && price !== undefined && price < close ? 'down' : 'up';
}

function openSentinel(s?: PmSentinel) {
  Object.assign(sentinel, s
    ? { basis: 'qfq', direction: s.direction, editing: s.id, open: true, price: String(s.price), source_ref: s.source_ref ?? '手工' }
    : { basis: 'qfq', direction: 'up', editing: undefined, open: true, price: '', source_ref: '手工' });
}

function pickRef(r: { label: string; price: number }) {
  sentinel.price = String(round2(r.price));
  sentinel.source_ref = r.label;
  sentinel.direction = sideOf(r.price);
}

async function saveSentinel() {
  const price = num(sentinel.price);
  if (!price) {
    showFailToast('请填写价格');
    return;
  }
  const body = { basis: isStock.value ? sentinel.basis : undefined, direction: sentinel.direction, price,
                 source_ref: sentinel.source_ref || undefined };
  sentinel.saving = true;
  try {
    detail.value = sentinel.editing ? await patch<PmDetail>(`/pm/sentinels/${sentinel.editing}`, body)
      : await post<PmDetail>(`/pm/items/${id.value}/sentinels`, body);
    showSuccessToast(sentinel.editing ? '哨兵已重设' : '哨兵已设置');
    sentinel.open = false;
  } catch (error: any) {
    showFailToast(error.message);
  } finally {
    sentinel.saving = false;
  }
}

async function removeSentinel(s: PmSentinel) {
  try {
    await showConfirmDialog({ message: `删除哨兵 ${px(s.price)}？`, title: '删除哨兵' });
  } catch {
    return;
  }
  try {
    detail.value = await del<PmDetail>(`/pm/sentinels/${s.id}`);
  } catch (error: any) {
    showFailToast(error.message);
  }
}

// -- label ----------------------------------------------------------------------------------------
async function setLabel(label?: PmLabel) {
  labelSheet.value = false;
  try {
    detail.value = await put<PmDetail>(`/pm/items/${id.value}/label`, label ? { label } : { use_view: true });
    showSuccessToast(`标签改为 ${detail.value.item.label_name}，从 ${detail.value.item.latest} 起生效`);
  } catch (error: any) {
    showFailToast(error.message);
  }
}

function qualityValue(key: string, dim: { pe?: null | number; period?: null | string; score: null | number; value: any }): string {
  if (dim.value === null || dim.value === undefined) return dim.score === null ? '无数据' : '';
  if (key === 'forecast') return `${dim.value}${dim.period ? `（${String(dim.period).slice(0, 7)}）` : ''}`;
  if (key === 'peg') return `${Number(dim.value).toFixed(2)}${dim.pe ? `（市盈率 ${dim.pe.toFixed(1)}）` : ''}`;
  if (key === 'margin_trend') return `${Number(dim.value) >= 0 ? '+' : ''}${Number(dim.value).toFixed(1)} 个百分点`;
  return pct(Number(dim.value), 1, key !== 'roe');
}

async function load() {
  loading.value = true;
  try {
    detail.value = await request<PmDetail>(`/pm/items/${id.value}`);
  } catch (error: any) {
    showFailToast(error.message);
  } finally {
    loading.value = false;
  }
}

watch(id, (value) => value && load());
onMounted(load);
</script>

<template>
  <van-nav-bar :title="item ? item.name || item.symbol : '标的结构'" left-arrow @click-left="router.back()" />
  <van-pull-refresh v-model="loading" @refresh="load">
    <van-skeleton v-if="!detail" :row="10" style="margin-top: 16px" />
    <template v-if="detail && item">
      <!-- summary -->
      <div class="card">
        <div style="display: flex; justify-content: space-between; align-items: flex-start">
          <div>
            <b style="font-size: 16px">{{ item.name || item.symbol }}</b>
            <span v-if="item.star" style="margin-left: 4px; color: #f59e0b">{{ '★'.repeat(item.star) }}</span>
            <div class="muted">{{ item.symbol }} · {{ item.pool_name }}<template v-if="item.latest"> · {{ item.latest }}</template></div>
          </div>
          <div style="text-align: right">
            <div :style="{ color: color(item.change), fontSize: '20px', fontWeight: 600 }">{{ px(item.close) }}</div>
            <div :style="{ color: color(item.change), fontSize: '12px' }">{{ pct(item.change, 2, true) }}</div>
          </div>
        </div>
        <div style="margin-top: 6px">
          <PmTag :color="labelColor(item.label)">{{ labelName(item.label) }}</PmTag>
          <PmTag v-if="item.phase" :color="PHASE[item.phase]?.color">{{ item.phase_name }}</PmTag>
          <PmTag v-if="detail.quality?.grade" :color="GRADE_COLOR[detail.quality.grade]">质地 {{ detail.quality.grade }}</PmTag>
          <Tags :company="item.company" :holders="item.holders" :industry="item.industry" show-industry />
        </div>
        <div v-if="item.note" class="muted" style="margin-top: 4px">备注：{{ item.note }}</div>
        <van-button type="primary" block icon="chart-trending-o" style="margin-top: 10px" @click="router.push(`/chart/${item.symbol}`)">看 K 线</van-button>
      </div>
      <div class="note" style="margin-top: 0; margin-bottom: 0">价位按前复权显示；系统按后复权保存，除权后自动对齐</div>

      <!-- label -->
      <div class="card">
        <div class="card-title">方向标签</div>
        <div style="display: flex; align-items: center; gap: 8px">
          <PmTag :color="labelColor(item.label)">{{ labelName(item.label) }}</PmTag>
          <span class="muted" style="flex: 1">{{ PM_LABEL[item.label]?.rule }}</span>
          <van-button size="small" @click="labelSheet = true">改标签</van-button>
        </div>
        <div v-if="item.stage" style="margin-top: 8px; font-size: 13px">
          系统观点：<PmTag :color="labelColor(item.stage.label)">{{ item.stage.name }}</PmTag>
          <span class="muted">{{ item.stage.days }} 个交易日</span>
          <div class="muted" style="margin-top: 2px; line-height: 18px">{{ item.stage.reason }}</div>
          <van-button v-if="canAdopt" size="small" type="primary" plain block style="margin-top: 6px" @click="setLabel()">
            采纳系统观点（{{ item.stage.name }}）
          </van-button>
        </div>
        <div v-else-if="detail.label_mode === 'manual'" class="muted" style="margin-top: 6px">手工模式，不显示系统观点</div>
      </div>

      <!-- script -->
      <div class="card">
        <div class="card-title">剧本</div>
        <template v-if="item.completion !== null">
          <div class="pm-kv"><span>结构</span>
            <span><PmTag :color="PHASE[item.phase ?? '']?.color">{{ item.phase_name }}</PmTag>
              <span v-if="detail.activated_on" class="muted">{{ detail.activated_on }} 激活</span></span></div>
          <div class="pm-kv"><span>剧本仓位</span><b>{{ pct(item.weight, 0) }}</b></div>
          <div class="pm-kv"><span>完成度</span>
            <span style="display: flex; align-items: center; gap: 8px; flex: 1; margin-left: 16px">
              <van-progress :percentage="progress(item.completion)" :show-pivot="false" stroke-width="6"
                            :color="item.top_watch ? '#e5484d' : '#1677ff'" style="flex: 1" />{{ pct(item.completion, 0) }}</span></div>
          <div class="pm-kv"><span>在等什么</span><span style="text-align: right">{{ item.waiting_for }}</span></div>
          <div v-if="item.next_price" class="pm-kv"><span>下一步</span><span>{{ item.next_step }} @ {{ px(item.next_price) }}</span></div>
          <div class="pm-kv"><span>主指数</span>
            <span>{{ item.main_index_name ?? item.main_index }}
              <PmTag v-if="detail.index_today" :color="labelColor(detail.index_today.label)" style="margin-left: 4px">{{ labelName(detail.index_today.label) }}</PmTag>
              <span v-if="detail.index_today?.completion !== null && detail.index_today?.completion !== undefined" class="muted">
                完成度 {{ pct(detail.index_today.completion, 0) }}</span></span></div>
          <div class="pm-kv"><span>本轮收益</span><span :style="{ color: color(item.round_return) }">{{ pct(item.round_return, 1, true) }}</span></div>
          <div class="pm-kv"><span>颈线→目标</span><span>{{ pct(item.script_return, 1, true) }}</span></div>
          <div v-if="detail.ladder.length" class="table-wrap" style="margin-top: 8px">
            <table class="table">
              <thead><tr><th>完成度</th><th>价格</th><th>留仓</th><th>状态</th></tr></thead>
              <tbody>
                <tr v-for="r in detail.ladder" :key="r.completion">
                  <td>{{ r.completion >= 1 ? '目标' : pct(r.completion, 0) }}</td>
                  <td>{{ px(r.price) }}</td>
                  <td>{{ pct(r.keep, 0) }}{{ r.completion >= 1 ? '（按主指数）' : '' }}</td>
                  <td><span v-if="r.done_on" style="color: #389e0d">{{ r.done_on }}</span><span v-else class="muted">未到</span></td>
                </tr>
              </tbody>
            </table>
          </div>
        </template>
        <div v-else class="muted">{{ item.waiting_for }}</div>
        <div v-if="item.main_index && item.completion === null" class="muted" style="margin-top: 4px">
          主指数 {{ item.main_index_name ?? item.main_index }}
          <PmTag v-if="detail.index_today" :color="labelColor(detail.index_today.label)" style="margin-left: 4px">{{ labelName(detail.index_today.label) }}</PmTag>
        </div>
      </div>

      <!-- path suggestion -->
      <div v-if="detail.path" class="card">
        <div class="card-title">本轮已走完：建议 <PmTag :color="PATH_COLOR[detail.path.path]">{{ detail.path.path }}</PmTag>{{ detail.path.name }}</div>
        <div style="font-size: 13px; line-height: 20px">{{ detail.path.reason }}</div>
        <div class="muted" style="margin: 6px 0; line-height: 18px">
          <template v-if="detail.path.path === 'A'">录回撤买入区 {{ px(detail.path.levels.buyback_zone[0]) }}–{{ px(detail.path.levels.buyback_zone[1]) }}，收盘进区买回 {{ pct(detail.path.levels.fraction, 0) }}</template>
          <template v-else-if="detail.path.path === 'B'">录起涨区 {{ px(detail.path.levels.base_zone[0]) }}–{{ px(detail.path.levels.base_zone[1]) }}，到了也不买，先等形态；第二轮目标默认前一轮峰值 {{ px(detail.path.levels.target) }}</template>
          <template v-else>录第二轮结构：颈线约为本轮峰值 {{ px(detail.path.levels.neckline) }}，目标画新的量度投射</template>
        </div>
        <van-button type="primary" size="small" block @click="usePath">按建议预填</van-button>
        <div class="muted" style="margin-top: 4px">三条路由你选，预填后还可以改成别的</div>
      </div>

      <!-- levels -->
      <div class="card">
        <div class="card-title" style="display: flex; justify-content: space-between; align-items: center">
          <span>结构（第 {{ detail.round_no }} 轮）</span>
          <van-button size="small" type="primary" icon="plus" @click="newLevel()">录入价位</van-button>
        </div>
        <div v-if="!roundLevels.length" class="muted">还没有确认的价位：点“录入价位”填写颈线、量度目标等价格</div>
        <div v-for="lv in roundLevels" :key="lv.id" class="pm-level">
          <div style="display: flex; justify-content: space-between">
            <span>{{ LEVEL_NAMES[lv.kind] ?? lv.kind }}
              <PmTag v-if="lv.top_mode" :color="lv.top_mode === 'confirmed' ? 'red' : 'orange'" style="margin-left: 4px">
                {{ lv.top_mode === 'confirmed' ? '已确认' : '观察' }}</PmTag>
              <span v-if="lv.fraction" class="muted">买回 {{ pct(lv.fraction, 0) }}</span></span>
            <b>{{ lv.lower ? `${px(lv.lower)} – ` : '' }}{{ px(lv.price) }}</b>
          </div>
          <div class="muted">
            {{ lv.effective_date }} 生效 · v{{ lv.version }} · {{ SOURCE[lv.source] ?? lv.source }} · {{ lv.created_by }}
            <template v-if="lv.basis === 'qfq' && Math.abs(lv.entered_price - lv.price) > 0.005">
              · 确认时输入 {{ lv.entered_price }}，之后有除权已换算</template>
          </div>
          <div v-if="lv.note" class="muted">{{ lv.note }}</div>
        </div>
        <div v-if="olderLevels.length" class="muted" style="margin-top: 6px; line-height: 18px">
          之前的轮次：{{ olderLevels.map((l) => `第 ${l.round_no} 轮${LEVEL_NAMES[l.kind]} ${px(l.price)}`).join('；') }}
        </div>
      </div>

      <!-- sentinels -->
      <div class="card">
        <div class="card-title" style="display: flex; justify-content: space-between; align-items: center">
          <span>哨兵（{{ item.sentinels.length }}/2）</span>
          <van-button v-if="item.sentinels.length < 2" size="small" type="primary" plain icon="plus" @click="openSentinel()">设置哨兵</van-button>
        </div>
        <div v-for="s in item.sentinels" :key="s.id" class="pm-level">
          <div style="display: flex; justify-content: space-between; align-items: center">
            <span style="color: #1677ff">⚓ {{ px(s.price) }} {{ s.direction === 'up' ? '↑站上' : '↓到达' }}
              <span class="muted">{{ s.source_ref ?? '' }}</span></span>
            <span v-if="s.status === 'active'">距 {{ pct(s.distance, 1, true) }}<span v-if="s.days" class="muted"> · 约 {{ s.days }} 天</span></span>
            <PmTag v-else color="blue" style="margin: 0">{{ s.crossed_on }} 已穿越</PmTag>
          </div>
          <div style="display: flex; gap: 8px; margin-top: 6px">
            <van-button size="small" style="flex: 1" @click="openSentinel(s)">{{ s.status === 'active' ? '改价' : '重设' }}</van-button>
            <van-button size="small" style="flex: 1" type="danger" plain @click="removeSentinel(s)">删除</van-button>
          </div>
        </div>
        <div class="muted" style="margin-top: 6px; line-height: 18px">
          哨兵只提醒：收盘穿越时提示你考虑更新标签，不会自动改标签。每个标的最多 2 条。
          <template v-if="detail.daily_range">近 20 日平均振幅 {{ pct(detail.daily_range, 1) }}，“约 N 天”按它折算。</template>
        </div>
      </div>

      <!-- events -->
      <div class="card">
        <div class="card-title">规则事件</div>
        <van-notice-bar v-if="item.label === 'undecided' || item.label === 'left'" wrapable :scrollable="false" left-icon="info-o"
                        :text="`${labelName(item.label)}：规则休眠，确认右侧后才会入场`" style="margin: 0 -12px 8px" />
        <div v-if="!detail.events.length" class="muted">还没有事件</div>
        <div v-for="(e, k) in events" :key="k" class="pm-level">
          <div><span class="muted">{{ e.trade_date }}</span>
            <PmTag :color="PRIORITY[e.priority]?.color" style="margin-left: 6px">{{ e.priority_name }}</PmTag></div>
          <div style="line-height: 18px">{{ e.message }}</div>
          <div class="muted">收盘 {{ px(e.close) }} · 仓位 {{ pct(e.weight, 0) }}</div>
        </div>
        <van-button v-if="detail.events.length > 10" block size="small" style="margin-top: 8px; border: none" @click="allEvents = !allEvents">
          {{ allEvents ? '收起' : `显示全部 ${detail.events.length} 条` }}
        </van-button>
      </div>

      <div v-if="detail.segments.length" class="card">
        <div class="card-title">剧本交易段</div>
        <div v-for="seg in detail.segments" :key="seg.entry_date" class="pm-level">
          <div style="display: flex; justify-content: space-between">
            <span>{{ seg.entry_date }} 入场 {{ px(seg.entry_close) }} / {{ pct(seg.entry_weight, 0) }}</span>
            <b :style="{ color: color(seg.return) }">{{ pct(seg.return, 1, true) }}</b>
          </div>
          <div class="muted">{{ seg.exit_date ? `离场 ${seg.exit_date} ${px(seg.exit_close)}` : `持有中（${px(seg.exit_close)}）` }}</div>
        </div>
      </div>

      <div v-if="detail.quality" class="card">
        <div class="card-title">质地 {{ detail.quality.grade ?? '—' }}{{ detail.quality.score !== null ? ` · ${detail.quality.score} 分` : '' }}</div>
        <div v-for="(dim, key) in detail.quality.dims" :key="key" class="pm-quality">
          <span class="muted" style="width: 72px; flex-shrink: 0">{{ dim.name }}</span>
          <van-progress :percentage="dim.score ?? 0" :show-pivot="false" stroke-width="5" style="width: 72px; flex-shrink: 0" />
          <span class="muted">{{ qualityValue(String(key), dim) }}</span>
        </div>
        <div class="muted" style="margin-top: 4px">数据截至 {{ detail.quality.as_of }}；只用于排序，不触发任何信号。</div>
      </div>

      <div v-if="item.industry" class="card">
        <div class="card-title">行业拥挤度</div>
        <div style="font-size: 13px">
          {{ item.industry.name }}：拥挤度 <b>{{ item.industry.c.toFixed(2) }}</b>
          <PmTag v-if="item.industry.zone === 'crowded'" color="red" style="margin-left: 4px">拥挤</PmTag>
          <span class="muted">{{ item.industry.zone_name ?? '—' }}（{{ item.industry.as_of }}）</span>
        </div>
        <div class="muted" style="margin-top: 4px">升破 {{ item.industry.high }} 提醒，回落到 {{ item.industry.low }} 以下解除；持有时提示减仓，未持有时只作提示。</div>
        <van-button size="small" block style="margin-top: 8px" @click="router.push('/moneymap')">看钱去哪地图</van-button>
      </div>

      <div class="card">
        <div class="card-title">筑底提示</div>
        <div v-for="(p, k) in detail.prompts" :key="k" class="pm-level"><span class="muted">{{ p.trade_date }}</span> {{ p.message }}</div>
        <div v-if="!detail.prompts.length" class="muted">{{ item.label === 'base' ? '筑底期间还没有命中判据' : '标签为筑底时才检查筑底判据' }}</div>
      </div>

      <div class="card">
        <div class="card-title">标签历史</div>
        <div v-for="(h, k) in detail.label_history" :key="k" class="pm-level">
          <span class="muted">{{ h.effective_date }}</span>
          <PmTag :color="labelColor(h.label)" style="margin-left: 6px">{{ h.label_name }}</PmTag>
          {{ h.source === 'system' ? '采纳系统观点' : h.source === 'auto' ? '自动' : '手工' }} · {{ h.created_by }}
          <div v-if="h.reason" class="muted">{{ h.reason }}</div>
        </div>
        <div v-if="!detail.label_history.length" class="muted">还没有设置过标签</div>
      </div>
    </template>
  </van-pull-refresh>

  <van-action-sheet v-model:show="labelSheet" :actions="labelActions" cancel-text="取消" description="方向标签：标签决定哪些规则生效"
                    @select="(a: any) => setLabel(a.key)" />

  <!-- level form -->
  <van-popup v-model:show="form.open" position="bottom" round closeable :style="{ maxHeight: '90%' }">
    <div style="padding: 16px 16px 8px; font-weight: 600">确认{{ LEVEL_NAMES[form.kind] }}</div>
    <div style="padding: 0 16px 8px">
      <van-tag v-for="[key, name] in KINDS" :key="key" :plain="form.kind !== key" type="primary" class="pm-chip" @click="form.kind = key">{{ name }}</van-tag>
    </div>
    <van-cell-group>
      <van-field v-if="isStock" label="价格口径">
        <template #input>
          <van-radio-group v-model="form.basis" direction="horizontal">
            <van-radio v-for="b in ['qfq', 'none', 'hfq']" :key="b" :name="b" style="margin-bottom: 2px">{{ BASIS_NAMES[b] }}</van-radio>
          </van-radio-group>
        </template>
      </van-field>
      <van-field v-if="isZone" v-model="form.lower" type="number" label="下沿" placeholder="价格" />
      <van-field v-model="form.price" type="number" :label="isZone ? '上沿' : '价格'" placeholder="价格" />
      <van-field v-if="form.kind === 'buyback_zone'" v-model="form.fraction" type="digit" label="买回成数" placeholder="1–100">
        <template #extra>%</template>
      </van-field>
      <van-field v-if="form.kind === 'top_neckline'" label="方式">
        <template #input>
          <van-radio-group v-model="form.top_mode">
            <van-radio name="observe" style="margin-bottom: 8px">先观察（跌破再清仓）</van-radio>
            <van-radio name="confirmed">立即确认（清仓）</van-radio>
          </van-radio-group>
        </template>
      </van-field>
      <DateField v-model="form.effective" label="生效日" placeholder="默认最新交易日" :max="item?.latest ?? undefined" />
      <van-cell title="新一轮结构" label="上一轮结构已结束，这是新的一轮">
        <template #right-icon><van-switch v-model="form.new_round" size="22" /></template>
      </van-cell>
      <van-field v-model="form.note" label="备注" placeholder="可不填" maxlength="500" />
    </van-cell-group>
    <div class="note" style="margin: 8px 16px">
      价格按所选口径（{{ isStock ? BASIS_NAMES[form.basis] : '指数和 ETF 不复权' }}）理解，系统换算成后复权保存；默认从最新交易日起生效，规则不回看更早的历史。
    </div>
    <div style="padding: 0 16px 20px">
      <van-button type="primary" block :loading="form.saving" @click="saveLevel">确认</van-button>
    </div>
  </van-popup>

  <!-- sentinel form -->
  <van-popup v-model:show="sentinel.open" position="bottom" round closeable :style="{ maxHeight: '90%' }">
    <div style="padding: 16px 16px 8px; font-weight: 600">{{ sentinel.editing ? '重设哨兵' : '设置哨兵' }}
      <span class="muted" style="font-weight: 400">现价 {{ px(item?.close) }}</span></div>
    <div v-if="sentinelRefs.length" style="padding: 0 16px 8px">
      <div class="muted" style="margin-bottom: 4px">引用（点一下填入）：</div>
      <van-tag v-for="r in sentinelRefs" :key="r.label" plain type="primary" class="pm-chip" @click="pickRef(r)">{{ r.label }} {{ px(r.price) }}</van-tag>
    </div>
    <van-cell-group>
      <van-field v-if="isStock" label="价格口径">
        <template #input>
          <van-radio-group v-model="sentinel.basis" direction="horizontal">
            <van-radio v-for="b in ['qfq', 'none', 'hfq']" :key="b" :name="b" style="margin-bottom: 2px">{{ BASIS_NAMES[b] }}</van-radio>
          </van-radio-group>
        </template>
      </van-field>
      <van-field v-model="sentinel.price" type="number" label="价格" placeholder="价格"
                 @blur="sentinel.direction = sideOf(num(sentinel.price))" />
      <van-field label="方向">
        <template #input>
          <van-radio-group v-model="sentinel.direction" direction="horizontal">
            <van-radio name="up">向上站上</van-radio>
            <van-radio name="down">向下到达</van-radio>
          </van-radio-group>
        </template>
      </van-field>
      <van-field v-model="sentinel.source_ref" label="来源" maxlength="24" placeholder="如 前高、颈线、起涨区上沿" />
    </van-cell-group>
    <div class="note" style="margin: 8px 16px">哨兵只提醒：收盘穿越时提示你考虑更新标签，不改标签。向上的哨兵须高于现价，向下的须低于现价。</div>
    <div style="padding: 0 16px 20px">
      <van-button type="primary" block :loading="sentinel.saving" @click="saveSentinel">{{ sentinel.editing ? '重设' : '设置' }}</van-button>
    </div>
  </van-popup>
</template>

<style>
.pm-kv { display: flex; justify-content: space-between; align-items: center; gap: 12px; padding: 5px 0; font-size: 13px; }
.pm-kv > span:first-child { color: #969799; flex-shrink: 0; }
.pm-level { padding: 8px 0; border-bottom: 1px solid #f5f6f8; font-size: 13px; }
.pm-level:last-of-type { border-bottom: none; }
.pm-quality { display: flex; align-items: center; gap: 8px; padding: 3px 0; font-size: 13px; }
</style>
