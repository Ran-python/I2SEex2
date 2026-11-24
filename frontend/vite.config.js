import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The FastAPI backend always runs on the lecturer's machine (port 8000).
// Use 127.0.0.1 (not "localhost") so Node doesn't resolve to IPv6 ::1.
const BACKEND = process.env.BACKEND_URL || 'http://127.0.0.1:8000'

const apiProxy = {
  '/api': {
    target: BACKEND,
    changeOrigin: false,
    // Allow up to 10 minutes for large MP4 video uploads & Gemini File API processing
    timeout: 600000,
    proxyTimeout: 600000,
    // Do NOT set xfwd: true because Vite's http-proxy OVERWRITES Cloudflare's
    // X-Forwarded-For header with 127.0.0.1! Instead, explicitly preserve
    // CF-Connecting-IP and append/forward the original client IP cleanly.
    xfwd: false,
    configure: (proxy) => {
      proxy.on('proxyReq', (proxyReq, req) => {
        const cfIp = req.headers['cf-connecting-ip']
        const origXff = req.headers['x-forwarded-for']
        const remoteAddr = req.socket?.remoteAddress || '127.0.0.1'
        if (cfIp) {
          proxyReq.setHeader('cf-connecting-ip', cfIp)
        }
        if (origXff) {
          proxyReq.setHeader('x-forwarded-for', `${origXff}, ${remoteAddr}`)
        } else if (cfIp) {
          proxyReq.setHeader('x-forwarded-for', `${cfIp}, ${remoteAddr}`)
        } else {
          proxyReq.setHeader('x-forwarded-for', remoteAddr)
        }
        if (req.headers['x-forwarded-proto']) {
          proxyReq.setHeader('x-forwarded-proto', req.headers['x-forwarded-proto'])
        }
        if (req.headers['x-forwarded-host']) {
          proxyReq.setHeader('x-forwarded-host', req.headers['x-forwarded-host'])
        }
      })
    },
  },
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    strictPort: true,
    allowedHosts: true, // allow Cloudflare Tunnel (*.trycloudflare.com), ngrok, Render, etc.
    proxy: apiProxy,
  },
  preview: {
    host: '0.0.0.0',
    port: 4173,
    strictPort: true,
    allowedHosts: true,
    proxy: apiProxy,
  },
})
