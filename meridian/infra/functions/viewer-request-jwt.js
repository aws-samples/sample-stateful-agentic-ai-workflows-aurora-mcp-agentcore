// Meridian viewer-request function for the Cognito release (cloudfront-js-2.0).
//
// The site is public and the API is not: the backend verifies the Cognito access token the
// browser sends, so this function holds no credential and reads no KeyValueStore.
//   1. For API paths, pass the request through with the browser's own Authorization header.
//   2. For everything else, drop the Authorization header (S3 must not see it) and rewrite
//      client-side routes such as /showcase to /index.html.

function isApi(uri) {
  return uri === '/health' || uri === '/api/health' || uri.startsWith('/api/');
}

async function handler(event) {
  const request = event.request;
  if (isApi(request.uri)) {
    return request;
  }
  delete request.headers.authorization;
  if (request.uri === '/' || !request.uri.includes('.')) {
    request.uri = '/index.html';
  }
  return request;
}
