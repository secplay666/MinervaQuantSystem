<script setup lang="ts">
/**
 * A minimal ECharts wrapper for phone pages: renders ``option`` (replaced on
 * change), follows the container's size, and emits clicks on data items.
 * Keep options phone-sized: few series, legend on top, grid with small margins,
 * tooltip trigger 'axis' (a tap shows it), no dataZoom slider unless needed.
 */
import type { ECharts, EChartsOption } from 'echarts';

import { onBeforeUnmount, onMounted, ref, watch } from 'vue';

import * as echarts from 'echarts';

const props = withDefaults(defineProps<{ height?: number | string; option: EChartsOption }>(), { height: 260 });
const emit = defineEmits<{ click: [params: any] }>();
const el = ref<HTMLDivElement>();
let chart: ECharts | null = null;
let observer: null | ResizeObserver = null;

onMounted(() => {
  if (!el.value) return;
  chart = echarts.init(el.value);
  chart.setOption(props.option, true);
  chart.on('click', (params) => emit('click', params));
  observer = new ResizeObserver(() => chart?.resize());
  observer.observe(el.value);
});
watch(() => props.option, (option) => chart?.setOption(option, true));
onBeforeUnmount(() => {
  observer?.disconnect();
  chart?.dispose();
  chart = null;
});
defineExpose({ chart: () => chart });
</script>

<template>
  <div ref="el" :style="{ height: typeof height === 'number' ? `${height}px` : height, width: '100%' }"></div>
</template>
