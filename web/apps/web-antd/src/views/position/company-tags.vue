<script lang="ts" setup>
import type { PmRow } from '#/api';

import { useRouter } from 'vue-router';

import { Tag, Tooltip } from 'ant-design-vue';

/** 公司在买 / 减持 under a stock's name: buybacks and holder changes of the last 90 days (information, no signal). */
defineProps<{ company?: PmRow['company'] }>();
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
</template>
