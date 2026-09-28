import type { ComponentRecordType, GenerateMenuAndRoutesOptions } from '@vben/types';

import { generateAccessible } from '@vben/access';
import { preferences } from '@vben/preferences';

import { BasicLayout, IFrameView } from '#/layouts';

const forbiddenComponent = () => import('#/views/_core/fallback/forbidden.vue');

/**
 * Frontend access mode: routes are defined in src/router/routes/modules and
 * filtered by ``meta.authority`` (permission codes) against the user's
 * permissions, which the backend decides (docs/design/stage4-app-design.md §6).
 */
async function generateAccess(options: GenerateMenuAndRoutesOptions) {
  const pageMap: ComponentRecordType = import.meta.glob('../views/**/*.vue');
  return await generateAccessible(preferences.app.accessMode, {
    ...options,
    forbiddenComponent,
    layoutMap: { BasicLayout, IFrameView },
    pageMap,
  });
}

export { generateAccess };
