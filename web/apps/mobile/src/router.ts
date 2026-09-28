import { createRouter, createWebHashHistory } from 'vue-router';

import { auth } from './store';

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { component: () => import('./views/Login.vue'), meta: { public: true }, path: '/login' },
    { component: () => import('./views/Home.vue'), meta: { tab: true }, path: '/' },
    { component: () => import('./views/Decisions.vue'), meta: { tab: true }, path: '/decisions' },
    { component: () => import('./views/Decision.vue'), path: '/decisions/:runId' },
    { component: () => import('./views/Accounts.vue'), meta: { tab: true }, path: '/accounts' },
    { component: () => import('./views/Account.vue'), path: '/accounts/:accountId' },
    { component: () => import('./views/Instrument.vue'), path: '/instruments/:symbol' },
    { component: () => import('./views/Events.vue'), meta: { tab: true }, path: '/events' },
    { component: () => import('./views/Me.vue'), meta: { tab: true }, path: '/me' },
  ],
});

router.beforeEach((to) => {
  if (!to.meta.public && !auth.access) return '/login';
  if (auth.user?.must_change_password && to.path !== '/me' && !to.meta.public) return '/me';
  return true;
});

export default router;
