import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const source = readFileSync(resolve(process.cwd(), 'playwright.config.ts'), 'utf8');
const commands = [...source.matchAll(/command:\s*'([^']+)'/g)].map(match => match[1]);

describe('playwright.config.ts web servers', () => {
  it('starts one ungated server and one gated server', () => {
    expect(commands).toHaveLength(2);
  });

  it('keeps the ungated suite out of Vite development mode, which loads .env.development.local', () => {
    const ungated = commands.find(command => command.includes('--port 4174'));
    expect(ungated).toBeDefined();
    expect(ungated).toContain('--mode e2e');
    expect(ungated).toContain('--strictPort');
    expect(ungated).not.toMatch(/--mode (development|gated)/);
  });

  it('runs the gated server in its own mode on its own port with inline placeholder settings', () => {
    const gated = commands.find(command => command.includes('--port 4175'));
    expect(gated).toBeDefined();
    expect(gated).toContain('--mode gated-e2e');
    expect(gated).toContain('--strictPort');
    expect(source).toMatch(/VITE_COGNITO_DOMAIN:\s*'[a-z0-9.-]+\.example\.test'/);
    expect(source).toMatch(/VITE_COGNITO_CLIENT_ID:\s*'[a-z0-9-]+'/);
  });
});
