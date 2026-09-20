import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// https://vitejs.dev/config/
export default defineConfig(({ mode }) => {
  // The client calls the backend origin directly (see src/api/client.ts), so
  // this proxy only serves a relative VITE_API_BASE_URL. Point it at the same
  // backend the documentation starts on port 8013.
  const env = loadEnv(mode, '.', 'VITE_')
  const backend = env.VITE_API_ORIGIN?.trim() || 'http://127.0.0.1:8013'
  return {
    plugins: [react()],
    build: {
      chunkSizeWarningLimit: 700,
    },
    server: {
      port: 5173,
      proxy: {
        '/api': {
          target: backend,
          changeOrigin: true,
        },
      },
    },
  }
})
