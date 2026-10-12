/**
 * Stops a build that must ship with sign-in from shipping without it. Without the Cognito
 * settings the bundle runs ungated, so the publish pipeline sets VITE_REQUIRE_SIGN_IN=1 and a
 * lost variable fails the build instead of quietly turning sign-in off. Plain builds are
 * unaffected.
 *
 * @param {Record<string, string | undefined>} env The VITE_ variables of the build.
 */
export function assertSignInConfigured(env) {
  if (env.VITE_REQUIRE_SIGN_IN?.trim() !== '1') return;
  if (env.VITE_COGNITO_DOMAIN?.trim() && env.VITE_COGNITO_CLIENT_ID?.trim()) return;
  throw new Error(
    'VITE_REQUIRE_SIGN_IN=1 needs sign-in settings, but VITE_COGNITO_DOMAIN and '
    + 'VITE_COGNITO_CLIENT_ID are not both set. Set both for this build, or unset '
    + 'VITE_REQUIRE_SIGN_IN to build without sign-in.',
  );
}
