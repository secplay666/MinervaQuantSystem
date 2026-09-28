<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';

import { App } from '@capacitor/app';
import { showNotify } from 'vant';

import { post, request } from '../api';
import { LEVEL, time } from '../format';
import { isNative } from '../store';

const POLL_MS = 60_000;  // the app polls while in the foreground (no WebSocket in the native shell)
const router = useRouter();
const items = ref<any[]>([]);
const unread = ref(0);
const loading = ref(false);
let timer: ReturnType<typeof setInterval> | undefined;
let newest = '';

async function load(notify = false) {
  loading.value = true;
  try {
    const data = await request('/events?limit=100');
    if (notify && newest) {
      const fresh = data.items.filter((e: any) => e.at > newest && e.level !== 'info');
      if (fresh.length) showNotify({ message: fresh[0].title, type: fresh[0].level === 'critical' ? 'danger' : 'warning' });
    }
    items.value = data.items;
    unread.value = data.unread;
    newest = data.items[0]?.at ?? newest;
  } finally {
    loading.value = false;
  }
}

async function open(event: any) {
  if (!event.read) {
    await post(`/events/${event.event_id}/read`);
    event.read = true;
    unread.value = Math.max(0, unread.value - 1);
  }
  if (event.run_id) router.push(`/decisions/${event.run_id}`);
  else if (event.account_id) router.push(`/accounts/${event.account_id}`);
}

async function readAll() {
  await post('/events/read-all');
  await load();
}

onMounted(async () => {
  await load();
  timer = setInterval(() => load(true), POLL_MS);
  if (isNative) App.addListener('resume', () => load(true));
});
onBeforeUnmount(() => clearInterval(timer));
</script>

<template>
  <van-nav-bar title="通知" :right-text="unread ? '全部已读' : ''" @click-right="unread && readAll()" />
  <van-pull-refresh v-model="loading" @refresh="load()">
    <van-empty v-if="!items.length && !loading" description="暂无通知" />
    <van-cell-group inset style="margin-top: 12px">
      <van-cell v-for="event in items" :key="event.event_id" is-link :label="`${time(event.at)} · ${event.body ?? ''}`" @click="open(event)">
        <template #title>
          <van-tag :type="(LEVEL[event.level]?.[1] as any)" style="margin-right: 6px">{{ LEVEL[event.level]?.[0] }}</van-tag>
          <span :style="{ fontWeight: event.read ? 'normal' : '600' }">{{ event.title }}</span>
        </template>
      </van-cell>
    </van-cell-group>
  </van-pull-refresh>
</template>
