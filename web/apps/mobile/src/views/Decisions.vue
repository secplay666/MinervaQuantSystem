<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';

import { request } from '../api';
import { KIND, RUN, yuan } from '../format';

const router = useRouter();
const runs = ref<any[]>([]);
const loading = ref(false);

async function load() {
  loading.value = true;
  try {
    runs.value = await request('/decisions?limit=60');
  } finally {
    loading.value = false;
  }
}

onMounted(load);
</script>

<template>
  <van-nav-bar title="每日决策" />
  <van-pull-refresh v-model="loading" @refresh="load">
    <van-empty v-if="!runs.length && !loading" description="暂无决策" />
    <van-cell-group inset style="margin-top: 12px">
      <van-cell v-for="run in runs" :key="run.run_id" is-link :title="`${run.trade_date} · ${run.account_id}`"
                :label="`${KIND[run.kind]}${run.nav_fen ? ' · 净值 ' + yuan(run.nav_fen, 0) : ''}${run.gates.find((g: any) => !g.passed) ? ' · ' + run.gates.find((g: any) => !g.passed).message : ''}`"
                @click="router.push(`/decisions/${run.run_id}`)">
        <template #value>
          <van-tag :type="(RUN[run.status]?.[1] as any)">{{ RUN[run.status]?.[0] }}</van-tag>
          <div v-if="run.summary?.buys !== undefined" class="muted">买{{ run.summary.buys }} / 卖{{ run.summary.sells }}</div>
        </template>
      </van-cell>
    </van-cell-group>
  </van-pull-refresh>
</template>
