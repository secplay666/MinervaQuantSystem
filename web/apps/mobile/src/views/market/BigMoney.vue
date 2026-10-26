<script setup lang="ts">
import type { EChartsOption } from 'echarts';

import type { BigMoneyDay, BigMoneyGroup } from './info-api';

import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { useRouter } from 'vue-router';

import { showFailToast } from 'vant';

import { qs, request } from '../../api';
import EChart from '../../components/EChart.vue';
import { DOWN, UP } from '../../format';
import { INK, missing, MUTED, SERIES } from './info-api';

const router = useRouter();
const data = ref<BigMoneyDay>();
const day = ref<string>();
const groupId = ref<string>();
const refreshing = ref(false);
const failed = ref('');
const picking = ref(false);
let timer: ReturnType<typeof setInterval> | undefined;

const RATIO_ALERT = 2;
const group = computed<BigMoneyGroup | undefined>(() => data.value?.groups.find((g) => g.id === groupId.value));
const yi = (v?: null | number, digits = 1) => (missing(v) ? '—' : `${v > 0 ? '+' : ''}${(v / 1e8).toFixed(digits)} 亿`);
const yiPlain = (v?: null | number) => (missing(v) ? '—' : `${(v / 1e8).toFixed(1)} 亿`);
const pct = (v?: null | number, digits = 0) => (missing(v) ? '—' : `${(v * 100).toFixed(digits)}%`);
const signed = (v?: null | number, digits = 2) => (missing(v) ? '—' : `${v > 0 ? '+' : ''}${(v * 100).toFixed(digits)}%`);
const times = (v?: null | number) => (missing(v) ? '—' : `${v.toFixed(1)} 倍`);
const netColor = (v?: null | number) => (missing(v) || Math.abs(v) < 1e8 ? undefined : v > 0 ? UP : DOWN);
const dayColumns = computed(() => [...(data.value?.days ?? [])].reverse().map((d) => ({ text: d, value: d })));

const statusText = computed(() => {
  const d = data.value;
  if (!d) return '';
  const at = d.updated.slice(11, 19);
  if (d.live) return `盘中，每分钟更新（最后更新 ${at}）`;
  return d.final ? `${d.day} 已收盘` : `${d.day}，监测没有在运行（最后更新 ${at}）`;
});

async function load(keepGroup = true) {
  failed.value = '';
  try {
    const result = await request<BigMoneyDay>(`/monitor/big-money${qs({ day: day.value })}`);
    data.value = result;
    day.value = result.day;
    if (!keepGroup || !result.groups.some((g) => g.id === groupId.value)) {
      const alerted = result.groups.find((g) => g.alerts.length);
      groupId.value = (alerted ?? result.groups.find((g) => g.id === 'csi300') ?? result.groups[0])?.id;
    }
  } catch (error: any) {
    failed.value = error.message;
    if (data.value) showFailToast(error.message);
  } finally {
    refreshing.value = false;
  }
}

function pickDay({ selectedValues }: { selectedValues: (number | string)[] }) {
  picking.value = false;
  day.value = String(selectedValues[0]);
  void load();
}

/* Three panes stacked in one chart (px positions so they stay readable at any phone width). */
const option = computed<EChartsOption>(() => {
  const g = group.value;
  if (!g || g.series.length === 0) return {};
  const xs = g.series.map((p) => p.time);
  const axis = { axisLine: { lineStyle: { color: MUTED } }, axisLabel: { color: MUTED, fontSize: 10 } };
  const split = { lineStyle: { opacity: 0.25 } };
  const title = (text: string, top: number) => ({ left: 4, text, textStyle: { color: INK, fontSize: 11, fontWeight: 'normal' as const }, top });
  const grid = (top: number, height: number) => ({ height, left: 44, right: 12, top });
  return {
    animation: false,
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
    grid: [grid(22, 100), grid(150, 100), grid(278, 100)],
    series: [
      { data: g.series.map((p) => p.index_price ?? '-'), itemStyle: { color: SERIES[0] }, lineStyle: { width: 1.5 },
        showSymbol: false, type: 'line', xAxisIndex: 0, yAxisIndex: 0 },
      { barMaxWidth: 3, type: 'bar', xAxisIndex: 1, yAxisIndex: 1,
        data: g.series.map((p) => ({ itemStyle: { color: (p.big_net ?? 0) >= 0 ? UP : DOWN }, value: p.big_net ?? 0 })) },
      // the opening auction makes the first minutes' ratio jump: drawn from 09:40
      { data: g.series.map((p) => (p.time < '09:40' ? '-' : p.ratio ?? '-')), itemStyle: { color: SERIES[1] },
        lineStyle: { width: 1.5 }, showSymbol: false, type: 'line', xAxisIndex: 2, yAxisIndex: 2,
        markLine: { data: [{ label: { formatter: `${RATIO_ALERT} 倍`, position: 'insideStartTop' }, yAxis: RATIO_ALERT },
                           { label: { formatter: '平常', position: 'insideStartBottom' }, yAxis: 1 }],
                    label: { color: INK, fontSize: 10 }, lineStyle: { color: INK, type: 'dashed' }, silent: true, symbol: 'none' } },
    ],
    title: [title('指数', 0), title('单笔 500 万以上大单的累计主动净买入（亿元）', 128),
            title(`成交额 / 平常同一时刻（虚线 ${RATIO_ALERT} 倍为提醒门槛之一）`, 256)],
    tooltip: {
      confine: true,
      formatter: (params: any) => {
        const p = g.series[(params as any[])[0]?.dataIndex ?? 0];
        if (!p) return '';
        return [`<b>${p.time}</b>`, `指数 ${missing(p.index_price) ? '—' : p.index_price.toFixed(2)}（${signed(p.index_change)}）`,
                `成交 ${yiPlain(p.amount)}，平常同一时刻 ${yiPlain(p.baseline)}，${times(p.ratio)}`,
                `大单净买 ${yi(p.big_net)}（近 30 分钟 ${yi(p.big_net_30m)}），大单占成交 ${pct(p.big_share)}`,
                `全部主动净买 ${yi(p.active_net)}，溢价 ${signed(p.premium, 3)}`].join('<br/>');
      },
      textStyle: { fontSize: 12 },
      trigger: 'axis',
    },
    xAxis: [0, 1, 2].map((k) => ({ boundaryGap: k === 1, data: xs, gridIndex: k, type: 'category' as const, ...axis,
                                  axisLabel: { color: MUTED, fontSize: 10, show: k === 2 }, axisTick: { show: k === 2 } })),
    yAxis: [
      { gridIndex: 0, scale: true, splitLine: split, splitNumber: 3, ...axis },
      { gridIndex: 1, splitLine: split, splitNumber: 3, ...axis,
        axisLabel: { color: MUTED, fontSize: 10, formatter: (v: number) => (v / 1e8).toFixed(0) } },
      { gridIndex: 2, max: (v: { max: number }) => Math.max(3, Math.ceil(v.max)), min: 0, splitLine: split, splitNumber: 3, ...axis },
    ],
  } as EChartsOption;
});

watch(() => data.value?.live, (live) => {
  if (timer) clearInterval(timer);
  timer = live ? setInterval(() => void load(), 60_000) : undefined;
});
onMounted(() => void load(false));
onBeforeUnmount(() => timer && clearInterval(timer));
</script>

<template>
  <van-nav-bar title="盘中大资金" left-arrow @click-left="router.back()" />
  <van-pull-refresh v-model="refreshing" @refresh="load()">
    <div class="note" style="margin-top: 12px">{{ statusText || '宽基 ETF 的成交和大单，每分钟和平常同一时刻比较' }}</div>
    <van-empty v-if="failed && !data" :description="failed" />
    <van-loading v-else-if="!data" style="padding: 48px 0; text-align: center" />
    <template v-else>
      <van-cell-group inset>
        <van-cell is-link :value="data.day" @click="picking = true">
          <template #title>
            日期 <van-tag v-if="data.live" type="primary" style="margin-left: 4px">盘中</van-tag>
          </template>
        </van-cell>
      </van-cell-group>
      <div v-if="data.errors" class="note">本日有 {{ data.errors }} 次请求失败（已自动跳过）</div>

      <div class="bm-groups">
        <div v-for="g in data.groups" :key="g.id" class="bm-group" :class="{ 'bm-selected': g.id === groupId }" @click="groupId = g.id">
          <div style="font-weight: 600">{{ g.name }}</div>
          <div v-if="g.alerts.length" style="margin-top: 2px">
            <van-tag v-for="a in g.alerts" :key="a.direction" :type="a.direction === 'buy' ? 'danger' : 'success'" style="margin: 2px 4px 0 0">
              {{ a.direction === 'buy' ? '大额买入' : '大额卖出' }} {{ a.time }}
            </van-tag>
          </div>
          <template v-if="g.latest">
            <div class="bm-line">成交 <b>{{ times(g.latest.ratio) }}</b></div>
            <div class="bm-line">大单 <b :style="{ color: netColor(g.latest.big_net) }">{{ yi(g.latest.big_net) }}</b>
              <span class="muted">（占 {{ pct(g.latest.big_share) }}）</span></div>
            <div class="muted">指数 {{ signed(g.latest.index_change) }}，溢价 {{ signed(g.latest.premium, 3) }}</div>
          </template>
          <div v-else class="muted" style="margin-top: 4px">还没有数据</div>
        </div>
      </div>

      <template v-if="group">
        <div class="card">
          <div class="card-title">{{ group.name }}：{{ group.funds.length }} 只 ETF 合计，平常每天成交约 {{ (group.usual_turnover / 1e8).toFixed(0) }} 亿元</div>
          <EChart v-if="group.series.length" :option="option" :height="400" />
          <van-empty v-else image-size="80" description="这一组还没有数据" />
          <van-button v-if="group.index" size="small" plain type="primary" block style="margin-top: 8px"
                      @click="router.push(`/chart/${group.index}`)">看{{ group.name }}指数 K 线</van-button>
        </div>
        <van-cell-group inset title="ETF">
          <van-cell v-for="f in group.funds" :key="f.symbol">
            <template #title>{{ f.name }} <span class="muted">{{ f.symbol }}</span></template>
            <template #label>
              成交 {{ yiPlain(f.amount) }} · 是平常的 {{ times(f.ratio) }} · 大单占比 {{ pct(f.big_share) }}<br />
              溢价 {{ signed(f.premium, 3) }} · 价格 {{ missing(f.price) ? '—' : f.price.toFixed(3) }}
            </template>
            <template #value>
              <div :style="{ color: netColor(f.big_net), fontWeight: 600 }">{{ yi(f.big_net) }}</div>
              <div class="muted">大单净买</div>
            </template>
          </van-cell>
        </van-cell-group>
      </template>
      <div class="note">
        提醒规则：09:45 以后，成交达到平常同一时刻的 2 倍以上、单笔 500 万以上的大单占成交一半以上、
        大单净买（卖）超过这组 ETF 平常一天成交的 15%（至少 2 亿元），每组每天提醒一次，写入通知中心。
        （2026-10-15、16、20、22、23 五个大额申购日，按这个规则回放都会触发，其余日子不会；只有 13 个交易日的验证，仅供参考。）
        大单主动买入减主动卖出，按腾讯 3 秒逐笔的买卖方向计算；直接在一级市场申购的资金在这里看不到，当天的申购份额收盘后才公布。
        大资金进场是背景信息，不是买卖信号。
      </div>
    </template>
  </van-pull-refresh>

  <van-popup v-model:show="picking" position="bottom" round>
    <van-picker title="选择日期" :columns="dayColumns" :model-value="day ? [day] : []" @confirm="pickDay" @cancel="picking = false" />
  </van-popup>
</template>

<style scoped>
.bm-groups { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin: 12px; }
.bm-group { padding: 10px; background: #fff; border: 2px solid transparent; border-radius: 8px; font-size: 13px; }
.bm-selected { border-color: #2a78d6; }
.bm-line { margin-top: 2px; }
</style>
