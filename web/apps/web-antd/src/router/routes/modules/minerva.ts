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
    name: 'EtfFlows',
    path: '/etf',
    component: () => import('#/views/market/etf.vue'),
    meta: { authority: ['market:view'], icon: 'lucide:landmark', order: 3.5, title: 'ETF 资金' },
  },
  {
    name: 'Events',
    path: '/events',
    component: () => import('#/views/events/index.vue'),
    meta: { authority: ['event:view'], icon: 'lucide:bell', order: 4, title: '通知中心' },
  },
  {
    name: 'ChartGroup',
    path: '/watch',
    meta: { authority: ['market:view', 'position:use'], icon: 'lucide:chart-candlestick', order: 4, title: '看盘与仓位' },
    children: [
      {
        name: 'Chart',
        path: '/chart', // ?symbol=600000 or sh000001; one tab for all symbols (fullPathKey: false)
        component: () => import('#/views/market/chart.vue'),
        meta: { authority: ['market:view'], fullPathKey: false, icon: 'lucide:chart-candlestick', title: '看盘' },
      },
      {
        name: 'MoneyMap',
        path: '/moneymap',
        component: () => import('#/views/market/money-map.vue'),
        meta: { authority: ['market:view'], icon: 'lucide:map', title: '钱去哪地图' },
      },
      {
        name: 'MarketBreadth',
        path: '/breadth',
        component: () => import('#/views/market/breadth.vue'),
        meta: { authority: ['market:view'], icon: 'lucide:activity', title: '市场宽度' },
      },
      {
        name: 'CompanyActions',
        path: '/company-actions',
        component: () => import('#/views/market/company-actions.vue'),
        meta: { authority: ['market:view'], icon: 'lucide:hand-coins', title: '回购增持' },
      },
      {
        name: 'PmBoard',
        path: '/position/board',
        component: () => import('#/views/position/board.vue'),
        meta: { authority: ['position:use'], icon: 'lucide:gauge', title: '仓位看板' },
      },
      {
        name: 'PmLibrary',
        path: '/position/library',
        component: () => import('#/views/position/library.vue'),
        meta: { authority: ['position:use'], icon: 'lucide:library', title: '标的库' },
      },
      {
        name: 'PmBreakouts',
        path: '/position/breakouts',
        component: () => import('#/views/position/lists.vue'),
        props: { kind: 'breakouts' },
        meta: { authority: ['position:use'], icon: 'lucide:trending-up', title: '突破确立' },
      },
      {
        name: 'PmTops',
        path: '/position/tops',
        component: () => import('#/views/position/lists.vue'),
        props: { kind: 'tops' },
        meta: { authority: ['position:use'], icon: 'lucide:mountain', title: '头部确立' },
      },
      {
        name: 'PmCampaigns',
        path: '/position/campaigns',
        component: () => import('#/views/position/campaigns.vue'),
        meta: { authority: ['position:use'], icon: 'lucide:scroll', title: '战役总账' },
      },
      {
        name: 'PmBacktest',
        path: '/position/backtest',
        component: () => import('#/views/position/backtest.vue'),
        meta: { authority: ['position:use'], icon: 'lucide:flask-conical', title: '阶段回测' },
      },
      {
        name: 'PmSettings',
        path: '/position/settings',
        component: () => import('#/views/position/settings.vue'),
        meta: { authority: ['position:use'], icon: 'lucide:sliders-horizontal', title: '规则设置' },
      },
      {
        name: 'PmItem',
        path: '/position/items/:id',
        component: () => import('#/views/position/item.vue'),
        meta: { activePath: '/position/library', authority: ['position:use'], hideInMenu: true, title: '标的结构' },
      },
    ],
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
