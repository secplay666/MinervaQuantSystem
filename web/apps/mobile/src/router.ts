import { createRouter, createWebHashHistory } from 'vue-router';

import { auth } from './store';

/** ``tab``: shows the tab bar.  ``permission``: the page needs it (hubs hide the entry, the guard sends home). */
const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { component: () => import('./views/Login.vue'), meta: { public: true }, path: '/login' },
    { component: () => import('./views/Home.vue'), meta: { tab: true }, path: '/' },
    // 行情
    { component: () => import('./views/market/Hub.vue'), meta: { tab: true }, path: '/market' },
    { component: () => import('./views/market/Overview.vue'), meta: { permission: 'market:view' }, path: '/market/overview' },
    { component: () => import('./views/market/Chart.vue'), meta: { permission: 'market:view' }, path: '/chart/:symbol' },
    { component: () => import('./views/Instrument.vue'), meta: { permission: 'market:view' }, path: '/instruments/:symbol' },
    { component: () => import('./views/market/Etf.vue'), meta: { permission: 'market:view' }, path: '/etf' },
    { component: () => import('./views/market/MoneyMap.vue'), meta: { permission: 'market:view' }, path: '/moneymap' },
    { component: () => import('./views/market/Breadth.vue'), meta: { permission: 'market:view' }, path: '/breadth' },
    { component: () => import('./views/market/CompanyActions.vue'), meta: { permission: 'market:view' }, path: '/company-actions' },
    { component: () => import('./views/market/BigMoney.vue'), meta: { permission: 'market:view' }, path: '/big-money' },
    { component: () => import('./views/market/Options.vue'), meta: { permission: 'market:view' }, path: '/options' },
    { component: () => import('./views/market/Volatility.vue'), meta: { permission: 'market:view' }, path: '/volatility' },
    // 仓位管家
    { component: () => import('./views/pm/Board.vue'), meta: { permission: 'position:use', tab: true }, path: '/pm' },
    { component: () => import('./views/pm/Library.vue'), meta: { permission: 'position:use' }, path: '/pm/library' },
    { component: () => import('./views/pm/Lists.vue'), meta: { permission: 'position:use' }, path: '/pm/breakouts', props: { kind: 'breakouts' } },
    { component: () => import('./views/pm/Lists.vue'), meta: { permission: 'position:use' }, path: '/pm/tops', props: { kind: 'tops' } },
    { component: () => import('./views/pm/Campaigns.vue'), meta: { permission: 'position:use' }, path: '/pm/campaigns' },
    { component: () => import('./views/pm/Backtest.vue'), meta: { permission: 'position:use' }, path: '/pm/backtest' },
    { component: () => import('./views/pm/Settings.vue'), meta: { permission: 'position:use' }, path: '/pm/settings' },
    { component: () => import('./views/pm/Item.vue'), meta: { permission: 'position:use' }, path: '/pm/items/:id' },
    // 决策与账户
    { component: () => import('./views/Decisions.vue'), meta: { tab: true }, path: '/decisions' },
    { component: () => import('./views/Decision.vue'), path: '/decisions/:runId' },
    { component: () => import('./views/Accounts.vue'), path: '/accounts' },
    { component: () => import('./views/Account.vue'), path: '/accounts/:accountId' },
    // 我的
    { component: () => import('./views/Events.vue'), path: '/events' },
    { component: () => import('./views/DataHealth.vue'), meta: { permission: 'data:view' }, path: '/data' },
    { component: () => import('./views/Me.vue'), meta: { tab: true }, path: '/me' },
  ],
});

router.beforeEach((to) => {
  if (!to.meta.public && !auth.access) return '/login';
  if (auth.user?.must_change_password && to.path !== '/me' && !to.meta.public) return '/me';
  const needed = to.meta.permission as string | undefined;
  if (needed && auth.user && !auth.user.permissions.includes(needed)) return '/';
  return true;
});

export default router;
