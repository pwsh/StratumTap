#!/usr/bin/env node
// Copies the Leaflet distribution out of node_modules into stratumtap/static/vendor/leaflet/
// so the SPA has no runtime CDN dependency (the appliance may sit on an isolated network).
//
//   npm install && npm run vendor
//
// node_modules/ is gitignored; stratumtap/static/vendor/ is committed.
import { cp, mkdir, readFile, readdir, rm } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const src = join(root, 'node_modules', 'leaflet', 'dist');
const dest = join(root, 'stratumtap', 'static', 'vendor', 'leaflet');
const EXPECTED = '1.9.4';

if (!existsSync(src)) {
  console.error(`vendor: ${src} not found — run \`npm install\` first.`);
  process.exit(1);
}

const pkg = JSON.parse(await readFile(join(root, 'node_modules', 'leaflet', 'package.json'), 'utf8'));
if (pkg.version !== EXPECTED) {
  console.error(`vendor: expected leaflet@${EXPECTED}, found ${pkg.version}.`);
  process.exit(1);
}

await rm(dest, { recursive: true, force: true });
await mkdir(join(dest, 'images'), { recursive: true });

for (const f of ['leaflet.js', 'leaflet.css']) {
  await cp(join(src, f), join(dest, f));
}
for (const f of await readdir(join(src, 'images'))) {
  await cp(join(src, 'images', f), join(dest, 'images', f));
}

console.log(`vendor: leaflet@${pkg.version} -> stratumtap/static/vendor/leaflet/`);
