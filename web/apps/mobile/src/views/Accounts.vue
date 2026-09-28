<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';

import { request } from '../api';
import { yuan } from '../format';

const router = useRouter();
const accounts = ref<any[]>([]);
const loading = ref(false);

async function load() {
  loading.value = true;
  try {
    accounts.value = await request('/accounts');
  } finally {
    loading.value = false;
  }
}

onMounted(load);
</script>

<template>
  <van-nav-bar title="账户" />
  <van-pull-refresh v-model="loading" @refresh="load">
    <van-empty v-if="!accounts.length && !loading" description="暂无账户" />
    <van-cell-group inset style="margin-top: 12px">
      <van-cell v-for="account in accounts" :key="account.account_id" is-link :title="account.name"
                :label="`${account.mode === 'paper' ? '模拟' : '手工'} · ${account.latest ? account.latest.trade_date + ' · ' + account.latest.positions + ' 只' : '暂无快照'}`"
                :value="account.latest ? `${yuan(account.latest.nav_fen, 0)} 元` : '—'"
                @click="router.push(`/accounts/${account.account_id}`)" />
    </van-cell-group>
  </van-pull-refresh>
</template>
