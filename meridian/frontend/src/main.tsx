import React, { lazy, Suspense } from 'react'
import ReactDOM from 'react-dom/client'
import './index.css'

const App = lazy(() => import('./App'))
const DemoStage = lazy(() => import('./stage/DemoStage').then((module) => ({ default: module.DemoStage })))
const MeridianDeviceShowcase = lazy(() => import('./showcase/MeridianDeviceShowcase'))

/**
 * Lightweight path-based router.
 *
 * We deliberately avoid adding react-router (or any new dep) for the booth
 * demo. `/` redirects to the live showcase because the Summit talk only needs
 * that surface. Legacy `/demo-stage`, `/stage`, and `/pro` routes remain for
 * older local walkthrough links without becoming the public entry point.
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
    return <MeridianDeviceShowcase />
  }
  if (path === '/pro') {
    return <App />
  }
  window.location.replace('/showcase')
  return null
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <Suspense fallback={<div aria-label="Loading Meridian" />}>{pickRoot()}</Suspense>
  </React.StrictMode>,
)
