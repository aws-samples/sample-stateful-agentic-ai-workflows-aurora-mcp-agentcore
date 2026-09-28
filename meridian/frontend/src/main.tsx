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
 * Three routes do not justify react-router (or any new dependency). `/`
 * redirects to the showcase, the primary surface. `/demo-stage` and `/stage`
 * serve the kiosk and playback surface.
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
