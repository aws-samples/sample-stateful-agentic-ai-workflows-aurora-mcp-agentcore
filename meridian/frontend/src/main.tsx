import React, { lazy, Suspense } from 'react'
import ReactDOM from 'react-dom/client'
import '@fontsource-variable/geist'
import '@fontsource-variable/geist-mono'
import './index.css'
import { RouteSkeleton } from './components/RouteSkeleton'
import { initialShowcaseTheme } from './lib/showcaseTheme'

const DemoStage = lazy(() => import('./stage/DemoStage').then((module) => ({ default: module.DemoStage })))
const MeridianDeviceShowcase = lazy(() => import('./showcase/MeridianDeviceShowcase'))

/**
 * Lightweight path-based router.
 *
 * We deliberately avoid adding react-router (or any new dep) for the booth
 * demo. `/` redirects to the live showcase because the chalk talk only needs
 * that surface. `/demo-stage` and `/stage` remain for kiosk and presenter use.
 */
function pickRoot() {
  const path = window.location.pathname.replace(/\/+$/, '')
  if (path === '') {
    window.location.replace('/showcase')
    return null
  }
  if (path === '/demo-stage' || path === '/stage') {
    return <DemoStage />
  }
  if (path === '/showcase' || path === '/device-showcase') {
    // Resolve before the lazy bundle so a light presentation never flashes a
    // dark loading screen. The mounted showcase keeps this attribute in sync.
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
