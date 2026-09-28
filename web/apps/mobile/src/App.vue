<script setup lang="ts">
import { computed } from 'vue';
import { useRoute } from 'vue-router';

const route = useRoute();
const showTabs = computed(() => Boolean(route.meta.tab));
</script>

<template>
  <div :class="{ 'with-tabs': showTabs }">
    <router-view />
    <van-tabbar v-if="showTabs" route safe-area-inset-bottom>
      <van-tabbar-item icon="home-o" to="/">概览</van-tabbar-item>
      <van-tabbar-item icon="todo-list-o" to="/decisions">决策</van-tabbar-item>
      <van-tabbar-item icon="balance-o" to="/accounts">账户</van-tabbar-item>
      <van-tabbar-item icon="bell" to="/events">通知</van-tabbar-item>
      <van-tabbar-item icon="user-o" to="/me">我的</van-tabbar-item>
    </van-tabbar>
  </div>
</template>

<style>
body {
  margin: 0;
  background: #f5f6f8;
  font-family: -apple-system, 'PingFang SC', 'Microsoft YaHei', sans-serif;
}
.with-tabs { padding-bottom: 60px; }
.muted { color: #969799; font-size: 12px; }
.up { color: #e5484d; }
.down { color: #16a34a; }
</style>
