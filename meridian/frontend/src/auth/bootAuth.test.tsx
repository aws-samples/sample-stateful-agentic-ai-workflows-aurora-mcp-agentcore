import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ConfigProblemScreen } from './ConfigProblemScreen';
import { loadAuthConfig } from './bootAuth';

const ORIGIN = 'https://s.test';

afterEach(() => vi.restoreAllMocks());

describe('loadAuthConfig', () => {
  it('logs one line naming an ungated build when sign-in is not configured', () => {
    const info = vi.spyOn(console, 'info').mockImplementation(() => undefined);
    expect(loadAuthConfig({}, ORIGIN)).toEqual({ config: null, failed: false });
    expect(info).toHaveBeenCalledTimes(1);
    expect(info.mock.calls[0][0]).toMatch(/ungated build/i);
  });

  it('returns the settings without logging when sign-in is configured', () => {
    const info = vi.spyOn(console, 'info').mockImplementation(() => undefined);
    const boot = loadAuthConfig(
      { VITE_COGNITO_DOMAIN: 'd.auth.example.test', VITE_COGNITO_CLIENT_ID: 'client-web' }, ORIGIN,
    );
    expect(boot.failed).toBe(false);
    expect(boot.config?.clientId).toBe('client-web');
    expect(info).not.toHaveBeenCalled();
  });

  it('reports a half configured build instead of throwing', () => {
    const error = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    expect(loadAuthConfig({ VITE_COGNITO_CLIENT_ID: 'client-web' }, ORIGIN))
      .toEqual({ config: null, failed: true });
    expect(error).toHaveBeenCalledTimes(1);
  });
});

describe('ConfigProblemScreen', () => {
  it('says plainly that sign-in is not configured correctly', () => {
    render(<ConfigProblemScreen />);
    expect(screen.getByRole('heading', { level: 1 }))
      .toHaveTextContent('Sign-in is not configured correctly');
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });
});
