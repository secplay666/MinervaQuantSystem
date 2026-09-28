// Copy the server's CA certificate into the Android project before a build
// (network_security_config.xml trusts it for the server).  The certificate is
// instance-specific, so it is not committed:
//   MINERVA_CA_CERT=path/to/ca.crt  (default: ~/.config/minerva/tls/ca.crt)
import { copyFileSync, existsSync, mkdirSync } from 'node:fs';
import { homedir } from 'node:os';
import { dirname, join } from 'node:path';

const source = process.env.MINERVA_CA_CERT ?? join(homedir(), '.config', 'minerva', 'tls', 'ca.crt');
const target = join('android', 'app', 'src', 'main', 'res', 'raw', 'minerva_ca.crt');

if (!existsSync('android')) {
  console.error('android/ is missing: run `npx cap add android` once');
  process.exit(1);
}
if (!existsSync(source)) {
  console.error(`CA certificate not found at ${source}; set MINERVA_CA_CERT`);
  process.exit(1);
}
mkdirSync(dirname(target), { recursive: true });
copyFileSync(source, target);
console.log(`copied ${source} -> ${target}`);
