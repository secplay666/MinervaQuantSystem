import type { RouteRecordRaw } from 'vue-router';

/**
 * Menus.  ``meta.authority`` lists permission codes; a route is visible when
 * the user holds any of them (src/api/core/user.ts puts permissions in roles).
 */
const routes: RouteRecordRaw[] = [
  {
    name: 'Home',
    path: '/home',
    component: () => import('#/views/home/index.vue'),
    meta: { affixTab: true, authority: ['dashboard:view'], icon: 'lucide:layout-dashboard', order: -1, title: '首页' },
  },
  {
    name: 'Decisions',
    path: '/decisions',
    component: () => import('#/views/decisions/list.vue'),
    meta: { authority: ['decision:view'], icon: 'lucide:clipboard-check', order: 1, title: '每日决策' },
  },
  {
    name: 'DecisionDetail',
    path: '/decisions/:runId',
    component: () => import('#/views/decisions/detail.vue'),
    meta: { activePath: '/decisions', authority: ['decision:view'], hideInMenu: true, title: '决策详情' },
  },
  {
    name: 'Accounts',
    path: '/accounts',
    component: () => import('#/views/accounts/list.vue'),
    meta: { authority: ['account:view'], icon: 'lucide:wallet', order: 2, title: '账户' },
  },
  {
    name: 'AccountDetail',
    path: '/accounts/:accountId',
    component: () => import('#/views/accounts/detail.vue'),
    meta: { activePath: '/accounts', authority: ['account:view'], hideInMenu: true, title: '账户详情' },
  },
  {
    name: 'Market',
    path: '/market',
    component: () => import('#/views/market/index.vue'),
    meta: { authority: ['market:view'], icon: 'lucide:candlestick-chart', order: 3, title: '市场' },
  },
  {
    name: 'Instrument',
    path: '/instruments/:symbol',
    component: () => import('#/views/market/instrument.vue'),
    meta: { activePath: '/market', authority: ['market:view'], hideInMenu: true, title: '个股' },
  },
  {
    name: 'Events',
    path: '/events',
    component: () => import('#/views/events/index.vue'),
    meta: { authority: ['event:view'], icon: 'lucide:bell', order: 4, title: '通知中心' },
  },
  {
    name: 'DataHealth',
    path: '/data',
    component: () => import('#/views/data/index.vue'),
    meta: { authority: ['data:view'], icon: 'lucide:database', order: 5, title: '数据健康' },
  },
  {
    name: 'System',
    path: '/system',
    meta: { authority: ['user:manage', 'audit:view', 'notify:manage'], icon: 'lucide:settings', order: 9, title: '系统管理' },
    children: [
      {
        name: 'Users',
        path: '/system/users',
        component: () => import('#/views/system/users.vue'),
        meta: { authority: ['user:manage'], icon: 'lucide:users', title: '账号管理' },
      },
      {
        name: 'Roles',
        path: '/system/roles',
        component: () => import('#/views/system/roles.vue'),
        meta: { authority: ['user:manage'], icon: 'lucide:shield-check', title: '角色与权限' },
      },
      {
        name: 'Sessions',
        path: '/system/sessions',
        component: () => import('#/views/system/sessions.vue'),
        meta: { authority: ['user:manage'], icon: 'lucide:monitor-smartphone', title: '在线会话' },
      },
      {
        name: 'Audit',
        path: '/system/audit',
        component: () => import('#/views/system/audit.vue'),
        meta: { authority: ['audit:view'], icon: 'lucide:scroll-text', title: '审计日志' },
      },
      {
        name: 'Notify',
        path: '/system/notify',
        component: () => import('#/views/system/notify.vue'),
        meta: { authority: ['notify:manage'], icon: 'lucide:bell-ring', title: '外部通知' },
      },
    ],
  },
  {
    name: 'Profile',
    path: '/profile',
    component: () => import('#/views/_core/profile/index.vue'),
    meta: { hideInMenu: true, icon: 'lucide:user', title: '个人设置' },
  },
];

export default routes;
