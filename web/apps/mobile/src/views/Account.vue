<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import { request } from '../api';
import { color, pct, yuan } from '../format';

const route = useRoute();
const router = useRouter();
const account = ref<any>();
const loading = ref(false);

async function load() {
  loading.value = true;
  try {
    account.value = await request(`/accounts/${route.params.accountId}`);
  } finally {
    loading.value = false;
  }
}

onMounted(load);
</script>

<template>
  <van-nav-bar :title="account?.name ?? '账户'" left-arrow @click-left="router.back()" />
  <van-pull-refresh v-model="loading" @refresh="load">
    <template v-if="account">
      <van-grid :column-num="3" style="margin-top: 12px">
        <van-grid-item><div class="muted">净值</div><b>{{ yuan(account.nav_fen, 0) }}</b></van-grid-item>
        <van-grid-item><div class="muted">现金</div><b>{{ yuan(account.cash_fen, 0) }}</b></van-grid-item>
        <van-grid-item>
          <div class="muted">相对初始</div>
          <b :style="{ color: color(account.nav_fen - account.initial_cash_fen) }">
            {{ pct(account.initial_cash_fen ? account.nav_fen / account.initial_cash_fen - 1 : null, 2, true) }}
          </b>
        </van-grid-item>
      </van-grid>
      <van-cell-group inset :title="`持仓（${account.holdings.length}）`">
        <van-empty v-if="!account.holdings.length" description="暂无持仓" image-size="60" />
        <van-cell v-for="h in account.holdings" :key="h.symbol" is-link :title="`${h.symbol} ${h.name ?? ''}`"
                  :label="`${h.qty.toLocaleString()} 股 · 现价 ${h.close?.toFixed(2) ?? '—'} · 权重 ${pct(h.weight, 1)}`"
                  @click="router.push(`/instruments/${h.symbol}`)">
          <template #value>
            <div>{{ yuan(h.market_value_fen, 0) }}</div>
            <div :style="{ color: color(h.pnl_fen), fontSize: '12px' }">{{ yuan(h.pnl_fen, 0) }}</div>
          </template>
        </van-cell>
      </van-cell-group>
      <p class="muted" style="text-align: center">持仓录入与成交回填请在电脑端操作</p>
    </template>
  </van-pull-refresh>
</template>
