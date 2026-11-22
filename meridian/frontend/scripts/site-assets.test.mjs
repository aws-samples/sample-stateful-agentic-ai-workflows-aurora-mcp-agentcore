// @vitest-environment node
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

// The site keeps every file under assets/ across releases and serves it as immutable for a year
// (infra/lib/meridian-web-stack.ts). That is safe only while each name there carries its content
// hash, which Vite's default naming guarantees for what it builds into assets/.
const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

describe('files under assets/', () => {
  it('come only from the Vite build, never from public/', () => {
    expect(fs.existsSync(path.join(frontend, 'public', 'assets'))).toBe(false);
  });

  it('keep Vite’s content-hashed names', () => {
    const config = fs.readFileSync(path.join(frontend, 'vite.config.ts'), 'utf8');
    expect(config).not.toMatch(/assetsDir|assetFileNames|chunkFileNames|entryFileNames/u);
  });

  it('carry a hash in every built name, when a build is present', () => {
    const built = path.join(frontend, 'dist', 'assets');
    if (!fs.existsSync(built)) return;
    for (const name of fs.readdirSync(built)) {
      expect(name, name).toMatch(/-[A-Za-z0-9_-]{8}\.[a-z0-9]+$/u);
    }
  });
});
