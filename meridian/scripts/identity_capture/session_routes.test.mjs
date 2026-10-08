import test from 'node:test';
import assert from 'node:assert/strict';
import {
  CODE, authorizeRedirect, cognitoDomain, hostedOrigin, installSession, isAuthorize, isToken,
  tokenResponse,
} from './session_routes.mjs';

const HOST = 'https://meridian-travelers-test.auth.us-east-1.amazoncognito.com';
const AUTHORIZE = `${HOST}/oauth2/authorize?response_type=code&client_id=abc`
  + '&redirect_uri=https%3A%2F%2Fsite.example.net%2Fshowcase&state=s123&code_challenge=x';

test('only the hosted page\'s authorize and token requests are recognized', () => {
  assert.equal(isAuthorize(AUTHORIZE), true);
  assert.equal(isToken(`${HOST}/oauth2/token`), true);
  assert.equal(isAuthorize('https://evil.example.com/oauth2/authorize'), false);
  assert.equal(isToken(`${HOST}/oauth2/revoke`), false);
  assert.equal(isToken('https://site.example.net/api/me'), false);
});

test('a pinned host recognizes only that host', () => {
  const pinned = new URL(HOST).hostname;
  const other = 'https://other-pool.auth.us-east-1.amazoncognito.com/oauth2/token';
  assert.equal(isToken(`${HOST}/oauth2/token`, pinned), true);
  assert.equal(isToken(other, pinned), false);
  assert.equal(isAuthorize(other.replace('token', 'authorize'), pinned), false);
});

test('the redirect carries the state, a code and the extra parameters back to the page', () => {
  const back = new URL(authorizeRedirect(AUTHORIZE, { present: '1', view: 'ladder' }));
  assert.equal(back.origin + back.pathname, 'https://site.example.net/showcase');
  assert.equal(back.searchParams.get('state'), 's123');
  assert.equal(back.searchParams.get('code'), CODE);
  assert.equal(back.searchParams.get('present'), '1');
  assert.equal(back.searchParams.get('view'), 'ladder');
});

test('an authorize request without state or redirect is refused', () => {
  const bare = `${HOST}/oauth2/authorize?client_id=abc`;
  assert.throws(() => authorizeRedirect(bare), /no redirect_uri/);
});

test('a redirect to another origin than the hosted site is refused', () => {
  assert.throws(
    () => authorizeRedirect(AUTHORIZE, {}, 'https://other.example.net'), /not the hosted site/);
  assert.ok(authorizeRedirect(AUTHORIZE, {}, 'https://site.example.net'));
});

test('the token response is a bearer response without a refresh token', () => {
  const body = tokenResponse({ access: 'a', id: 'i' });
  assert.deepEqual(
    body, { access_token: 'a', id_token: 'i', token_type: 'Bearer', expires_in: 3600 });
  assert.equal('refresh_token' in body, false);
  assert.throws(() => tokenResponse({ access: 'a' }), /both tokens/);
});

test('installSession answers the authorize and token requests', async () => {
  const routes = [];
  const page = { route: async (match, handler) => { routes.push({ match, handler }); } };
  await installSession(page, { access: 'a', id: 'i' }, { present: '1' });
  assert.equal(routes.length, 2);

  const fulfilled = [];
  const request = (url) => ({
    request: () => ({ url: () => url }),
    fulfill: async (options) => fulfilled.push(options),
  });
  const authorize = routes.find((r) => r.match(new URL(AUTHORIZE)));
  await authorize.handler(request(AUTHORIZE));
  assert.equal(fulfilled[0].status, 302);
  assert.match(fulfilled[0].headers.location, /code=capture-code/);

  const tokenUrl = `${HOST}/oauth2/token`;
  const token = routes.find((r) => r.match(new URL(tokenUrl)));
  await token.handler(request(tokenUrl));
  assert.equal(JSON.parse(fulfilled[1].body).access_token, 'a');
  assert.equal(fulfilled[1].headers['access-control-allow-origin'], '*');
});

test('a pinned session leaves another pool\'s requests alone', async () => {
  const routes = [];
  const page = { route: async (match, handler) => { routes.push({ match, handler }); } };
  const pin = { host: new URL(HOST).hostname, origin: 'https://site.example.net' };
  await installSession(page, { access: 'a', id: 'i' }, {}, pin);
  const other = new URL('https://other-pool.auth.us-east-1.amazoncognito.com/oauth2/token');
  assert.equal(routes.some((r) => r.match(other)), false);
  assert.equal(routes.some((r) => r.match(new URL(`${HOST}/oauth2/token`))), true);
});

test('the hosted origin is read from a release record and must be a plain https site', () => {
  const record = (url) => JSON.stringify({ site: { SiteUrl: url } });
  assert.equal(hostedOrigin(record('https://d111.cloudfront.net/')), 'https://d111.cloudfront.net');
  assert.throws(() => hostedOrigin(record('http://d111.cloudfront.net')), /https/);
  assert.throws(() => hostedOrigin(record('https://u:p@d111.cloudfront.net')), /https/);
  assert.throws(() => hostedOrigin('{}'), /https/);
});

test('the Cognito domain comes from the frontend settings and must be a hosted-UI host', () => {
  const env = 'VITE_COGNITO_CLIENT_ID=abc\n'
    + 'VITE_COGNITO_DOMAIN=https://x.auth.us-east-1.amazoncognito.com/\n';
  assert.equal(cognitoDomain(env), 'x.auth.us-east-1.amazoncognito.com');
  assert.throws(() => cognitoDomain('VITE_COGNITO_DOMAIN=evil.example.com'), /hosted-UI/);
  assert.throws(() => cognitoDomain('VITE_X=1'), /VITE_COGNITO_DOMAIN/);
});
