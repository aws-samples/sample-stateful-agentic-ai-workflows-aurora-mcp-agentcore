import React, { lazy, Suspense } from 'react'
import ReactDOM from 'react-dom/client'
import './index.css'
import { RouteSkeleton } from './components/RouteSkeleton'
import { AuthGate } from './auth/AuthGate'
import { readAuthConfig } from './auth/config'
import { initialShowcaseTheme } from './lib/showcaseTheme'

const MeridianDeviceShowcase = lazy(() => import('./showcase/MeridianDeviceShowcase'))

/**
 * Lightweight path-based router.
 *
 * Two routes do not justify react-router (or any new dependency). `/`
 * redirects to the showcase, the primary surface.
 */
function pickRoot() {
  const path = window.location.pathname.replace(/\/+$/, '')
  if (path === '') {
    window.location.replace('/showcase')
    return null
  }
  if (path === '/showcase' || path === '/device-showcase') {
    // Resolve before the lazy bundle so loading matches the requested theme.
    // The mounted showcase keeps this attribute in sync.
    document.documentElement.dataset.theme = initialShowcaseTheme()
    return <MeridianDeviceShowcase />
  }
  window.location.replace('/showcase')
  return null
}

// Both settings unset means this build has no sign-in and the API decides who is calling.
const authConfig = readAuthConfig({
  VITE_COGNITO_DOMAIN: import.meta.env.VITE_COGNITO_DOMAIN as string | undefined,
  VITE_COGNITO_CLIENT_ID: import.meta.env.VITE_COGNITO_CLIENT_ID as string | undefined,
}, window.location.origin)

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <AuthGate config={authConfig}>
      <Suspense fallback={<RouteSkeleton />}>{pickRoot()}</Suspense>
    </AuthGate>
  </React.StrictMode>,
)
