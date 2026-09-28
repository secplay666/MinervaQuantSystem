import {
  defineOverridesPreferences,
  definePreferencesExtension,
} from '@vben/preferences';

interface WebAntdPreferencesExtension {
  defaultTableSize: number;
}

/**
 * Minerva preferences (only overrides; the rest are vben defaults).
 * Clear the browser cache after changing them.
 */
export const overridesPreferences = defineOverridesPreferences({
  app: {
    accessMode: 'frontend',
    defaultHomePath: '/home',
    enableCheckUpdates: false,
    enablePreferences: true,
    enableRefreshToken: true,
    loginExpiredMode: 'page',
    name: import.meta.env.VITE_APP_TITLE,
    watermark: false,
  },
  copyright: {
    companyName: 'Minerva',
    companySiteLink: '',
    date: '2026',
    enable: true,
    icp: '',
    icpLink: '',
  },
  logo: {
    enable: true,
    source: '/favicon.ico',
  },
  widget: {
    globalSearch: false,
    languageToggle: false,
    lockScreen: true,
    notification: true,
    refresh: true,
    sidebarToggle: true,
    themeToggle: true,
  },
});

export const preferencesExtension = definePreferencesExtension<WebAntdPreferencesExtension>({
  tabLabel: '表格',
  title: '表格偏好',
  fields: [
    {
      component: 'number',
      componentProps: { max: 200, min: 10, step: 10 },
      defaultValue: 20,
      key: 'defaultTableSize',
      label: '默认每页条数',
    },
  ],
});
