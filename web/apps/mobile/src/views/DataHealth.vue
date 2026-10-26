<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';

import { showFailToast } from 'vant';

import { request } from '../api';
import { KIND, RUN, time } from '../format';

const router = useRouter();
const status = ref<Record<string, any>>();
const loading = ref(false);
const INGEST: Record<string, string> = { aborted: 'default', complete: 'success', failed: 'danger', partial: 'warning' };

async function load() {
  loading.value = true;
  try {
    status.value = await request('/system/status');
  } catch (error: any) {
    showFailToast(error.message);
  } finally {
    loading.value = false;
  }
}

onMounted(load);
</script>

<template>
  <van-nav-bar title="数据健康" left-arrow @click-left="router.back()" />
  <van-pull-refresh v-model="loading" @refresh="load">
    <div v-if="status" class="note">行情库最新交易日 {{ status.latest_session ?? '—' }}</div>
    <van-cell-group v-if="status" inset title="采集运行（最近 5 次，含重建）">
      <van-cell v-for="run in status.ingest_runs ?? []" :key="run.run_id" :title="run.expected_latest_date ?? run.run_id"
                :label="`${run.mode ?? '重建'} · 结束 ${time(run.finished_at)}`">
        <template #value>
          <van-tag :type="(INGEST[run.status] as any)">{{ run.status }}</van-tag>
          <div class="muted">阻断 {{ run.quality_summary?.blocking ?? '—' }} / 警告 {{ run.quality_summary?.warning ?? '—' }}</div>
        </template>
      </van-cell>
    </van-cell-group>
    <van-cell-group v-if="status?.daily_jobs?.length" inset title="每日任务">
      <van-cell v-for="job in status.daily_jobs" :key="job.started" :title="job.latest_session ?? '—'"
                :label="`开始 ${time(job.started)}${job.warning ? ` · ${job.warning}` : ''}`"
                :value="`${job.status ?? '—'} · 退出码 ${job.exit}`" />
    </van-cell-group>
    <van-cell-group v-if="status?.backups?.length" inset title="备份快照">
      <van-cell v-for="b in status.backups" :key="b.finished" :title="b.snapshot" :label="`完成 ${time(b.finished)}`"
                :value="`新增 ${b.added_mb} MB · ${b.seconds} s`" />
    </van-cell-group>
    <van-cell-group v-if="status?.decisions?.length" inset title="最近决策">
      <van-cell v-for="d in status.decisions" :key="d.run_id" is-link :title="`${d.trade_date} ${d.account_id}`"
                :label="`${KIND[d.kind] ?? d.kind}${d.failed_gate ? ` · 阻断闸门 ${d.failed_gate}` : ''}`"
                @click="router.push(`/decisions/${d.run_id}`)">
        <template #value><van-tag :type="(RUN[d.status]?.[1] as any)">{{ RUN[d.status]?.[0] ?? d.status }}</van-tag></template>
      </van-cell>
    </van-cell-group>
  </van-pull-refresh>
</template>
