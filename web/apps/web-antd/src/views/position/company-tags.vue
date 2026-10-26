<script lang="ts" setup>
import type { PmRow } from '#/api';

import { useRouter } from 'vue-router';

import { Tag, Tooltip } from 'ant-design-vue';

/** 公司在买 / 减持 under a stock's name: buybacks and holder changes of the last 90 days (information, no signal);
 *  户数大增: the 大涨后股东户数大增 reminder of the last 60 sessions. */
defineProps<{ company?: PmRow['company']; holders?: PmRow['holders'] }>();
const pct = (v: number) => `${v > 0 ? '+' : ''}${Math.round(v * 100)}%`;
const router = useRouter();
</script>

<template>
  <Tooltip v-if="company && (company.buying || company.reducing)" placement="right">
    <template #title>
      <div>近 {{ company.days }} 天的公告（只是信息，不是信号）：</div>
      <div v-for="note in company.notes" :key="note">{{ note }}</div>
      <div class="opacity-70">点击看回购增持列表</div>
    </template>
    <span class="cursor-pointer" @click="router.push('/company-actions')">
      <Tag v-if="company.buying" color="blue" class="!mr-1 !px-1 !text-xs !leading-4">公司在买</Tag>
      <Tag v-if="company.reducing" color="orange" class="!mr-1 !px-1 !text-xs !leading-4">减持</Tag>
    </span>
  </Tooltip>
  <Tooltip v-if="holders" placement="right">
    <template #title>
      <div>{{ holders.notice_date }} 公告：{{ holders.end_date }} 股东户数较上季 {{ pct(holders.change) }}，公告前 120 日涨 {{ pct(holders.run) }}。</div>
      <div>大涨后股东户数大增，历史上之后 60 天多数跑输（样本内统计），宜减仓、不追。</div>
    </template>
    <Tag color="red" class="!mr-1 !px-1 !text-xs !leading-4">户数大增</Tag>
  </Tooltip>
</template>
