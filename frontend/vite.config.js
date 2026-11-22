import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The FastAPI backend always runs on the lecturer's machine (port 8000).
// Use 127.0.0.1 (not "localhost") so Node doesn't resolve to IPv6 ::1.
const BACKEND = process.env.BACKEND_URL || 'http://127.0.0.1:8000'

const apiProxy = {
  '/api': {
    target: BACKEND,
    changeOrigin: false,
    // Adds X-Forwarded-For with the student's real LAN IP so the backend
    // subnet guard + device lock see the phone's address, not the proxy's.
    xfwd: true,
  },
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0', // reachable over the LAN (http://192.168.x.x:5173)
    port: 5173,
    strictPort: true,
    proxy: apiProxy,
  },
  preview: {
    host: '0.0.0.0',
    port: 4173,
    strictPort: true,
    proxy: apiProxy,
  },
})
