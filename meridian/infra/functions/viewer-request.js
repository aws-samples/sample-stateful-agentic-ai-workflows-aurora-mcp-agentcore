// Meridian viewer-request function (cloudfront-js-2.0).
//
// Three jobs on every request:
//   1. Basic auth at the edge. The expected credential lives in the CloudFront
//      KeyValueStore under "basic", written by scripts/publish.py, never in code.
//   2. For API paths, replace the browser's Authorization header with the
//      backend bearer token (KeyValueStore key "token"). The token never reaches
//      the browser; the backend refuses any caller without it.
//   3. For everything else, drop the Authorization header (S3 must not see it)
//      and rewrite client-side routes such as /showcase to /index.html.
import cf from 'cloudfront';

const kvs = cf.kvs();

function unauthorized() {
  return {
    statusCode: 401,
    statusDescription: 'Unauthorized',
    headers: { 'www-authenticate': { value: 'Basic realm="Meridian"' } },
  };
}

function isApi(uri) {
  return uri === '/health' || uri === '/api/health' || uri.startsWith('/api/');
}

async function handler(event) {
  const request = event.request;
  const supplied = request.headers.authorization ? request.headers.authorization.value : '';
  let expected;
  try {
    expected = await kvs.get('basic');
  } catch (err) {
    return unauthorized();
  }
  if (!supplied || supplied !== 'Basic ' + expected) {
    return unauthorized();
  }
  if (isApi(request.uri)) {
    let token;
    try {
      token = await kvs.get('token');
    } catch (err) {
      return { statusCode: 503, statusDescription: 'Backend token missing' };
    }
    request.headers.authorization = { value: 'Bearer ' + token };
    return request;
  }
  delete request.headers.authorization;
  if (request.uri === '/' || !request.uri.includes('.')) {
    request.uri = '/index.html';
  }
  return request;
}
