import { readAuthConfig, type AuthConfig, type AuthEnv } from './config';

export interface AuthBoot {
  config: AuthConfig | null;
  /** The settings are present but wrong, so the page cannot offer sign-in. */
  failed: boolean;
}

/**
 * Read the sign-in settings once at start. A build without them says so in the console, and a
 * mistake in them becomes a state the page can show rather than an uncaught error.
 */
export function loadAuthConfig(env: AuthEnv, origin: string): AuthBoot {
  try {
    const config = readAuthConfig(env, origin);
    if (!config) {
      console.info('Meridian is running as an ungated build: sign-in is not configured.');
    }
    return { config, failed: false };
  } catch (error) {
    console.error(error instanceof Error ? error.message : 'Sign-in settings are invalid.');
    return { config: null, failed: true };
  }
}
