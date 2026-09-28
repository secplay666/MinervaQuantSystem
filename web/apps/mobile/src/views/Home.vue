<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';

import { request } from '../api';
import { big, color, KIND, pct, RUN, yuan } from '../format';
import { auth, can } from '../store';

const router = useRouter();
const overview = ref<any>();
const accounts = ref<any[]>([]);
const decisions = ref<any[]>([]);
const unread = ref(0);
const loading = ref(false);

async function load() {
  loading.value = true;
  const tasks: Promise<unknown>[] = [];
  if (can('market:view')) tasks.push(request('/market/overview').then((v) => (overview.value = v)));
  if (can('account:view')) tasks.push(request('/accounts').then((v) => (accounts.value = v)));
  if (can('decision:view')) tasks.push(request('/decisions?limit=5').then((v) => (decisions.value = v)));
  if (can('event:view')) tasks.push(request('/events?limit=1').then((v) => (unread.value = v.unread)));
  await Promise.allSettled(tasks);
  loading.value = false;
}

onMounted(load);
</script>

<template>
  <van-nav-bar :title="`你好，${auth.user?.display_name ?? ''}`" />
  <van-pull-refresh v-model="loading" @refresh="load">
    <van-notice-bar v-if="unread" left-icon="bell" mode="link" :text="`${unread} 条未读通知`" @click="router.push('/events')" />
    <van-cell-group v-if="overview" inset title="市场" style="margin-top: 12px">
      <van-grid :column-num="3" :border="false">
        <van-grid-item v-for="index in overview.indices" :key="index.symbol">
          <div class="muted">{{ index.name }}</div>
          <div :style="{ color: color(index.change_pct), fontWeight: 600 }">{{ index.close.toFixed(2) }}</div>
          <div :style="{ color: color(index.change_pct), fontSize: '12px' }">{{ pct(index.change_pct, 2, true) }}</div>
        </van-grid-item>
      </van-grid>
      <van-cell :title="`上涨 ${overview.breadth.up} · 下跌 ${overview.breadth.down}`"
                :value="`涨停 ${overview.breadth.limit_up} / 跌停 ${overview.breadth.limit_down}`"
                :label="`${overview.session} · 成交额 ${big(overview.breadth.amount_cny)}元`" />
    </van-cell-group>
    <van-cell-group v-if="accounts.length" inset title="账户">
      <van-cell v-for="account in accounts" :key="account.account_id" :title="account.name" is-link
                :label="account.mode === 'paper' ? '模拟账户' : '手工账户'" :value="account.latest ? `${yuan(account.latest.nav_fen, 0)} 元` : '—'"
                @click="router.push(`/accounts/${account.account_id}`)" />
    </van-cell-group>
    <van-cell-group v-if="decisions.length" inset title="最近决策">
      <van-cell v-for="run in decisions" :key="run.run_id" is-link :title="`${run.trade_date} ${run.account_id}`"
                :label="KIND[run.kind]" @click="router.push(`/decisions/${run.run_id}`)">
        <template #value>
          <van-tag :type="(RUN[run.status]?.[1] as any)">{{ RUN[run.status]?.[0] }}</van-tag>
          <span v-if="run.summary?.buys !== undefined" style="margin-left: 6px">买{{ run.summary.buys }}/卖{{ run.summary.sells }}</span>
        </template>
      </van-cell>
    </van-cell-group>
  </van-pull-refresh>
</template>
