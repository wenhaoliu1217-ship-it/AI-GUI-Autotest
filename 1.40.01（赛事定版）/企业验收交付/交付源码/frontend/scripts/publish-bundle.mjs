import { cpSync, existsSync, mkdirSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const sourceRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const repoRoot = resolve(sourceRoot, '..');
const buildRoot = join(sourceRoot, 'dist');
const targetRoot = join(repoRoot, 'frontend-dist');

if (!existsSync(join(buildRoot, 'index.html'))) {
  throw new Error(`Build output is missing: ${join(buildRoot, 'index.html')}`);
}
if (resolve(targetRoot).split(/[/\\]/).at(-1) !== 'frontend-dist') {
  throw new Error(`Refusing to publish to an unexpected target: ${targetRoot}`);
}

rmSync(targetRoot, { recursive: true, force: true });
mkdirSync(targetRoot, { recursive: true });
cpSync(buildRoot, targetRoot, { recursive: true });

const bundleVersion = '1.40.01';
const assets = {};
const assetsRoot = join(targetRoot, 'assets');
for (const name of readdirSync(assetsRoot).filter((item) => /\.(css|js)$/.test(item))) {
  const file = join(assetsRoot, name);
  const bytes = statSync(file).size;
  const sha256 = createHash('sha256').update(readFileSync(file)).digest('hex');
  assets[`assets/${name}`] = { bytes, sha256 };
}

const manifest = {
  schemaVersion: 1,
  bundleVersion,
  apiContractVersion: 'ai-gui-http-v1',
  certifiedBackendVersion: bundleVersion,
  entry: 'index.html',
  assets,
  policy: {
    entryHtmlNoCache: true,
    hashedAssetsImmutable: true,
    unknownAssetsRejected: false,
  },
};
writeFileSync(join(targetRoot, 'bundle-manifest.json'), `${JSON.stringify(manifest, null, 2)}\n`, 'utf8');

console.log(`Published ${bundleVersion} frontend to ${targetRoot}`);
for (const [name, value] of Object.entries(assets)) {
  console.log(`${name}: ${value.bytes} bytes sha256=${value.sha256}`);
}
