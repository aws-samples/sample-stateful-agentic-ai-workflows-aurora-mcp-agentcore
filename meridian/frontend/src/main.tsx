import React, { lazy, Suspense } from 'react'
import ReactDOM from 'react-dom/client'
import './index.css'
import { RouteSkeleton } from './components/RouteSkeleton'
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

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <Suspense fallback={<RouteSkeleton />}>{pickRoot()}</Suspense>
  </React.StrictMode>,
)
