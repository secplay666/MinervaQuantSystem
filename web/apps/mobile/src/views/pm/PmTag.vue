<script setup lang="ts">
/** A tag in the PC app's colours (red, orange, gold, green, blue, geekblue, cyan, default). */
import { computed } from 'vue';

import { TAG_COLORS } from './pm-api';
import './pm.css';

const props = withDefaults(defineProps<{ color?: string }>(), { color: 'default' });
const colors = computed(() => TAG_COLORS[props.color] ?? TAG_COLORS.default!);
</script>

<template>
  <van-tag :color="colors[0]" :text-color="colors[1]" class="pm-tag"><slot /></van-tag>
</template>

<style>
.pm-tag { margin-right: 4px; vertical-align: middle; }
</style>
