// Embedded Python backend lifecycle. The packaged app is self-contained: it
// bundles the FastAPI backend + a Python runtime + the reference data, spawns
// uvicorn on launch in local_mock_demo mode, waits for it to become healthy,
// and shuts it down on quit. If a backend is already serving on the port
// (e.g. a developer's own `uvicorn`), it is reused instead of spawning a second.
import { app } from 'electron'
import { spawn } from 'child_process'
import { join } from 'path'
import { existsSync } from 'fs'
import http from 'http'

const PORT = 8000
const HOST = '127.0.0.1'
export const BACKEND_URL = `http://${HOST}:${PORT}`

let child = null
let startedByUs = false

// Resolve the bundled (packaged) or repo (dev) locations of the runtime, the
// backend source, and the reference data.
function paths() {
  if (app.isPackaged) {
    const base = process.resourcesPath
    return {
      python: join(base, 'backend-venv', 'bin', 'python3.14'),
      pythonAlt: join(base, 'backend-venv', 'bin', 'python'),
      cwd: join(base, 'backend'),
      data: join(base, 'data')
    }
  }
  const base = app.getAppPath() // project root in dev
  return {
    python: join(base, 'backend', '.venv', 'bin', 'python3.14'),
    pythonAlt: join(base, 'backend', '.venv', 'bin', 'python'),
    cwd: join(base, 'backend'),
    data: join(base, 'data')
  }
}

function ping(timeoutMs = 1500) {
  return new Promise((resolve) => {
    const req = http.get(`${BACKEND_URL}/healthz`, { timeout: timeoutMs }, (res) => {
      res.resume()
      resolve(res.statusCode === 200)
    })
    req.on('error', () => resolve(false))
    req.on('timeout', () => { req.destroy(); resolve(false) })
  })
}

async function waitHealthy(totalMs = 30000, everyMs = 500) {
  const deadline = Date.now() + totalMs
  while (Date.now() < deadline) {
    if (await ping()) return true
    await new Promise((r) => setTimeout(r, everyMs))
  }
  return false
}

// Start (or adopt) the backend. Returns { ok, reused, spawned }.
export async function startBackend() {
  // Only the packaged app auto-spawns; in dev the developer runs their own
  // backend (override with ELLIS_SPAWN_BACKEND=1 to test the bundle path).
  const shouldSpawn = app.isPackaged || process.env.ELLIS_SPAWN_BACKEND === '1'

  if (await ping()) {
    // A backend is already listening — reuse it, never double-spawn.
    return { ok: true, reused: true, spawned: false }
  }
  if (!shouldSpawn) return { ok: false, reused: false, spawned: false }

  const p = paths()
  const python = existsSync(p.python) ? p.python : p.pythonAlt
  if (!existsSync(python) || !existsSync(p.cwd)) {
    console.error('[backend] bundled runtime not found:', python, p.cwd)
    return { ok: false, reused: false, spawned: false }
  }

  const dbPath = join(app.getPath('userData'), 'ellis.db')
  const env = {
    ...process.env,
    ELLIS_RUNTIME_MODE: 'local_mock_demo',
    DATABASE_URL: `sqlite:///${dbPath}`,
    ELLIS_DATA_DIR: p.data,
    PYTHONUNBUFFERED: '1',
    PYTHONDONTWRITEBYTECODE: '1'
  }
  child = spawn(python, ['-m', 'uvicorn', 'app.main:app', '--host', HOST,
                         '--port', String(PORT), '--log-level', 'warning'],
                { cwd: p.cwd, env, stdio: ['ignore', 'pipe', 'pipe'] })
  startedByUs = true
  child.stdout.on('data', (d) => process.stdout.write(`[backend] ${d}`))
  child.stderr.on('data', (d) => process.stderr.write(`[backend] ${d}`))
  child.on('exit', (code) => { console.log('[backend] exited', code); child = null })

  const ok = await waitHealthy()
  return { ok, reused: false, spawned: true }
}

export function stopBackend() {
  if (child && startedByUs) {
    try { child.kill('SIGTERM') } catch (_) { /* already gone */ }
    // Escalate if it does not exit promptly.
    const ref = child
    setTimeout(() => { try { ref.kill('SIGKILL') } catch (_) {} }, 3000)
    child = null
  }
}
