import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import ConciergeApp from './ConciergeApp'
import { DemoStage } from './stage/DemoStage'
import { MeridianDeviceShowcase } from './showcase/MeridianDeviceShowcase'
import { MeridianProChalkTalk } from './pro/MeridianProChalkTalk'
import './index.css'

/**
 * Lightweight path-based router.
 *
 * We deliberately avoid adding react-router (or any new dep) for the booth
 * demo — the keynote stage is a single, self-contained surface and the rest of
 * the marketing app is a single-page composition. The `/demo-stage` and
 * `/stage` paths both mount the cinematic Demo Stage; anything else mounts the
 * existing App.
 *
 * Kiosk loop:           open /demo-stage?kiosk=1
 * Builder (technical):  press B once on the stage, or append ?view=builder
 */
function pickRoot() {
  const path = window.location.pathname.replace(/\/+$/, '')
  if (path === '/demo-stage' || path === '/stage') {
    return <DemoStage />
  }
  // `/` mounts only the dark ConciergeStudio — single full-page surface.
  if (path === '' || path === '/' || path === '/app') {
    return <ConciergeApp />
  }
  // Marketing scroll (Hero, Journey, Trips, Memory, System, Vision) lives here.
  if (path === '/marketing') {
    return <App />
  }
  // Old chalk-talk surface still reachable for the workshop walkthrough.
  if (path === '/chalk-talk' || path === '/pro' || path === '/classic') {
    return <MeridianProChalkTalk />
  }
  if (
    path === '/showcase' ||
    path === '/device-showcase' ||
    path === '/slides/meridian-device-showcase'
  ) {
    return <MeridianDeviceShowcase />
  }
  return <ConciergeApp />
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>{pickRoot()}</React.StrictMode>,
)
