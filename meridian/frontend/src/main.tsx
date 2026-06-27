import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import { DemoStage } from './stage/DemoStage'
import { MeridianDeviceShowcase } from './showcase/MeridianDeviceShowcase'
import './index.css'

/**
 * Lightweight path-based router.
 *
 * We deliberately avoid adding react-router (or any new dep) for the booth
 * demo. `/` redirects to the live showcase because the Summit talk only needs
 * that surface. `/demo-stage` and `/stage` still mount the cinematic Demo
 * Stage for kiosk use; `/pro` keeps the old overview available for local
 * builder walkthroughs without making it the public entry point.
 *
 * Kiosk loop:           open /demo-stage?kiosk=1
 * Builder (technical):  press B once on the stage, or append ?view=builder
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
  <React.StrictMode>{pickRoot()}</React.StrictMode>,
)
