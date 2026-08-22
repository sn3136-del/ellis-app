// Throwaway VERIFY config: renderer on 5210, proxying /api to the verify
// backend on 8010. Loaded via http://app.localhost:5210 so the renderer's own
// resolveBase() picks origin+/api (its proxy-deployment shape) — no source
// changes, and the user's running instance (5199/8000) is never touched.
import base from './vite.web.config.mjs'

const PORT = 5210

const cfg = { ...base }
cfg.server = {
  ...base.server, host: '127.0.0.1', port: PORT, strictPort: true,
  proxy: { '/api': { target: 'http://127.0.0.1:8010',
                     rewrite: (p) => p.replace(/^\/api/, '') } },
}
cfg.plugins = (base.plugins || []).map((p) =>
  p && p.name === 'ellis-web-csp'
    ? { name: 'ellis-web-csp',
        transformIndexHtml(html) {
          const csp = [
            "default-src 'self'",
            "img-src 'self' data: blob:",
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
            "font-src 'self' https://fonts.gstatic.com",
            "script-src 'self' 'unsafe-inline'",
            `connect-src 'self' ws://app.localhost:${PORT}`,
            "frame-src 'self' blob: https:",
          ].join('; ')
          return html.replace(/<meta http-equiv="Content-Security-Policy"[^>]*>/i,
            `<meta http-equiv="Content-Security-Policy" content="${csp}" />`)
        } }
    : p)
export default cfg
