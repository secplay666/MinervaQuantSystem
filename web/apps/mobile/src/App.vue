<script setup lang="ts">
import { computed } from 'vue';
import { useRoute } from 'vue-router';

import { can } from './store';

const route = useRoute();
const showTabs = computed(() => Boolean(route.meta.tab));
</script>

<template>
  <div :class="{ 'with-tabs': showTabs }">
    <router-view />
    <van-tabbar v-if="showTabs" route safe-area-inset-bottom>
      <van-tabbar-item icon="home-o" to="/">概览</van-tabbar-item>
      <van-tabbar-item v-if="can('market:view')" icon="chart-trending-o" to="/market">行情</van-tabbar-item>
      <van-tabbar-item v-if="can('position:use')" icon="aim" to="/pm">仓位</van-tabbar-item>
      <van-tabbar-item v-if="can('decision:view')" icon="todo-list-o" to="/decisions">决策</van-tabbar-item>
      <van-tabbar-item icon="user-o" to="/me">我的</van-tabbar-item>
    </van-tabbar>
  </div>
</template>

<style>
body {
  margin: 0;
  background: #f5f6f8;
  font-family: -apple-system, 'PingFang SC', 'Microsoft YaHei', sans-serif;
  -webkit-text-size-adjust: 100%;
}
.with-tabs { padding-bottom: 60px; }
.muted { color: #969799; font-size: 12px; }
.up { color: #e5484d; }
.down { color: #16a34a; }
/* A card on the grey background, as the inset cell groups. */
.card { margin: 12px; padding: 12px; background: #fff; border-radius: 8px; }
.card-title { margin-bottom: 8px; font-size: 14px; font-weight: 600; }
/* Compact tables for numeric lists that do not fit cells: scroll sideways inside the card. */
.table-wrap { overflow-x: auto; -webkit-overflow-scrolling: touch; }
.table { width: 100%; font-size: 12px; border-collapse: collapse; white-space: nowrap; }
.table th { padding: 6px 4px; color: #969799; font-weight: 400; text-align: right; border-bottom: 1px solid #ebedf0; }
.table td { padding: 6px 4px; text-align: right; border-bottom: 1px solid #f5f6f8; }
.table th:first-child, .table td:first-child { text-align: left; position: sticky; left: 0; background: #fff; }
.note { margin: 8px 16px 16px; color: #969799; font-size: 12px; line-height: 18px; }
.van-tag { white-space: nowrap; }
</style>
