// Standalone Vite config to run the Ellis renderer as a LOCAL WEB APP — no
// Electron. Used by `npm run ellis:web` (scripts/start-ellis-web.sh). Binds to
// 127.0.0.1 only. The renderer talks to the local FastAPI backend at
// 127.0.0.1:8000 over HTTP; backend credentials never reach the browser.
//
// The renderer's index.html ships a strict Electron CSP; here we rewrite it for
// the web dev server so Vite HMR + the loopback backend are permitted, without
// modifying the source file or the Electron build.
import { resolve } from 'path'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const HOST = '127.0.0.1'
const PORT = 5199
// ELLIS_PUBLIC=1: the server is being exposed through a tunnel at a public
// URL (Trip.com's acceptance standard requires an online, no-install test
// link). The browser then reaches the API same-origin under /api (see
// visaBackend.resolveBase), which the dev server proxies to the local
// backend — backend credentials still never reach the browser.
const PUBLIC = process.env.ELLIS_PUBLIC === '1'

const webCsp = [
  "default-src 'self'",
  // blob: carries the in-app document preview — bytes are fetched from the
  // authenticated backend (connect-src) and rendered from a local blob URL,
  // never by framing/hotlinking a cross-origin page.
  "img-src 'self' data: blob:",
  "style-src 'self' 'unsafe-inline'",
  "font-src 'self'",
  // Vite dev needs its module preamble; loopback backend + HMR websocket only.
  "script-src 'self' 'unsafe-inline'",
  `connect-src 'self' ws://${HOST}:${PORT} http://${HOST}:8000 http://localhost:8000` +
    (PUBLIC ? ' https: wss:' : ''),
  // Browserbase Live View is a real secure feature: the short-lived URL comes
  // from the trusted local backend and is embedded in a sandboxed iframe.
  // blob: is the local PDF preview (Chrome's built-in viewer).
  "frame-src 'self' blob: https:"
].join('; ')

function webCspPlugin() {
  return {
    name: 'ellis-web-csp',
    transformIndexHtml(html) {
      return html.replace(
        /<meta http-equiv="Content-Security-Policy"[^>]*>/i,
        `<meta http-equiv="Content-Security-Policy" content="${webCsp}" />`
      )
    }
  }
}

export default defineConfig({
  root: 'src/renderer',
  base: '/',
  server: {
    host: HOST, port: PORT, strictPort: true,
    // Public mode: accept the tunnel's Host header and serve the API
    // same-origin so one URL carries the whole product.
    ...(PUBLIC ? { allowedHosts: true } : {}),
    proxy: {
      '/api': {
        target: `http://${HOST}:8000`,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
  plugins: [react(), webCspPlugin()],
  resolve: { alias: { '@': resolve(__dirname, 'src/renderer/src') } }
})
