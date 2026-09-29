<script lang="ts" setup>
import type { Account, DecisionRun, EventItem, Overview, TodoAccount } from '#/api';

import { computed, onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';

import { useAccess } from '@vben/access';
import { Page } from '@vben/common-ui';

import { Button, Card, Col, Empty, List, Row, Space, Statistic, Table, Tag } from 'ant-design-vue';

import { accountsApi, decisionsApi, eventsApi, overviewApi, systemStatusApi, todoApi } from '#/api';
import { bigYuan, changeColor, dateTime, LEVEL, pct, RUN_KIND, RUN_STATUS, timeLeft, yuan } from '#/utils/format';

const router = useRouter();
const { hasAccessByCodes } = useAccess();
const status = ref<Record<string, any>>();
const overview = ref<Overview>();
const accounts = ref<Account[]>([]);
const decisions = ref<DecisionRun[]>([]);
const events = ref<EventItem[]>([]);
const unread = ref(0);
const todo = ref<{ accounts: TodoAccount[]; critical_unread: { at: string; event_id: string; run_id: null | string; title: string }[] }>();
const todoItems = computed(() => {
  const items: { action: () => void; button: string; color: string; detail: string; title: string }[] = [];
  for (const a of todo.value?.accounts ?? []) {
    for (const r of a.pending_runs) {
      const paper = r.paper_cutoff && timeLeft(r.paper_cutoff)
        ? `；模拟账户 ${dateTime(r.paper_cutoff)} 前批准才按开盘成交（剩 ${timeLeft(r.paper_cutoff)}）` : '';
      items.push({ action: () => router.push(`/decisions/${r.run_id}`), button: '去审核', color: 'warning',
                   title: `${a.name}：${r.pending} 条交易意图待审核`,
                   detail: `${r.trade_date} ${RUN_KIND[r.kind] ?? r.kind}，审核截止 ${dateTime(r.valid_until)}（剩 ${timeLeft(r.valid_until) || '—'}）${paper}` });
    }
    const latest = a.latest;
    const open = () => router.push(`/decisions/${latest.run_id}`);
    if (latest.status === 'blocked') {
      items.push({ action: open, button: '查看', color: 'error', title: `${a.name}：${latest.trade_date} 的决策被闸门阻断`,
                   detail: latest.failed_gate ? `${latest.failed_gate.gate} ${latest.failed_gate.message}` : '' });
    } else if (latest.status === 'failed') {
      items.push({ action: open, button: '查看', color: 'error', title: `${a.name}：${latest.trade_date} 的决策运行失败`,
                   detail: latest.reason ?? '' });
    }
  }
  // A blocked or failed run is listed above already; skip its own critical event.
  const shown = new Set((todo.value?.accounts ?? []).filter((a) => ['blocked', 'failed'].includes(a.latest.status))
    .map((a) => a.latest.run_id));
  for (const e of (todo.value?.critical_unread ?? []).filter((x) => !x.run_id || !shown.has(x.run_id))) {
    items.push({ action: () => router.push('/events'), button: '通知中心', color: 'error', title: e.title,
                 detail: `严重通知 · ${dateTime(e.at)}` });
  }
  return items;
});
const pendingTotal = computed(() => (todo.value?.accounts ?? []).reduce((n, a) => n + a.pending, 0));

const latestIngest = computed(() => status.value?.ingest_runs?.find((r: any) => r.mode !== undefined && r.mode !== null));
const totalNav = computed(() => accounts.value.reduce((n, a) => n + (a.latest?.nav_fen ?? 0), 0));

async function load() {
  const tasks: Promise<unknown>[] = [];
  if (hasAccessByCodes(['data:view'])) tasks.push(systemStatusApi().then((v) => (status.value = v)));
  if (hasAccessByCodes(['market:view'])) tasks.push(overviewApi().then((v) => (overview.value = v)));
  if (hasAccessByCodes(['account:view'])) tasks.push(accountsApi().then((v) => (accounts.value = v)));
  if (hasAccessByCodes(['decision:view'])) {
    tasks.push(decisionsApi({ limit: 10 }).then((v) => (decisions.value = v)), todoApi().then((v) => (todo.value = v)));
  }
  if (hasAccessByCodes(['event:view']))
    tasks.push(eventsApi({ limit: 8 }).then((v) => ((events.value = v.items), (unread.value = v.unread))));
  await Promise.allSettled(tasks);
}

const decisionColumns = [
  { dataIndex: 'trade_date', title: '交易日', width: 110 },
  { dataIndex: 'account_id', title: '账户' },
  { dataIndex: 'kind', key: 'kind', title: '类型' },
  { dataIndex: 'status', key: 'status', title: '状态' },
  { key: 'intents', title: '交易意图' },
];

onMounted(load);
</script>

<template>
  <Page title="今日概览" :description="overview ? `最新交易日 ${overview.session}` : ''">
    <Row :gutter="[16, 16]">
      <Col :xs="12" :md="6">
        <Card size="small">
          <Statistic title="最近一次采集" :value="latestIngest?.status === 'complete' ? '完成' : latestIngest?.status ?? '—'"
                     :value-style="{ color: latestIngest?.status === 'complete' ? '#16a34a' : '#e5484d' }" />
          <div class="text-muted-foreground mt-1 text-xs">交易日 {{ latestIngest?.expected_latest_date ?? '—' }}</div>
        </Card>
      </Col>
      <Col :xs="12" :md="6">
        <Card size="small" class="cursor-pointer" @click="router.push('/decisions')">
          <Statistic title="待审核的交易意图" :value="pendingTotal" suffix="条"
                     :value-style="{ color: pendingTotal ? '#d97706' : undefined }" />
          <div class="text-muted-foreground mt-1 text-xs">所有仍在审核期内的清单；点击进入审核</div>
        </Card>
      </Col>
      <Col :xs="12" :md="6">
        <Card size="small">
          <Statistic title="账户总净值" :value="bigYuan(totalNav)" suffix="元" />
          <div class="text-muted-foreground mt-1 text-xs">{{ accounts.length }} 个账户</div>
        </Card>
      </Col>
      <Col :xs="12" :md="6">
        <Card size="small" class="cursor-pointer" @click="router.push('/events')">
          <Statistic title="未读通知" :value="unread" suffix="条" />
          <div class="text-muted-foreground mt-1 text-xs">点击查看通知中心</div>
        </Card>
      </Col>

      <Col v-if="todo" :span="24">
        <Card size="small" title="待办">
          <Empty v-if="!todoItems.length" description="暂无待办" :image-style="{ height: '40px' }" />
          <List v-else size="small" :data-source="todoItems">
            <template #renderItem="{ item }">
              <List.Item>
                <List.Item.Meta :description="item.detail">
                  <template #title>
                    <Tag :color="item.color">{{ item.color === 'error' ? '需处理' : '待审核' }}</Tag>{{ item.title }}
                  </template>
                </List.Item.Meta>
                <template #actions><Button size="small" type="link" @click="item.action()">{{ item.button }}</Button></template>
              </List.Item>
            </template>
          </List>
        </Card>
      </Col>

      <Col :xs="24" :lg="14">
        <Card size="small" title="市场" :extra="overview ? overview.session : ''">
          <template v-if="overview">
            <Row :gutter="[12, 12]">
              <Col v-for="index in overview.indices" :key="index.symbol" :xs="12" :md="8">
                <div class="text-muted-foreground text-xs">{{ index.name }}</div>
                <div class="text-lg font-semibold" :style="{ color: changeColor(index.change_pct) }">
                  {{ index.close.toFixed(2) }}
                  <span class="text-sm">{{ pct(index.change_pct, 2, true) }}</span>
                </div>
              </Col>
            </Row>
            <Space class="mt-3" wrap>
              <Tag color="red">上涨 {{ overview.breadth.up }}</Tag>
              <Tag color="green">下跌 {{ overview.breadth.down }}</Tag>
              <Tag>平盘 {{ overview.breadth.flat }}</Tag>
              <Tag color="red">涨停 {{ overview.breadth.limit_up }}</Tag>
              <Tag color="green">跌停 {{ overview.breadth.limit_down }}</Tag>
              <Tag>成交额 {{ bigYuan(overview.breadth.amount_cny, false) }}</Tag>
            </Space>
          </template>
          <Empty v-else description="无行情权限或行情库不可用" />
        </Card>
      </Col>
      <Col :xs="24" :lg="10">
        <Card size="small" title="账户">
          <List :data-source="accounts" size="small">
            <template #renderItem="{ item }">
              <List.Item class="cursor-pointer" @click="router.push(`/accounts/${item.account_id}`)">
                <Space>
                  <Tag :color="item.mode === 'paper' ? 'purple' : 'blue'">{{ item.mode === 'paper' ? '模拟' : '手工' }}</Tag>
                  <span>{{ item.name }}</span>
                </Space>
                <span>{{ item.latest ? `${yuan(item.latest.nav_fen)} 元` : '暂无快照' }}</span>
              </List.Item>
            </template>
          </List>
        </Card>
      </Col>

      <Col :xs="24" :lg="14">
        <Card size="small" title="最近决策">
          <Table :columns="decisionColumns" :data-source="decisions" :pagination="false" row-key="run_id" size="small"
                 :custom-row="(record: DecisionRun) => ({ onClick: () => router.push(`/decisions/${record.run_id}`) })">
            <template #bodyCell="{ column, record }">
              <template v-if="column.key === 'kind'">{{ RUN_KIND[record.kind] }}</template>
              <template v-else-if="column.key === 'status'">
                <Tag :color="RUN_STATUS[record.status]?.color">{{ RUN_STATUS[record.status]?.label }}</Tag>
              </template>
              <template v-else-if="column.key === 'intents'">
                {{ record.summary?.buys !== undefined ? `买 ${record.summary.buys} / 卖 ${record.summary.sells}` : '—' }}
              </template>
            </template>
          </Table>
        </Card>
      </Col>
      <Col :xs="24" :lg="10">
        <Card size="small" title="最新通知">
          <List :data-source="events" size="small">
            <template #renderItem="{ item }">
              <List.Item>
                <List.Item.Meta :description="`${dateTime(item.at)} · ${item.body ?? ''}`">
                  <template #title>
                    <Tag :color="LEVEL[item.level]?.color">{{ LEVEL[item.level]?.label }}</Tag>{{ item.title }}
                  </template>
                </List.Item.Meta>
              </List.Item>
            </template>
          </List>
        </Card>
      </Col>
    </Row>
  </Page>
</template>
