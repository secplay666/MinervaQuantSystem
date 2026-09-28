import type { CapacitorConfig } from '@capacitor/cli';

const config: CapacitorConfig = {
  appId: 'com.minerva.decision',
  appName: 'Minerva 决策',
  webDir: 'dist',
  android: { allowMixedContent: false },
  plugins: {
    // Native HTTP for fetch/XHR: TLS follows android/.../network_security_config.xml
    // (the private CA is trusted only there), and there is no CORS.
    CapacitorHttp: { enabled: true },
  },
  server: { androidScheme: 'https' },
};

export default config;
