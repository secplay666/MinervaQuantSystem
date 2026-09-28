import type { NotificationItem } from '@vben/layouts';

import type { EventItem } from '#/api';

import { computed, ref } from 'vue';

import { useAccessStore } from '@vben/stores';

import { notification } from 'ant-design-vue';

import { eventsApi, readAllEventsApi, readEventApi } from '#/api';
import { apiURL } from '#/api/request';
import { dateTime } from '#/utils/format';

const items = ref<EventItem[]>([]);
const unread = ref(0);
let socket: null | WebSocket = null;
let retry = 0;
let stopped = false;

function linkOf(event: EventItem): string | undefined {
  if (event.run_id) return `/decisions/${event.run_id}`;
  if (event.account_id) return `/accounts/${event.account_id}`;
  return '/events';
}

function toNotification(event: EventItem): NotificationItem {
  return {
    avatar: '',
    date: dateTime(event.at),
    id: event.event_id,
    isRead: event.read,
    link: linkOf(event),
    message: [event.body, event.action_hint].filter(Boolean).join(' · '),
    title: event.title,
  };
}

async function load() {
  try {
    const data = await eventsApi({ limit: 20 });
    items.value = data.items;
    unread.value = data.unread;
  } catch {
    // shown by the request client
  }
}

function socketUrl(): string {
  const base = apiURL.startsWith('http') ? apiURL : `${location.origin}${apiURL}`;
  return `${base.replace(/^http/, 'ws')}/ws/events`;
}

function connect() {
  const token = useAccessStore().accessToken;
  if (!token || stopped) return;
  socket = new WebSocket(socketUrl());
  socket.addEventListener('open', () => {
    retry = 0;
    socket?.send(JSON.stringify({ token }));
  });
  socket.addEventListener('message', (message) => {
    const payload = JSON.parse(message.data);
    if (payload.type !== 'event') return;
    const event: EventItem = payload.event;
    items.value = [event, ...items.value].slice(0, 50);
    unread.value += 1;
    if (event.level !== 'info') {
      notification[event.level === 'critical' ? 'error' : 'warning']({
        description: event.body ?? undefined,
        duration: event.level === 'critical' ? 0 : 6,
        message: event.title,
      });
    }
  });
  socket.addEventListener('close', () => {
    socket = null;
    if (!stopped) setTimeout(connect, Math.min(30_000, 2000 * 2 ** retry++));  // reconnect with backoff
  });
}

export function useEvents() {
  return {
    notifications: computed(() => items.value.map((item) => toNotification(item))),
    unread,
    async markAll() {
      await readAllEventsApi();
      items.value = items.value.map((item) => ({ ...item, read: true }));
      unread.value = 0;
    },
    async markRead(id: number | string) {
      const item = items.value.find((event) => event.event_id === id);
      if (item && !item.read) {
        await readEventApi(String(id));
        item.read = true;
        unread.value = Math.max(0, unread.value - 1);
      }
    },
    reload: load,
    start() {
      stopped = false;
      void load();
      if (!socket) connect();
    },
    stop() {
      stopped = true;
      socket?.close();
      socket = null;
    },
  };
}
