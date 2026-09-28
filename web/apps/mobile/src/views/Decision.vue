<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import { showConfirmDialog, showFailToast, showSuccessToast } from 'vant';

import { post, request } from '../api';
import { INTENT, KIND, price, RUN, time, yuan } from '../format';
import { can } from '../store';

const route = useRoute();
const router = useRouter();
const run = ref<any>();
const loading = ref(false);
const sheet = reactive<{ intent: any; open: boolean }>({ intent: null, open: false });
const dialog = reactive<{ kind: '' | 'modify' | 'reject'; qty: number; reason: string }>({ kind: '', qty: 0, reason: '' });
const canApprove = computed(() => can('decision:approve'));
const pending = computed(() => run.value?.intents.filter((i: any) => i.status === 'pending_approval') ?? []);
const failedGate = computed(() => run.value?.gates.find((g: any) => !g.passed));
const actions = computed(() => {
  const status = sheet.intent?.status;
  const list: { callback: () => void; color?: string; name: string }[] = [];
  if (status === 'pending_approval') list.push({ callback: () => act('approve'), color: '#1677ff', name: '批准' });
  if (['approved', 'modified', 'pending_approval'].includes(status)) {
    list.push({ callback: () => act('modify'), name: '修改数量' }, { callback: () => act('reject'), color: '#e5484d', name: '拒绝' });
  }
  return list;
});

async function load() {
  loading.value = true;
  try {
    run.value = await request(`/decisions/${route.params.runId}`);
  } finally {
    loading.value = false;
  }
}

function replace(updated: any) {
  const index = run.value.intents.findIndex((i: any) => i.intent_id === updated.intent_id);
  if (index >= 0) run.value.intents[index] = { ...updated, name: run.value.intents[index].name };
}

async function act(kind: 'approve' | 'modify' | 'reject') {
  const intent = sheet.intent;
  sheet.open = false;
  if (kind !== 'approve') {
    Object.assign(dialog, { kind, qty: intent.qty, reason: '' });
    return;
  }
  try {
    replace(await post(`/intents/${intent.intent_id}/approve`, {}));
    showSuccessToast('已批准');
  } catch (error: any) {
    showFailToast(error.message);
  }
}

async function submitDialog() {
  const intent = sheet.intent;
  if (!dialog.reason.trim()) {
    showFailToast('请填写原因');
    return false;
  }
  try {
    const body = dialog.kind === 'modify' ? { qty: dialog.qty, reason: dialog.reason } : { reason: dialog.reason };
    replace(await post(`/intents/${intent.intent_id}/${dialog.kind}`, body));
    showSuccessToast(dialog.kind === 'modify' ? '已修改' : '已拒绝');
    return true;
  } catch (error: any) {
    showFailToast(error.message);
    return false;
  }
}

async function approveAll(includeWarnings: boolean) {
  const count = includeWarnings ? pending.value.length : pending.value.filter((i: any) => i.risk === 'pass').length;
  try {
    await showConfirmDialog({ message: `批准 ${count} 条${includeWarnings ? '（含警告）' : '（仅检查全部通过的）'}？`, title: '批量批准' });
  } catch {
    return;
  }
  try {
    const result = await post(`/decisions/${run.value.run_id}/approve-all`, { include_warnings: includeWarnings });
    showSuccessToast(`已批准 ${result.approved} 条`);
    await load();
  } catch (error: any) {
    showFailToast(error.message);
  }
}

onMounted(load);
</script>

<template>
  <van-nav-bar :title="run ? `决策 ${run.trade_date}` : '决策'" left-arrow @click-left="router.back()" />
  <van-pull-refresh v-model="loading" @refresh="load">
    <template v-if="run">
      <van-notice-bar v-if="failedGate" color="#e5484d" background="#fff1f0" wrapable
                      :text="`被闸门 ${failedGate.gate} 阻断：${failedGate.message}${failedGate.hint ? '。' + failedGate.hint : ''}`" />
      <van-notice-bar v-else-if="pending.length" wrapable
                      :text="`${pending.length} 条待审核，截止 ${time(run.intents[0]?.valid_until)}（执行日 ${run.next_session}）`" />
      <van-cell-group inset style="margin-top: 12px">
        <van-cell title="账户" :value="run.account_id" />
        <van-cell title="类型" :value="KIND[run.kind]" />
        <van-cell title="状态"><template #value><van-tag :type="(RUN[run.status]?.[1] as any)">{{ RUN[run.status]?.[0] }}</van-tag></template></van-cell>
        <van-cell title="净值 / 现金" :value="`${yuan(run.nav_fen, 0)} / ${yuan(run.cash_fen, 0)}`" />
        <van-cell v-if="run.summary?.buys !== undefined" title="交易"
                  :value="`卖 ${run.summary.sells} 笔 · 买 ${run.summary.buys} 笔 · 费用 ${yuan(run.summary.fees_fen)}`" />
      </van-cell-group>
      <div v-if="canApprove && pending.length" style="display: flex; gap: 8px; margin: 12px 16px">
        <van-button type="primary" size="small" style="flex: 1" @click="approveAll(false)">批准全部通过项</van-button>
        <van-button size="small" style="flex: 1" @click="approveAll(true)">全部批准（含警告）</van-button>
      </div>
      <van-cell-group v-if="run.intents.length" inset title="交易清单">
        <van-cell v-for="intent in run.intents" :key="intent.intent_id" :is-link="canApprove"
                  @click="canApprove && ((sheet.intent = intent), (sheet.open = true))">
          <template #title>
            <van-tag :type="intent.side === 'buy' ? 'danger' : 'success'" style="margin-right: 6px">{{ intent.side === 'buy' ? '买' : '卖' }}</van-tag>
            <span @click.stop="router.push(`/instruments/${intent.symbol}`)">{{ intent.symbol }} {{ intent.name ?? '' }}</span>
          </template>
          <template #label>
            {{ intent.qty.toLocaleString() }} 股 · 参考 {{ price(intent.ref_price_fen) }} · {{ yuan(intent.est_notional_fen, 0) }} 元
            <div v-for="check in intent.checks" :key="check.rule_id" :style="{ color: check.decision === 'reject' ? '#e5484d' : '#ed8a19' }">
              {{ check.rule_id }} {{ check.message }}
            </div>
          </template>
          <template #value>
            <van-tag :type="(INTENT[intent.status]?.[1] as any)">{{ INTENT[intent.status]?.[0] ?? intent.status }}</van-tag>
          </template>
        </van-cell>
      </van-cell-group>
    </template>
  </van-pull-refresh>

  <van-action-sheet v-model:show="sheet.open" cancel-text="取消" :actions="actions"
                    :description="sheet.intent ? `${sheet.intent.side === 'buy' ? '买入' : '卖出'} ${sheet.intent.symbol} ${sheet.intent.qty} 股` : ''" />

  <van-dialog :show="!!dialog.kind" :title="dialog.kind === 'modify' ? '修改数量' : '拒绝'" show-cancel-button
              :before-close="async (action: string) => (action === 'confirm' ? await submitDialog() : true)"
              @closed="dialog.kind = ''">
    <van-cell-group>
      <van-field v-if="dialog.kind === 'modify'" label="数量（股）">
        <template #input><van-stepper v-model="dialog.qty" :min="1" :step="100" integer input-width="80px" /></template>
      </van-field>
      <van-field v-model="dialog.reason" label="原因" type="textarea" rows="2" placeholder="必填" />
    </van-cell-group>
  </van-dialog>
</template>
