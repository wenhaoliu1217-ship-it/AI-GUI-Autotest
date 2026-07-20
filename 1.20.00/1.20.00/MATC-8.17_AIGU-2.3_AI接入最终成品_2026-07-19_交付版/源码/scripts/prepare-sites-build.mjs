import { copyFileSync, mkdirSync, readdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const dist = join(root, 'dist');
const serverDir = join(dist, 'server');
const openaiDir = join(dist, '.openai');

mkdirSync(serverDir, { recursive: true });
mkdirSync(openaiDir, { recursive: true });

copyFileSync(join(root, '.openai', 'hosting.json'), join(openaiDir, 'hosting.json'));

const assetsDir = join(dist, 'assets');
const indexHtml = readFileSync(join(dist, 'index.html'), 'utf8');
const assetEntries = readdirSync(assetsDir)
  .filter((filename) => filename.endsWith('.css') || filename.endsWith('.js'))
  .map((filename) => {
    const contentType = filename.endsWith('.css') ? 'text/css; charset=utf-8' : 'text/javascript; charset=utf-8';
    const content = readFileSync(join(assetsDir, filename), 'utf8');
    return [`/assets/${filename}`, { contentType, content }];
  });

writeFileSync(
  join(serverDir, 'index.js'),
  `const indexHtml = ${JSON.stringify(indexHtml)};
const assets = new Map(${JSON.stringify(assetEntries)});

export default {
  async fetch(request, env) {
    const pathname = new URL(request.url).pathname;
    const asset = assets.get(pathname);

    if (asset) {
      return new Response(asset.content, {
        headers: {
          'content-type': asset.contentType,
          'cache-control': 'public, max-age=31536000, immutable'
        }
      });
    }

    return new Response(indexHtml, {
      headers: {
        'content-type': 'text/html; charset=utf-8',
        'cache-control': 'no-cache'
      }
    });
  }
};
`,
  'utf8'
);
