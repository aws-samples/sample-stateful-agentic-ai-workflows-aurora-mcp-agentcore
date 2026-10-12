// Answers the hosted sign-in's two requests inside a headless browser, so the app completes a
// real authorization-code flow with a seeded user's real tokens and nobody types a password.
// The authorize request is answered with a redirect carrying a synthetic code; the token request
// is answered with the tokens piped in from mint_session.py. The API and every layer behind it
// then verify those real tokens.
//
// Pass a pin ({ host, origin }) to answer only the one Cognito host the hosted site was built
// with, and only redirects back to the hosted site's own origin.
export const CODE = 'capture-code';
const COGNITO_HOST = /\.amazoncognito\.com$/;

function cognitoPath(urlText, pathname, host) {
  const url = new URL(urlText);
  const hostOk = host ? url.hostname === host : COGNITO_HOST.test(url.hostname);
  return hostOk && url.pathname === pathname;
}

export const isAuthorize = (urlText, host = null) => (
  cognitoPath(urlText, '/oauth2/authorize', host));
export const isToken = (urlText, host = null) => cognitoPath(urlText, '/oauth2/token', host);

export function authorizeRedirect(urlText, extra = {}, origin = null) {
  const url = new URL(urlText);
  const redirect = url.searchParams.get('redirect_uri');
  const state = url.searchParams.get('state');
  if (!redirect || !state) throw new Error('the authorize request has no redirect_uri or state');
  const back = new URL(redirect);
  if (origin && back.origin !== origin) {
    throw new Error(`the redirect goes to ${back.origin}, which is not the hosted site`);
  }
  for (const [key, value] of Object.entries(extra)) back.searchParams.set(key, value);
  back.searchParams.set('code', CODE);
  back.searchParams.set('state', state);
  return back.toString();
}

export function tokenResponse(tokens) {
  if (!tokens?.access || !tokens?.id) throw new Error('both tokens are needed: access and id');
  return {
    access_token: tokens.access, id_token: tokens.id, token_type: 'Bearer', expires_in: 3600,
  };
}

export async function installSession(page, tokens, extra = {}, pin = {}) {
  const { host = null, origin = null } = pin;
  await page.route((url) => isAuthorize(url.toString(), host), (route) => route.fulfill({
    status: 302,
    headers: { location: authorizeRedirect(route.request().url(), extra, origin) },
  }));
  await page.route((url) => isToken(url.toString(), host), (route) => route.fulfill({
    status: 200, contentType: 'application/json',
    headers: { 'access-control-allow-origin': '*' },
    body: JSON.stringify(tokenResponse(tokens)),
  }));
}

// The hosted site, from the non-secret release record written by publish.py.
export function hostedOrigin(recordText) {
  const siteUrl = JSON.parse(recordText)?.site?.SiteUrl ?? '';
  let url;
  try { url = new URL(siteUrl); } catch { url = null; }
  if (!url || url.protocol !== 'https:' || url.username || url.password) {
    throw new Error('the release record needs a credential-free https site.SiteUrl');
  }
  return url.origin;
}

// The Cognito hosted-UI host the site was built with, from the frontend settings file.
export function cognitoDomain(envText) {
  const line = envText.split('\n').find((l) => l.startsWith('VITE_COGNITO_DOMAIN='));
  if (!line) throw new Error('VITE_COGNITO_DOMAIN is missing from the frontend settings');
  const domain = line.slice(line.indexOf('=') + 1).trim()
    .replace(/^https?:\/\//, '').replace(/\/+$/, '');
  if (!/^[a-z0-9-]+\.auth\.[a-z0-9-]+\.amazoncognito\.com$/.test(domain)) {
    throw new Error('VITE_COGNITO_DOMAIN is not a Cognito hosted-UI host');
  }
  return domain;
}

// The tokens arrive only on an inherited pipe: capture_session.py creates it and passes its read
// end here as --token-fd N. They are never read from standard input, argv or the environment.
export function tokenFdFromArgs(argv) {
  const rest = [];
  let text = null;
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === '--token-fd') { text = argv[i + 1]; i += 1; }
    else if (argv[i].startsWith('--token-fd=')) text = argv[i].slice('--token-fd='.length);
    else rest.push(argv[i]);
  }
  if (text === null || !/^\d+$/.test(text) || Number(text) < 3) {
    throw new Error('--token-fd N (3 or higher) is required; run capture_session.py');
  }
  return { fd: Number(text), rest };
}

export function readTokenPipe(fd, fsApi) {
  let isPipe;
  try {
    isPipe = fsApi.fstatSync(fd).isFIFO();
  } catch {
    throw new Error(`descriptor ${fd} is not open`);
  }
  if (!isPipe) throw new Error(`descriptor ${fd} is not a pipe`);
  let tokens;
  try {
    tokens = JSON.parse(fsApi.readFileSync(fd, 'utf8'));
  } catch {
    throw new Error('the token message is not the JSON that mint_session.py writes');
  }
  if (!tokens?.jordan?.access || !tokens?.jordan?.id) {
    throw new Error('the token message has no tokens for jordan');
  }
  return tokens;
}
