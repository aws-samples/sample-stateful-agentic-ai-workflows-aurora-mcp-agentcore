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
  <React.StrictMode>{pickRoot()}</React.StrictMode>,
)
