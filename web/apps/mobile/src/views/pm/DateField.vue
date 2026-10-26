<script setup lang="ts">
/** A date field (YYYY-MM-DD or empty) picked from a bottom date picker; the ✕ clears it back to the default. */
import { ref } from 'vue';

const props = withDefaults(defineProps<{ label: string; max?: string; min?: string; placeholder?: string }>(), {
  max: undefined, min: '2005-01-01', placeholder: '',
});
defineOptions({ inheritAttrs: false });
const model = defineModel<string | undefined>();
const open = ref(false);
const picked = ref<string[]>([]);
const toDate = (s: string) => new Date(`${s}T00:00:00`);
const today = () => new Date().toISOString().slice(0, 10);

function show() {
  picked.value = (model.value ?? props.max ?? today()).split('-');
  open.value = true;
}

function confirm({ selectedValues }: { selectedValues: string[] }) {
  model.value = selectedValues.join('-');
  open.value = false;
}
</script>

<template>
  <van-field v-bind="$attrs" :label="label" :model-value="model ?? ''" :placeholder="placeholder" readonly is-link @click="show">
    <template v-if="model" #right-icon><van-icon name="clear" color="#c8c9cc" style="padding: 8px" @click.stop="model = undefined" /></template>
  </van-field>
  <van-popup v-model:show="open" position="bottom" round>
    <van-date-picker v-model="picked" :title="label" :min-date="toDate(min)" :max-date="toDate(max ?? today())"
                     @confirm="confirm" @cancel="open = false" />
  </van-popup>
</template>
