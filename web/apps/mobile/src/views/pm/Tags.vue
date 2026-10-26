<script setup lang="ts">
/**
 * 公司在买 / 减持 (buybacks and holder changes of the last 90 days, information only),
 * 户数大增 (大涨后股东户数大增 in the last 60 sessions) and the industry crowding tag.
 * A tap explains the tag (the PC tooltips).
 */
import type { PmCompany, PmHolders, PmIndustry } from './pm-api';

import { useRouter } from 'vue-router';

import { showConfirmDialog, showDialog } from 'vant';

import PmTag from './PmTag.vue';

const props = defineProps<{ company?: PmCompany; holders?: PmHolders; industry?: null | PmIndustry; showIndustry?: boolean }>();
const router = useRouter();
const signed = (v: number) => `${v > 0 ? '+' : ''}${Math.round(v * 100)}%`;

function explainCompany() {
  const c = props.company!;
  showConfirmDialog({
    cancelButtonText: '关闭', confirmButtonText: '看回购增持列表',
    message: [`近 ${c.days} 天的公告（只是信息，不是信号）：`, ...c.notes].join('\n'), messageAlign: 'left', title: '公司动作',
  }).then(() => router.push('/company-actions')).catch(() => undefined);
}

function explainHolders() {
  const h = props.holders!;
  showDialog({
    message: `${h.notice_date} 公告：${h.end_date} 股东户数较上季 ${signed(h.change)}，公告前 120 日涨 ${signed(h.run)}。`
      + '大涨后股东户数大增，历史上之后 60 天多数跑输（样本内统计），宜减仓、不追。',
    messageAlign: 'left', title: '户数大增',
  });
}

function explainIndustry() {
  const i = props.industry!;
  showConfirmDialog({
    cancelButtonText: '关闭', confirmButtonText: '看钱去哪地图',
    message: `${i.name}：拥挤度 ${i.c.toFixed(2)}，${i.zone_name ?? '—'}（${i.as_of}）。升破 ${i.high} 提醒，回落到 ${i.low} 以下解除。`,
    messageAlign: 'left', title: '行业拥挤度',
  }).then(() => router.push('/moneymap')).catch(() => undefined);
}
</script>

<template>
  <span v-if="company && (company.buying || company.reducing)" class="pm-tap" @click.stop="explainCompany">
    <PmTag v-if="company.buying" color="blue">公司在买</PmTag>
    <PmTag v-if="company.reducing" color="orange">减持</PmTag>
  </span>
  <span v-if="holders" class="pm-tap" @click.stop="explainHolders"><PmTag color="red">户数大增</PmTag></span>
  <span v-if="industry && (showIndustry || industry.zone === 'crowded')" class="pm-tap" @click.stop="explainIndustry">
    <PmTag :color="industry.zone === 'crowded' ? 'red' : 'default'">
      {{ industry.name }} {{ industry.c.toFixed(2) }}{{ industry.zone === 'crowded' ? ' 拥挤' : '' }}
    </PmTag>
  </span>
</template>

<style>
/* A larger hit area around the small tags. */
.pm-tap { display: inline-block; padding: 6px 0; }
</style>
