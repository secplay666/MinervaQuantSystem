<script lang="ts" setup>
import { onMounted, ref } from 'vue';

import { Page } from '@vben/common-ui';

import { Card, Col, Row, Table, Tag } from 'ant-design-vue';

import { systemStatusApi } from '#/api';
import { dateTime, RUN_KIND, RUN_STATUS } from '#/utils/format';

const status = ref<Record<string, any>>();
const loading = ref(false);

const STATUS_COLOR: Record<string, string> = { aborted: 'default', complete: 'success', failed: 'error', partial: 'warning' };

async function load() {
  loading.value = true;
  try {
    status.value = await systemStatusApi();
  } finally {
    loading.value = false;
  }
}

const ingestColumns = [
  { dataIndex: 'run_id', title: '运行' },
  { key: 'mode', title: '类型' },
  { key: 'status', title: '状态' },
  { dataIndex: 'expected_latest_date', title: '最新交易日' },
  { key: 'quality', title: '阻断 / 警告' },
  { key: 'finished', title: '结束' },
];
const jobColumns = [
  { dataIndex: 'started', title: '开始', customRender: ({ text }: any) => dateTime(text) },
  { dataIndex: 'exit', title: '退出码' },
  { dataIndex: 'status', title: '采集状态' },
  { dataIndex: 'latest_session', title: '交易日' },
  { dataIndex: 'warning', title: '警告' },
];
const backupColumns = [
  { dataIndex: 'finished', title: '完成', customRender: ({ text }: any) => dateTime(text) },
  { dataIndex: 'snapshot', title: '快照' },
  { dataIndex: 'files', title: '文件数' },
  { dataIndex: 'added_mb', title: '新增 MB' },
  { dataIndex: 'seconds', title: '耗时 s' },
];
const decisionColumns = [
  { dataIndex: 'trade_date', title: '交易日' },
  { dataIndex: 'account_id', title: '账户' },
  { key: 'kind', title: '类型' },
  { key: 'status', title: '状态' },
  { dataIndex: 'failed_gate', title: '阻断闸门' },
];

onMounted(load);
</script>

<template>
  <Page title="数据健康" :description="status?.latest_session ? `行情库最新交易日 ${status.latest_session}` : ''">
    <Row :gutter="[16, 16]">
      <Col :span="24">
        <Card size="small" title="采集运行（最近 5 次，含重建）" :loading="loading">
          <Table :columns="ingestColumns" :data-source="status?.ingest_runs ?? []" row-key="run_id" size="small" :pagination="false">
            <template #bodyCell="{ column, record }">
              <template v-if="column.key === 'mode'">{{ record.mode ?? '重建' }}</template>
              <template v-else-if="column.key === 'status'"><Tag :color="STATUS_COLOR[record.status]">{{ record.status }}</Tag></template>
              <template v-else-if="column.key === 'quality'">
                {{ record.quality_summary?.blocking ?? '—' }} / {{ record.quality_summary?.warning ?? '—' }}
              </template>
              <template v-else-if="column.key === 'finished'">{{ dateTime(record.finished_at) }}</template>
            </template>
          </Table>
        </Card>
      </Col>
      <Col :xs="24" :lg="12">
        <Card size="small" title="每日任务（logs/daily/history.tsv）">
          <Table :columns="jobColumns" :data-source="status?.daily_jobs ?? []" row-key="started" size="small" :pagination="false" />
        </Card>
      </Col>
      <Col :xs="24" :lg="12">
        <Card size="small" title="备份快照（logs/backup/history.tsv）">
          <Table :columns="backupColumns" :data-source="status?.backups ?? []" row-key="finished" size="small" :pagination="false" />
        </Card>
      </Col>
      <Col :span="24">
        <Card size="small" title="最近决策">
          <Table :columns="decisionColumns" :data-source="status?.decisions ?? []" row-key="run_id" size="small" :pagination="false">
            <template #bodyCell="{ column, record }">
              <template v-if="column.key === 'kind'">{{ RUN_KIND[record.kind] }}</template>
              <template v-else-if="column.key === 'status'">
                <Tag :color="RUN_STATUS[record.status]?.color">{{ RUN_STATUS[record.status]?.label }}</Tag>
              </template>
            </template>
          </Table>
        </Card>
      </Col>
    </Row>
  </Page>
</template>
