<script lang="ts" setup>
import type { ManagedUser } from '#/api';

import { onMounted, reactive, ref } from 'vue';

import { Page } from '@vben/common-ui';
import { useUserStore } from '@vben/stores';

import { Alert, Button, Card, Form, Input, message, Modal, Select, Space, Switch, Table, Tag, Typography } from 'ant-design-vue';

import { createUserApi, patchUserApi, resetPasswordApi, rolesApi, unlockUserApi, usersApi } from '#/api';
import { dateTime, ROLE_LABEL } from '#/utils/format';

const users = ref<ManagedUser[]>([]);
const roles = ref<{ label: string; value: string }[]>([]);
const loading = ref(false);
const me = useUserStore();
const create = reactive({ display_name: '', open: false, roles: ['viewer'] as string[], username: '' });
const edit = reactive<{ display_name: string; open: boolean; roles: string[]; user?: ManagedUser }>({ display_name: '', open: false, roles: [] });
const secret = reactive({ open: false, password: '', username: '' });

async function load() {
  loading.value = true;
  try {
    const [u, r] = await Promise.all([usersApi(), rolesApi()]);
    users.value = u;
    roles.value = r.map((role) => ({ label: `${role.name}（${role.code}）`, value: role.code }));
  } finally {
    loading.value = false;
  }
}

function showSecret(username: string, password: string) {
  Object.assign(secret, { open: true, password, username });
}

async function submitCreate() {
  if (!/^[A-Za-z0-9_.-]{3,64}$/.test(create.username) || !create.display_name) {
    message.warning('用户名为 3–64 位字母、数字或 _.-；显示名必填');
    return;
  }
  const user = await createUserApi({ display_name: create.display_name, roles: create.roles, username: create.username });
  create.open = false;
  Object.assign(create, { display_name: '', roles: ['viewer'], username: '' });
  showSecret(user.username, user.temporary_password ?? '');
  await load();
}

function openEdit(row: Record<string, any>) {
  const user = row as ManagedUser;
  Object.assign(edit, { display_name: user.display_name, open: true, roles: [...user.roles], user });
}

async function submitEdit() {
  if (!edit.user) return;
  await patchUserApi(edit.user.id, { display_name: edit.display_name, roles: edit.roles });
  edit.open = false;
  message.success('已保存');
  await load();
}

async function toggleActive(row: Record<string, any>, active: boolean) {
  await patchUserApi(row.id, { is_active: active });
  message.success(active ? '已启用' : '已停用，现有会话已注销');
  await load();
}

function reset(row: Record<string, any>) {
  Modal.confirm({
    content: `为 ${row.username} 生成新的临时密码，现有会话全部注销，下次登录必须修改密码。`,
    title: '重置密码',
    async onOk() {
      const result = await resetPasswordApi(row.id);
      showSecret(row.username, result.temporary_password);
    },
  });
}

async function unlock(row: Record<string, any>) {
  await unlockUserApi(row.id);
  message.success('已解锁');
}

const columns = [
  { dataIndex: 'username', title: '用户名', width: 130 },
  { dataIndex: 'display_name', title: '显示名' },
  { key: 'roles', title: '角色' },
  { key: 'flags', title: '状态' },
  { key: 'last', title: '最后登录', width: 150 },
  { key: 'active', title: '启用', width: 80 },
  { key: 'actions', title: '操作', width: 230 },
];

onMounted(load);
</script>

<template>
  <Page title="用户" description="账号由管理员创建；新账号和重置后的账号首次登录必须修改临时密码">
    <template #extra><Button type="primary" @click="create.open = true">新建用户</Button></template>
    <Card size="small">
      <Table :columns="columns" :data-source="users" :loading="loading" row-key="id" size="middle" :pagination="false">
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'roles'">
            <Tag v-for="role in record.roles" :key="role" color="blue">{{ ROLE_LABEL[role] ?? role }}</Tag>
          </template>
          <template v-else-if="column.key === 'flags'">
            <Tag v-if="record.must_change_password" color="warning">待改密</Tag>
            <Tag v-if="record.totp_enabled" color="success">两步验证</Tag>
          </template>
          <template v-else-if="column.key === 'last'">{{ dateTime(record.last_login_at) }}</template>
          <template v-else-if="column.key === 'active'">
            <Switch :checked="record.is_active" :disabled="String(record.id) === me.userInfo?.userId"
                    @change="(v: any) => toggleActive(record, Boolean(v))" />
          </template>
          <template v-else-if="column.key === 'actions'">
            <Space size="small">
              <Button size="small" @click="openEdit(record)">编辑</Button>
              <Button size="small" @click="reset(record)">重置密码</Button>
              <Button size="small" @click="unlock(record)">解锁</Button>
            </Space>
          </template>
        </template>
      </Table>
    </Card>

    <Modal v-model:open="create.open" title="新建用户" ok-text="创建" @ok="submitCreate">
      <Form layout="vertical">
        <Form.Item label="用户名" required><Input v-model:value="create.username" :maxlength="64" /></Form.Item>
        <Form.Item label="显示名" required><Input v-model:value="create.display_name" :maxlength="64" /></Form.Item>
        <Form.Item label="角色"><Select v-model:value="create.roles" mode="multiple" :options="roles" /></Form.Item>
      </Form>
    </Modal>

    <Modal v-model:open="edit.open" :title="`编辑 ${edit.user?.username ?? ''}`" ok-text="保存" @ok="submitEdit">
      <Form layout="vertical">
        <Form.Item label="显示名"><Input v-model:value="edit.display_name" :maxlength="64" /></Form.Item>
        <Form.Item label="角色" extra="不能移除自己的管理员角色；至少保留一名启用的管理员">
          <Select v-model:value="edit.roles" mode="multiple" :options="roles" />
        </Form.Item>
      </Form>
    </Modal>

    <Modal v-model:open="secret.open" title="临时密码" :footer="null">
      <Alert type="warning" show-icon message="这个密码只显示一次，请通过安全渠道交给用户；首次登录后必须修改。" class="mb-3" />
      <p>用户名：<b>{{ secret.username }}</b></p>
      <p>临时密码：<Typography.Text copyable code>{{ secret.password }}</Typography.Text></p>
    </Modal>
  </Page>
</template>
