import type { RouteRecordRaw } from 'vue-router';

import { LOGIN_PATH } from '@vben/constants';
import { preferences } from '@vben/preferences';

const BasicLayout = () => import('#/layouts/basic.vue');
const AuthPageLayout = () => import('#/layouts/auth.vue');

/** 全局 404 */
const fallbackNotFoundRoute: RouteRecordRaw = {
  component: () => import('#/views/_core/fallback/not-found.vue'),
  meta: { hideInBreadcrumb: true, hideInMenu: true, hideInTab: true, title: '404' },
  name: 'FallbackNotFound',
  path: '/:path(.*)*',
};

/** 基本路由（不经过权限过滤）：根布局与登录页。账号由管理员创建，没有注册和找回密码。 */
const coreRoutes: RouteRecordRaw[] = [
  {
    component: BasicLayout,
    meta: { hideInBreadcrumb: true, title: 'Root' },
    name: 'Root',
    path: '/',
    redirect: preferences.app.defaultHomePath,
    children: [],
  },
  {
    component: AuthPageLayout,
    meta: { hideInTab: true, title: '登录' },
    name: 'Authentication',
    path: '/auth',
    redirect: LOGIN_PATH,
    children: [
      {
        name: 'Login',
        path: 'login',
        component: () => import('#/views/_core/authentication/login.vue'),
        meta: { title: '登录' },
      },
      {
        name: 'Register',
        path: 'register',
        component: () => import('#/views/_core/authentication/register.vue'),
        meta: { title: '注册' },
      },
    ],
  },
];

export { coreRoutes, fallbackNotFoundRoute };
