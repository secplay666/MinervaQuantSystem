<script lang="ts" setup>
import type { InvitationItem } from '#/api';

import { computed, onMounted, reactive, ref } from 'vue';

import { Alert, Button, Form, Input, InputNumber, message, Modal, Popconfirm, Select, Table, Tag, Typography } from 'ant-design-vue';

import { createInvitationApi, invitationsApi, revokeInvitationApi, rolesApi } from '#/api';
import { dateTime, ROLE_LABEL } from '#/utils/format';

const items = ref<InvitationItem[]>([]);
const roleOptions = ref<{ label: string; value: string }[]>([]);
const loading = ref(false);
const form = reactive({ days: 7, max_uses: 1, note: '', open: false, roles: ['viewer'] as string[] });
const created = reactive({ code: '', open: false });
const link = computed(() => `${location.origin}${location.pathname}#/auth/register?code=${created.code}`);

const STATE: Record<string, { color: string; label: string }> = {
  active: { color: 'success', label: '可用' },
  expired: { color: 'default', label: '已过期' },
  revoked: { color: 'error', label: '已作废' },
  used_up: { color: 'processing', label: '已用完' },
};

async function load() {
  loading.value = true;
  try {
    const [list, roles] = await Promise.all([invitationsApi(), rolesApi()]);
    items.value = list;
    // Codes cannot grant administration (the backend refuses it too).
    roleOptions.value = roles.filter((r) => r.code !== 'admin' && !r.permissions.includes('user:manage'))
      .map((r) => ({ label: `${r.name}（${r.code}）`, value: r.code }));
  } finally {
    loading.value = false;
  }
}

async function submit() {
  if (!form.roles.length) {
    message.warning('至少选择一个角色');
    return;
  }
  const result = await createInvitationApi({ days: form.days, max_uses: form.max_uses, note: form.note || undefined,
                                             roles: form.roles });
  form.open = false;
  Object.assign(created, { code: result.code ?? '', open: true });
  Object.assign(form, { days: 7, max_uses: 1, note: '', roles: ['viewer'] });
  await load();
}

async function revoke(id: number) {
  await revokeInvitationApi(id);
  message.success('邀请码已作废');
  await load();
}

const columns = [
  { key: 'hint', title: '邀请码', width: 120 },
  { key: 'roles', title: '授予角色' },
  { key: 'uses', title: '已用 / 可用', width: 100 },
  { key: 'state', title: '状态', width: 90 },
  { key: 'expires', title: '有效期至', width: 150 },
  { key: 'users', title: '已注册用户' },
  { dataIndex: 'note', title: '备注', ellipsis: true },
  { dataIndex: 'created_by', title: '创建人', width: 100 },
  { key: 'actions', title: '', width: 80 },
];

defineExpose({ openCreate: () => (form.open = true) });
onMounted(load);
</script>

<template>
  <div>
    <div class="mb-3 flex items-center justify-between">
      <span class="text-muted-foreground text-sm">
        生成邀请码发给新用户，对方在登录页点"注册"并填入邀请码即可创建账号，获得邀请码上的角色。邀请码不能授予管理员权限。
      </span>
      <Button type="primary" @click="form.open = true">生成邀请码</Button>
    </div>
    <Table :columns="columns" :data-source="items" :loading="loading" row-key="id" size="middle" :pagination="{ pageSize: 20 }">
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'hint'"><code>····-{{ record.hint }}</code></template>
        <template v-else-if="column.key === 'roles'">
          <Tag v-for="role in record.roles" :key="role" color="blue">{{ ROLE_LABEL[role] ?? role }}</Tag>
        </template>
        <template v-else-if="column.key === 'uses'">{{ record.used_count }} / {{ record.max_uses }}</template>
        <template v-else-if="column.key === 'state'">
          <Tag :color="STATE[record.state]?.color">{{ STATE[record.state]?.label ?? record.state }}</Tag>
        </template>
        <template v-else-if="column.key === 'expires'">{{ dateTime(record.expires_at) }}</template>
        <template v-else-if="column.key === 'users'">{{ record.users.join('、') || '—' }}</template>
        <template v-else-if="column.key === 'actions'">
          <Popconfirm v-if="record.state === 'active'" title="作废这个邀请码？已注册的账号不受影响" @confirm="revoke(record.id)">
            <Button danger size="small">作废</Button>
          </Popconfirm>
        </template>
      </template>
    </Table>

    <Modal v-model:open="form.open" title="生成邀请码" ok-text="生成" @ok="submit">
      <Form layout="vertical">
        <Form.Item label="授予角色" required extra="新用户注册后获得这些角色，之后可以在用户列表中调整">
          <Select v-model:value="form.roles" mode="multiple" :options="roleOptions" />
        </Form.Item>
        <Form.Item label="可注册人数"><InputNumber v-model:value="form.max_uses" :min="1" :max="50" /></Form.Item>
        <Form.Item label="有效天数"><InputNumber v-model:value="form.days" :min="1" :max="30" /></Form.Item>
        <Form.Item label="备注"><Input v-model:value="form.note" :maxlength="200" placeholder="例如发给谁、用途" /></Form.Item>
      </Form>
    </Modal>

    <Modal v-model:open="created.open" title="邀请码已生成" :footer="null">
      <Alert type="warning" show-icon class="mb-3" message="邀请码只显示这一次，请复制后通过安全渠道发给对方。" />
      <p>邀请码：<Typography.Text copyable code>{{ created.code }}</Typography.Text></p>
      <p class="text-sm">注册链接（已带邀请码）：</p>
      <Typography.Paragraph copyable class="text-xs">{{ link }}</Typography.Paragraph>
    </Modal>
  </div>
</template>
