<script lang="ts" setup>
import type { NotificationItem } from '@vben/layouts';

import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';

import { AuthenticationLoginExpiredModal } from '@vben/common-ui';
import { BasicLayout, LockScreen, Notification, UserDropdown } from '@vben/layouts';
import { preferences } from '@vben/preferences';
import { useAccessStore, useUserStore } from '@vben/stores';

import { metaApi } from '#/api';
import { useEvents } from '#/composables/use-events';
import { useAuthStore } from '#/store';
import { ROLE_LABEL } from '#/utils/format';
import LoginForm from '#/views/_core/authentication/login.vue';

const router = useRouter();
const userStore = useUserStore();
const authStore = useAuthStore();
const accessStore = useAccessStore();
const events = useEvents();
const environment = ref('');

const menus = computed(() => [
  { handler: () => router.push({ name: 'Profile' }), icon: 'lucide:user', text: '个人设置' },
  { handler: () => router.push({ name: 'Events' }), icon: 'lucide:bell', text: '通知中心' },
]);

const avatar = computed(() => userStore.userInfo?.avatar || preferences.app.defaultAvatar);
const roleText = computed(() =>
  (userStore.userInfo?.desc ?? '').split(', ').filter(Boolean).map((r: string) => ROLE_LABEL[r] ?? r).join('、'),
);

async function handleLogout() {
  events.stop();
  await authStore.logout(false);
}

function handleClick(item: NotificationItem) {
  if (item.id) void events.markRead(item.id);
  if (item.link) router.push(item.link);
}

onMounted(async () => {
  events.start();
  try {
    environment.value = (await metaApi()).environment_label;
  } catch {
    environment.value = '';
  }
});
onBeforeUnmount(() => events.stop());
</script>

<template>
  <BasicLayout
    :avatar
    :text="userStore.userInfo?.realName"
    @clear-preferences-and-logout="handleLogout"
    @logout="handleLogout"
  >
    <template #user-dropdown>
      <UserDropdown
        :avatar
        :menus
        :text="userStore.userInfo?.realName"
        :description="roleText"
        :tag-text="environment"
        @clear-preferences-and-logout="handleLogout"
        @logout="handleLogout"
      />
    </template>
    <template #notification>
      <Notification
        :dot="events.unread.value > 0"
        :notifications="events.notifications.value"
        @clear="events.markAll"
        @read="(item) => item.id && events.markRead(item.id)"
        @remove="(item) => item.id && events.markRead(item.id)"
        @make-all="events.markAll"
        @on-click="handleClick"
        @view-all="router.push({ name: 'Events' })"
      />
    </template>
    <template #extra>
      <AuthenticationLoginExpiredModal v-model:open="accessStore.loginExpired" :avatar>
        <LoginForm />
      </AuthenticationLoginExpiredModal>
    </template>
    <template #lock-screen>
      <LockScreen :avatar @to-login="handleLogout" />
    </template>
  </BasicLayout>
</template>
