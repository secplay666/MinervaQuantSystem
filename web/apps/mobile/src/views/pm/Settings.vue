<script setup lang="ts">
import type { PmSettings } from './pm-api';

import { computed, onMounted, reactive, ref } from 'vue';
import { useRouter } from 'vue-router';

import { showFailToast, showSuccessToast } from 'vant';

import { put, request } from '../../api';
import './pm.css';

interface Field { help?: string; key: string; kind: 'int' | 'pct'; name: string }

const STAGE_MAIN: Field[] = [
  { key: 'ma', kind: 'int', name: '均线周期', help: '判断方向的长期均线（交易日）' },
  { key: 'slope', kind: 'int', name: '斜率回看', help: '均线在多少个交易日内的涨跌算方向' },
  { key: 'flat', kind: 'pct', name: '个股走平阈值', help: '均线涨跌在 ±阈值内算走平' },
  { key: 'index_flat', kind: 'pct', name: '指数走平阈值', help: '指数波动小，单独设阈值' },
  { key: 'band', kind: 'pct', name: '均线附近区间', help: '收盘在均线 ±区间内算贴近均线' },
  { key: 'confirm', kind: 'int', name: '确认天数', help: '新阶段持续这么多天才切换，避免来回跳' },
];
const STAGE_BASE: Field[] = [
  { key: 'context', kind: 'int', name: '首段判断回看', help: '判断之前是否有过一段上涨' },
  { key: 'rise', kind: 'pct', name: '首段涨幅' },
  { key: 'dry_window', kind: 'int', name: '缩量窗口（日）', help: '判据②：近期这么多天的均量' },
  { key: 'dry_compare', kind: 'int', name: '对比窗口（日）', help: '判据②：与之前这么多天的均量对比' },
  { key: 'dry_ratio', kind: 'pct', name: '缩量比例', help: '近期均量低于对比期的这个比例算缩量' },
  { key: 'surge_multiple', kind: 'int', name: '放量倍数', help: '阳线当天成交量是缩量期均量的倍数' },
  { key: 'rebound', kind: 'pct', name: '回踩反弹', help: '判据③：回踩后反弹的幅度' },
];
const RULES: Field[] = [
  { key: 'trailing', kind: 'pct', name: '移动止盈回撤', help: '自持有期最高收盘回撤这么多，清仓' },
  { key: 'trailing_after', kind: 'pct', name: '止盈启用完成度', help: '完成度曾达到这个值后才启用移动止盈' },
  { key: 'false_break', kind: 'pct', name: '假突破线（颈线的）', help: '收盘跌破颈线 × 这个比例，清仓' },
  { key: 'late_index', kind: 'pct', name: '指数晚期完成度', help: '主指数完成度达到这个值后只建半仓' },
  { key: 'late_weight', kind: 'pct', name: '指数晚期仓位' },
  { key: 'final_index_high', kind: 'pct', name: '到目标留仓·指数 ≥90%' },
  { key: 'final_index_mid', kind: 'pct', name: '到目标留仓·指数 70–90%' },
  { key: 'final_index_low', kind: 'pct', name: '到目标留仓·指数 <70%' },
  { key: 'final_index_exhausted', kind: 'pct', name: '到目标留仓·指数已衰竭' },
  { key: 'realized', kind: 'pct', name: '已兑现（峰值完成度）' },
  { key: 'exhausted', kind: 'pct', name: '已衰竭（自峰值回撤）' },
  { key: 'top_watch', kind: 'pct', name: '顶部观察提示完成度' },
  { key: 'confirm_closes', kind: 'int', name: '站上颈线的收盘数', help: '连续这么多个收盘在颈线上方才入场' },
];

const router = useRouter();
const settings = ref<PmSettings>();
const saving = ref(false);
const open = ref<string[]>(['general']);
// Inputs are kept as typed text (percentages shown as %), converted on save.
const form = reactive({ auto_base: true, crowd_high: '', crowd_low: '', holder_rise: '', holder_run: '',
                        label_mode: 'suggest' as 'manual' | 'suggest', ladder: [] as string[][], preset: 'steady', push_daily: false,
                        rules: {} as Record<string, string>, stage: {} as Record<string, string> });
const presetValues = computed(() => settings.value?.presets.find((p) => p.key === form.preset)?.params ?? {});
const defaults = computed(() => settings.value?.rule_defaults ?? {});

function shown(field: Field, value: null | number | undefined): string {
  if (value === undefined || value === null) return '';
  return String(field.kind === 'pct' ? Math.round(value * 10_000) / 100 : value);
}
function parsed(field: Field, text: string | undefined): number | undefined {
  if (text === undefined || text === '' || !Number.isFinite(Number(text))) return undefined;
  return field.kind === 'pct' ? Number(text) / 100 : Number(text);
}
function differs(field: Field, text: string | undefined, base?: number): boolean {
  const value = parsed(field, text);
  return value !== undefined && base !== undefined && Math.abs(value - base) > 1e-9;
}
const pctText = (v: number) => String(Math.round(v * 100));

function fill(s: PmSettings) {
  settings.value = s;
  Object.assign(form, {
    auto_base: s.auto_base, crowd_high: String(s.crowd_high), crowd_low: String(s.crowd_low),
    holder_rise: pctText(s.holder_rise), holder_run: pctText(s.holder_run), label_mode: s.label_mode,
    ladder: (s.rule_params.ladder as number[][]).map((r) => r.map(pctText)), preset: s.stage_preset, push_daily: s.push_daily,
    rules: Object.fromEntries(RULES.map((f) => [f.key, shown(f, s.rule_params[f.key])])),
    stage: Object.fromEntries([...STAGE_MAIN, ...STAGE_BASE].map((f) => [f.key, shown(f, s.stage_params[f.key])])),
  });
}

function choosePreset(key: string) {
  form.preset = key;
  const params = settings.value?.presets.find((p) => p.key === key)?.params ?? {};
  form.stage = Object.fromEntries([...STAGE_MAIN, ...STAGE_BASE].map((f) => [f.key, shown(f, params[f.key])]));
}

async function save() {
  if (!settings.value) return;
  const stageOverrides = Object.fromEntries([...STAGE_MAIN, ...STAGE_BASE]
    .filter((f) => differs(f, form.stage[f.key], presetValues.value[f.key])).map((f) => [f.key, parsed(f, form.stage[f.key])!]));
  const ruleOverrides: Record<string, any> = Object.fromEntries(RULES
    .filter((f) => differs(f, form.rules[f.key], defaults.value[f.key])).map((f) => [f.key, parsed(f, form.rules[f.key])]));
  const ladder = form.ladder.map((r) => r.map((v) => Number(v) / 100));
  if (ladder.some((r) => r.some((v) => !Number.isFinite(v)))) {
    showFailToast('阶梯减仓：请填写数字');
    return;
  }
  if (JSON.stringify(ladder) !== JSON.stringify(defaults.value.ladder)) ruleOverrides.ladder = ladder;
  const [high, low] = [Number(form.crowd_high), Number(form.crowd_low)];
  if (!(low < high)) {
    showFailToast('行业拥挤提醒：解除线要低于提醒线');
    return;
  }
  saving.value = true;
  try {
    fill(await put<PmSettings>('/pm/settings', {
      auto_base: form.auto_base, crowd_high: high, crowd_low: low, holder_rise: Number(form.holder_rise) / 100,
      holder_run: Number(form.holder_run) / 100, label_mode: form.label_mode, push_daily: form.push_daily,
      rule_params: ruleOverrides, stage_params: stageOverrides, stage_preset: form.preset,
    }));
    showSuccessToast('已保存；看板和提示按新参数重新计算');
  } catch (error: any) {
    showFailToast(error.message);
  } finally {
    saving.value = false;
  }
}

onMounted(async () => {
  try {
    fill(await request<PmSettings>('/pm/settings'));
  } catch (error: any) {
    showFailToast(error.message);
  }
});
</script>

<template>
  <van-nav-bar title="规则设置" left-arrow @click-left="router.back()">
    <template #right><span style="color: #1677ff" @click="save">保存</span></template>
  </van-nav-bar>
  <div class="note" style="margin-bottom: 8px">只影响你自己的标的库；默认值取自方法论文档。改过的项标蓝。</div>
  <van-skeleton v-if="!settings" :row="10" />
  <template v-else>
    <van-collapse v-model="open">
      <van-collapse-item title="标签与推送" name="general">
        <div class="pm-set-label">标签模式</div>
        <van-radio-group v-model="form.label_mode">
          <van-radio name="suggest" class="pm-set-radio">系统给观点，我确认标签（可一键采纳）</van-radio>
          <van-radio name="manual" class="pm-set-radio">纯手工：不显示系统观点</van-radio>
        </van-radio-group>

        <div class="pm-set-row">
          <span class="pm-set-label">左侧→筑底</span><van-switch v-model="form.auto_base" size="22" />
        </div>
        <div class="muted pm-set-help">标签为左侧、收盘进入你画的起涨区时，自动转为筑底并提示（唯一的自动改标签；哨兵只提醒，不改标签）</div>

        <div class="pm-set-label" style="margin-top: 12px">行业拥挤提醒</div>
        <van-field v-model="form.crowd_high" type="number" label="拥挤度升破" input-align="right" class="pm-set-field">
          <template #extra><span class="pm-unit">时提醒</span></template>
        </van-field>
        <van-field v-model="form.crowd_low" type="number" label="回落到" input-align="right" class="pm-set-field">
          <template #extra><span class="pm-unit">以下解除</span></template>
        </van-field>
        <div class="muted pm-set-help">只对个股：按它所属的申万一级行业（钱去哪地图）；持有时提示减仓，未持有时只作提示</div>

        <div class="pm-set-label" style="margin-top: 12px">股东户数提醒</div>
        <van-field v-model="form.holder_run" type="digit" label="公告前 120 日涨幅 ≥" label-width="11em" input-align="right" class="pm-set-field">
          <template #extra><span class="pm-unit">%</span></template>
        </van-field>
        <van-field v-model="form.holder_rise" type="digit" label="季末户数较上季增加 ≥" label-width="11em" input-align="right" class="pm-set-field">
          <template #extra><span class="pm-unit">%</span></template>
        </van-field>
        <div class="muted pm-set-help">“大涨后股东户数大增”：筹码分散到大量新股东手里。按默认值（50%、20%），2016–2026 年这类公告之后 60 天平均跑输约 4%，近半数跌过 20%（样本内统计）。持有时提示减仓，未持有时只作提示</div>

        <div class="pm-set-row" style="margin-top: 12px">
          <span class="pm-set-label">每日推送</span><van-switch v-model="form.push_daily" size="22" />
        </div>
        <div class="muted pm-set-help">每晚数据更新后，有新提示时发一条只含条数的消息到外部通知渠道（不含证券名称，详情在仓位看板）</div>
      </van-collapse-item>

      <van-collapse-item title="四阶段（系统观点）" name="stage">
        <div style="margin-bottom: 6px">
          <van-tag v-for="p in settings.presets" :key="p.key" :plain="form.preset !== p.key" type="primary" class="pm-chip"
                   @click="choosePreset(p.key)">{{ p.name }}</van-tag>
        </div>
        <van-field v-for="f in STAGE_MAIN" :key="f.key" v-model="form.stage[f.key]" type="number" input-align="right" label-width="60%" class="pm-set-field">
          <template #label>
            <div :class="{ 'pm-changed': differs(f, form.stage[f.key], presetValues[f.key]) }">{{ f.name }}</div>
            <div class="muted">预设 {{ shown(f, presetValues[f.key]) }}{{ f.kind === 'pct' ? '%' : '' }}{{ f.help ? ` · ${f.help}` : '' }}</div>
          </template>
          <template v-if="f.kind === 'pct'" #extra><span class="pm-unit">%</span></template>
        </van-field>
      </van-collapse-item>

      <van-collapse-item title="筑底判据参数" name="base">
        <van-field v-for="f in STAGE_BASE" :key="f.key" v-model="form.stage[f.key]" type="number" input-align="right" label-width="60%" class="pm-set-field">
          <template #label>
            <div :class="{ 'pm-changed': differs(f, form.stage[f.key], presetValues[f.key]) }">{{ f.name }}</div>
            <div class="muted">预设 {{ shown(f, presetValues[f.key]) }}{{ f.kind === 'pct' ? '%' : '' }}{{ f.help ? ` · ${f.help}` : '' }}</div>
          </template>
          <template v-if="f.kind === 'pct'" #extra><span class="pm-unit">%</span></template>
        </van-field>
      </van-collapse-item>

      <van-collapse-item title="结构规则" name="rules">
        <div class="muted pm-set-help" style="margin-bottom: 8px">改动后，已有结构的规则事件按新参数重新回放；已经记下的提示不会删除，同一事件不会重复提示。</div>
        <div class="pm-set-label">阶梯减仓</div>
        <div v-for="(rung, k) in form.ladder" :key="k" class="pm-rung">
          完成度 ≥ <input v-model="rung[0]" type="number" inputmode="numeric" class="pm-input" />%
          留 <input v-model="rung[1]" type="number" inputmode="numeric" class="pm-input" />%
        </div>
        <div class="muted pm-set-help">默认 {{ (defaults.ladder as number[][]).map((r) => `${r[0]! * 100}%→${r[1]! * 100}%`).join('，') }}</div>
        <van-field v-for="f in RULES" :key="f.key" v-model="form.rules[f.key]" type="number" input-align="right" label-width="60%" class="pm-set-field">
          <template #label>
            <div :class="{ 'pm-changed': differs(f, form.rules[f.key], defaults[f.key]) }">{{ f.name }}</div>
            <div class="muted">默认 {{ shown(f, defaults[f.key]) }}{{ f.kind === 'pct' ? '%' : '' }}{{ f.help ? ` · ${f.help}` : '' }}</div>
          </template>
          <template v-if="f.kind === 'pct'" #extra><span class="pm-unit">%</span></template>
        </van-field>
      </van-collapse-item>
    </van-collapse>
    <div style="padding: 16px">
      <van-button type="primary" block :loading="saving" @click="save">保存</van-button>
    </div>
  </template>
</template>

<style>
.pm-set-label { font-size: 14px; color: #323233; }
.pm-set-row { display: flex; justify-content: space-between; align-items: center; margin-top: 12px; min-height: 40px; }
.pm-set-radio { padding: 8px 0; }
.pm-set-help { line-height: 18px; }
.pm-set-field { padding-left: 0; padding-right: 0; }
.pm-unit { margin-left: 4px; color: #646566; }
.pm-changed { color: #1677ff; font-weight: 600; }
.pm-rung { display: flex; align-items: center; gap: 4px; padding: 6px 0; font-size: 14px; }
.pm-input { width: 52px; height: 32px; padding: 0 6px; font-size: 14px; text-align: right; border: 1px solid #dcdee0; border-radius: 4px; }
</style>
