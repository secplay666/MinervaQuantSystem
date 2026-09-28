<script lang="ts" setup>
import { computed, onMounted, reactive, ref } from 'vue';

import { Page } from '@vben/common-ui';

import { Button, Card, Checkbox, Col, Form, Input, message, Modal, Row, Table, Tag } from 'ant-design-vue';

import { createRoleApi, permissionsApi, rolesApi, setRolePermissionsApi } from '#/api';

type Role = Awaited<ReturnType<typeof rolesApi>>[number];

const roles = ref<Role[]>([]);
const permissions = ref<Awaited<ReturnType<typeof permissionsApi>>>([]);
const selected = ref<string>('');
const draft = ref<string[]>([]);
const create = reactive({ code: '', description: '', name: '', open: false });

const role = computed(() => roles.value.find((r) => r.code === selected.value));
const groups = computed(() => {
  const out: Record<string, { code: string; name: string }[]> = {};
  for (const p of permissions.value) (out[p.group] ??= []).push(p);
  return out;
});
const dirty = computed(() => role.value && [...draft.value].sort().join() !== [...role.value.permissions].sort().join());

async function load() {
  const [r, p] = await Promise.all([rolesApi(), permissionsApi()]);
  roles.value = r;
  permissions.value = p;
  if (!selected.value && r.length) choose(r[0]!.code);
  else choose(selected.value);
}

function choose(code: string) {
  selected.value = code;
  draft.value = [...(roles.value.find((r) => r.code === code)?.permissions ?? [])];
}

async function save() {
  await setRolePermissionsApi(selected.value, draft.value);
  message.success('权限已更新，立即对该角色的所有用户生效');
  await load();
}

async function submitCreate() {
  if (!/^[a-z][a-z0-9_]{2,31}$/.test(create.code) || !create.name) {
    message.warning('编码为小写字母开头的 3–32 位字母、数字或下划线；名称必填');
    return;
  }
  await createRoleApi({ code: create.code, description: create.description, name: create.name, permissions: [] });
  create.open = false;
  selected.value = create.code;
  await load();
}

onMounted(load);
</script>

<template>
  <Page title="角色与权限" description="内置角色的权限由系统定义；需要自定义组合时新建角色，再逐项开关权限">
    <template #extra><Button type="primary" @click="create.open = true">新建角色</Button></template>
    <Row :gutter="16">
      <Col :xs="24" :md="8">
        <Card size="small" title="角色">
          <Table :data-source="roles" row-key="code" size="small" :pagination="false" class="cursor-pointer"
                 :columns="[{ dataIndex: 'name', title: '名称' }, { dataIndex: 'code', title: '编码' }, { key: 'builtin', title: '' }]"
                 :row-class-name="(record: Role) => (record.code === selected ? 'ant-table-row-selected' : '')"
                 :custom-row="(record: Role) => ({ onClick: () => choose(record.code) })">
            <template #bodyCell="{ column, record }">
              <Tag v-if="column.key === 'builtin' && record.builtin">内置</Tag>
            </template>
          </Table>
        </Card>
      </Col>
      <Col :xs="24" :md="16">
        <Card v-if="role" size="small" :title="`${role.name} 的权限`">
          <template #extra>
            <Button v-if="!role.builtin" type="primary" :disabled="!dirty" @click="save">保存</Button>
            <span v-else class="text-muted-foreground">内置角色只读</span>
          </template>
          <p v-if="role.description" class="text-muted-foreground mb-3">{{ role.description }}</p>
          <div v-for="(items, group) in groups" :key="group" class="mb-3">
            <div class="mb-1 font-semibold">{{ group }}</div>
            <Checkbox.Group v-model:value="draft" :disabled="role.builtin">
              <Checkbox v-for="p in items" :key="p.code" :value="p.code" class="mb-1 mr-4">{{ p.name }}（{{ p.code }}）</Checkbox>
            </Checkbox.Group>
          </div>
        </Card>
      </Col>
    </Row>

    <Modal v-model:open="create.open" title="新建角色" ok-text="创建" @ok="submitCreate">
      <Form layout="vertical">
        <Form.Item label="编码" required extra="例如 watcher"><Input v-model:value="create.code" :maxlength="32" /></Form.Item>
        <Form.Item label="名称" required><Input v-model:value="create.name" :maxlength="64" /></Form.Item>
        <Form.Item label="说明"><Input v-model:value="create.description" /></Form.Item>
      </Form>
    </Modal>
  </Page>
</template>
