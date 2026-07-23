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

const webCsp = [
  "default-src 'self'",
  "img-src 'self' data:",
  "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
  "font-src 'self' https://fonts.gstatic.com",
  // Vite dev needs its module preamble; loopback backend + HMR websocket only.
  "script-src 'self' 'unsafe-inline'",
  `connect-src 'self' ws://${HOST}:${PORT} http://${HOST}:8000 http://localhost:8000`,
  // Browserbase Live View is a real secure feature: the short-lived URL comes
  // from the trusted local backend and is embedded in a sandboxed iframe.
  "frame-src 'self' https:"
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
  server: { host: HOST, port: PORT, strictPort: true },
  plugins: [react(), webCspPlugin()],
  resolve: { alias: { '@': resolve(__dirname, 'src/renderer/src') } }
})
