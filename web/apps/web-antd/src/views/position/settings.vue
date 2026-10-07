<script lang="ts" setup>
import type { PmSettings } from '#/api';

import { computed, onMounted, reactive, ref } from 'vue';

import { Page } from '@vben/common-ui';

import { Alert, Button, Card, Col, Collapse, Form, InputNumber, message, Radio, Row, Space, Switch, Tooltip } from 'ant-design-vue';

import { pmSaveSettingsApi, pmSettingsApi } from '#/api';

interface Field { help?: string; key: string; kind: 'int' | 'pct'; name: string; step?: number }

const STAGE_MAIN: Field[] = [
  { key: 'ma', kind: 'int', name: '均线周期', help: '判断方向的长期均线（交易日）' },
  { key: 'slope', kind: 'int', name: '斜率回看', help: '均线在多少个交易日内的涨跌算方向' },
  { key: 'flat', kind: 'pct', name: '个股走平阈值', step: 0.1, help: '均线涨跌在 ±阈值内算走平' },
  { key: 'index_flat', kind: 'pct', name: '指数走平阈值', step: 0.1, help: '指数波动小，单独设阈值' },
  { key: 'band', kind: 'pct', name: '均线附近区间', step: 0.5, help: '收盘在均线 ±区间内算贴近均线' },
  { key: 'confirm', kind: 'int', name: '确认天数', help: '新阶段持续这么多天才切换，避免来回跳' },
];
const STAGE_BASE: Field[] = [
  { key: 'context', kind: 'int', name: '首段判断回看', help: '判断之前是否有过一段上涨' },
  { key: 'rise', kind: 'pct', name: '首段涨幅', step: 5 },
  { key: 'dry_window', kind: 'int', name: '缩量窗口（日）', help: '判据②：近期这么多天的均量' },
  { key: 'dry_compare', kind: 'int', name: '对比窗口（日）', help: '判据②：与之前这么多天的均量对比' },
  { key: 'dry_ratio', kind: 'pct', name: '缩量比例', step: 5, help: '近期均量低于对比期的这个比例算缩量' },
  { key: 'surge_multiple', kind: 'int', name: '放量倍数', step: 0.1, help: '阳线当天成交量是缩量期均量的倍数' },
  { key: 'rebound', kind: 'pct', name: '回踩反弹', step: 0.5, help: '判据③：回踩后反弹的幅度' },
];
const RULES: Field[] = [
  { key: 'trailing', kind: 'pct', name: '移动止盈回撤', step: 1, help: '自持有期最高收盘回撤这么多，清仓' },
  { key: 'trailing_after', kind: 'pct', name: '止盈启用完成度', step: 5, help: '完成度曾达到这个值后才启用移动止盈' },
  { key: 'false_break', kind: 'pct', name: '假突破线（颈线的）', step: 1, help: '收盘跌破颈线 × 这个比例，清仓' },
  { key: 'late_index', kind: 'pct', name: '指数晚期完成度', step: 5, help: '主指数完成度达到这个值后只建半仓' },
  { key: 'late_weight', kind: 'pct', name: '指数晚期仓位', step: 5 },
  { key: 'final_index_high', kind: 'pct', name: '到目标留仓·指数 ≥90%', step: 5 },
  { key: 'final_index_mid', kind: 'pct', name: '到目标留仓·指数 70–90%', step: 5 },
  { key: 'final_index_low', kind: 'pct', name: '到目标留仓·指数 <70%', step: 5 },
  { key: 'final_index_exhausted', kind: 'pct', name: '到目标留仓·指数已衰竭', step: 5 },
  { key: 'realized', kind: 'pct', name: '已兑现（峰值完成度）', step: 5 },
  { key: 'exhausted', kind: 'pct', name: '已衰竭（自峰值回撤）', step: 1 },
  { key: 'top_watch', kind: 'pct', name: '顶部观察提示完成度', step: 5 },
  { key: 'confirm_closes', kind: 'int', name: '站上颈线的收盘数', help: '连续这么多个收盘在颈线上方才入场' },
];

const settings = ref<PmSettings>();
const saving = ref(false);
const form = reactive<{ auto_base: boolean; crowd_high: number; crowd_low: number; label_mode: 'manual' | 'suggest'; ladder: number[][];
                        preset: string; push_daily: boolean; rules: Record<string, number>; stage: Record<string, number> }>({
  auto_base: true, crowd_high: 1.8, crowd_low: 1.4, label_mode: 'suggest', ladder: [], preset: 'steady', push_daily: false,
  rules: {}, stage: {},
});
const presetValues = computed(() => settings.value?.presets.find((p) => p.key === form.preset)?.params ?? {});

function shown(field: Field, value: number | undefined): number | undefined {
  if (value === undefined || value === null) return undefined;
  return field.kind === 'pct' ? Math.round(value * 10_000) / 100 : value;
}
function stored(field: Field, value: null | number): number | undefined {
  if (value === null || value === undefined) return undefined;
  return field.kind === 'pct' ? value / 100 : value;
}
function differs(a?: number, b?: number): boolean {
  return a !== undefined && b !== undefined && Math.abs(a - b) > 1e-9;
}

function fill(s: PmSettings) {
  settings.value = s;
  form.label_mode = s.label_mode;
  form.push_daily = s.push_daily;
  form.auto_base = s.auto_base;
  form.crowd_high = s.crowd_high;
  form.crowd_low = s.crowd_low;
  form.preset = s.stage_preset;
  form.stage = { ...s.stage_params };
  form.rules = { ...s.rule_params };
  form.ladder = (s.rule_params.ladder as number[][]).map((r) => [...r]);
}

function choosePreset(key: string) {
  form.preset = key;
  form.stage = { ...(settings.value?.presets.find((p) => p.key === key)?.params ?? {}) };
}

async function save() {
  if (!settings.value) return;
  const stageOverrides = Object.fromEntries([...STAGE_MAIN, ...STAGE_BASE]
    .filter((f) => differs(form.stage[f.key], presetValues.value[f.key])).map((f) => [f.key, form.stage[f.key]!]));
  const defaults = settings.value.rule_defaults;
  const ruleOverrides: Record<string, any> = Object.fromEntries(RULES
    .filter((f) => differs(form.rules[f.key], defaults[f.key])).map((f) => [f.key, form.rules[f.key]]));
  if (JSON.stringify(form.ladder) !== JSON.stringify(defaults.ladder)) ruleOverrides.ladder = form.ladder;
  if (!(form.crowd_low < form.crowd_high)) {
    message.error('行业拥挤提醒：解除线要低于提醒线');
    return;
  }
  saving.value = true;
  try {
    fill(await pmSaveSettingsApi({ auto_base: form.auto_base, crowd_high: form.crowd_high, crowd_low: form.crowd_low,
                                   label_mode: form.label_mode, push_daily: form.push_daily,
                                   rule_params: ruleOverrides,
                                   stage_params: stageOverrides, stage_preset: form.preset }));
    message.success('已保存；看板和提示按新参数重新计算');
  } finally {
    saving.value = false;
  }
}

onMounted(async () => fill(await pmSettingsApi()));
</script>

<template>
  <Page title="规则设置" description="只影响你自己的标的库；默认值取自方法论文档">
    <template #extra><Button type="primary" :loading="saving" @click="save">保存</Button></template>
    <template v-if="settings">
      <Card size="small" title="标签与推送">
        <Form :label-col="{ style: { width: '120px' } }">
          <Form.Item label="标签模式">
            <Radio.Group v-model:value="form.label_mode">
              <Radio value="suggest">系统给观点，我确认标签（可一键采纳）</Radio>
              <Radio value="manual">纯手工：不显示系统观点</Radio>
            </Radio.Group>
          </Form.Item>
          <Form.Item label="左侧→筑底">
            <Space>
              <Switch v-model:checked="form.auto_base" />
              <span class="text-xs text-gray-500">标签为左侧、收盘进入你画的起涨区时，自动转为筑底并提示（唯一的自动改标签；哨兵只提醒，不改标签）</span>
            </Space>
          </Form.Item>
          <Form.Item label="行业拥挤提醒">
            <Space wrap>
              <span class="text-xs">拥挤度升破</span>
              <InputNumber v-model:value="form.crowd_high" :min="0.6" :max="5" :step="0.1" size="small" class="!w-20" />
              <span class="text-xs">时提醒，回落到</span>
              <InputNumber v-model:value="form.crowd_low" :min="0.5" :max="4.9" :step="0.1" size="small" class="!w-20" />
              <span class="text-xs">以下时提示解除</span>
              <span class="text-xs text-gray-500">只对个股：按它所属的申万一级行业（钱去哪地图）；持有时提示减仓，未持有时只作提示</span>
            </Space>
          </Form.Item>
          <Form.Item label="每日推送" class="!mb-0">
            <Space>
              <Switch v-model:checked="form.push_daily" />
              <span class="text-xs text-gray-500">每晚数据更新后，有新提示时发一条只含条数的消息到外部通知渠道（不含证券名称，详情在仓位看板）</span>
            </Space>
          </Form.Item>
        </Form>
      </Card>

      <Card size="small" title="四阶段（系统观点）" class="mt-3">
        <template #extra>
          <Radio.Group :value="form.preset" button-style="solid" size="small" @change="(e: any) => choosePreset(e.target.value)">
            <Radio.Button v-for="p in settings.presets" :key="p.key" :value="p.key">{{ p.name }}</Radio.Button>
          </Radio.Group>
        </template>
        <Row :gutter="[16, 8]">
          <Col v-for="f in STAGE_MAIN" :key="f.key" :lg="8" :xs="12">
            <Tooltip :title="f.help">
              <div class="mb-1 text-sm" :class="differs(form.stage[f.key], presetValues[f.key]) ? 'font-medium text-blue-600' : ''">
                {{ f.name }} <span class="text-xs text-gray-400">预设 {{ shown(f, presetValues[f.key]) }}{{ f.kind === 'pct' ? '%' : '' }}</span>
              </div>
            </Tooltip>
            <InputNumber :value="shown(f, form.stage[f.key])" :step="f.step ?? 1" class="!w-full"
                         :addon-after="f.kind === 'pct' ? '%' : undefined"
                         @change="(v: any) => (form.stage[f.key] = stored(f, v)!)" />
          </Col>
        </Row>
        <Collapse ghost class="mt-2">
          <Collapse.Panel key="base" header="筑底判据参数">
            <Row :gutter="[16, 8]">
              <Col v-for="f in STAGE_BASE" :key="f.key" :lg="8" :xs="12">
                <Tooltip :title="f.help">
                  <div class="mb-1 text-sm" :class="differs(form.stage[f.key], presetValues[f.key]) ? 'font-medium text-blue-600' : ''">
                    {{ f.name }} <span class="text-xs text-gray-400">预设 {{ shown(f, presetValues[f.key]) }}{{ f.kind === 'pct' ? '%' : '' }}</span>
                  </div>
                </Tooltip>
                <InputNumber :value="shown(f, form.stage[f.key])" :step="f.step ?? 1" class="!w-full"
                             :addon-after="f.kind === 'pct' ? '%' : undefined"
                             @change="(v: any) => (form.stage[f.key] = stored(f, v)!)" />
              </Col>
            </Row>
          </Collapse.Panel>
        </Collapse>
      </Card>

      <Card size="small" title="结构规则" class="mt-3">
        <Alert class="mb-3" show-icon type="info" message="改动后，已有结构的规则事件按新参数重新回放；已经记下的提示不会删除，同一事件不会重复提示。" />
        <div class="mb-2 text-sm font-medium">阶梯减仓</div>
        <Space wrap class="mb-3">
          <span v-for="(rung, k) in form.ladder" :key="k" class="mr-4">
            完成度 ≥
            <InputNumber :value="Math.round(rung[0]! * 100)" :min="1" :max="99" size="small" class="!w-16"
                         @change="(v: any) => (rung[0] = v / 100)" />%
            留
            <InputNumber :value="Math.round(rung[1]! * 100)" :min="0" :max="100" size="small" class="!w-16"
                         @change="(v: any) => (rung[1] = v / 100)" />%
          </span>
          <span class="text-xs text-gray-400">默认 {{ (settings.rule_defaults.ladder as number[][]).map((r) => `${r[0]! * 100}%→${r[1]! * 100}%`).join('，') }}</span>
        </Space>
        <Row :gutter="[16, 8]">
          <Col v-for="f in RULES" :key="f.key" :lg="6" :md="8" :xs="12">
            <Tooltip :title="f.help">
              <div class="mb-1 text-sm" :class="differs(form.rules[f.key], settings.rule_defaults[f.key]) ? 'font-medium text-blue-600' : ''">
                {{ f.name }} <span class="text-xs text-gray-400">默认 {{ shown(f, settings.rule_defaults[f.key]) }}{{ f.kind === 'pct' ? '%' : '' }}</span>
              </div>
            </Tooltip>
            <InputNumber :value="shown(f, form.rules[f.key])" :step="f.step ?? 1" class="!w-full"
                         :addon-after="f.kind === 'pct' ? '%' : undefined"
                         @change="(v: any) => (form.rules[f.key] = stored(f, v)!)" />
          </Col>
        </Row>
      </Card>
    </template>
  </Page>
</template>
