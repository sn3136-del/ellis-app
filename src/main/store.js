import { app } from 'electron'
import { join } from 'path'
import { readFileSync, writeFileSync, existsSync, mkdirSync } from 'fs'

// Lightweight JSON persistence in the OS user-data directory.
// Keeps a single state file: settings + notifications + trips.
// No demo/case seeding of any kind — a fresh install starts empty.

const DEFAULT_STATE = {
  settings: {
    anthropicKey: '',
    anthropicModel: 'claude-fable-5',
    localAI: { enabled: false, endpoint: 'http://127.0.0.1:11434', model: 'llama3.1:8b' },
    kimi: { enabled: true, apiKey: '', endpoint: 'https://api.moonshot.ai/v1', model: 'kimi-k3' },
    // The org's own sender address for traveler emails (blank = use the local
    // Mail app). Trial users enter their internal email + app password here.
    smtp: { user: '', appPassword: '', host: 'smtp.gmail.com', port: 587 },
    // Where the Trip.com demo agent transmits filing packages (agency/visa-
    // centre intake address). Empty = package prepared, not transmitted.
    tripFiling: { endpoint: '' }
  },
  notifications: [],
  trips: []
}

let cachedPath = null

function statePath() {
  if (cachedPath) return cachedPath
  const dir = app.getPath('userData')
  if (!existsSync(dir)) mkdirSync(dir, { recursive: true })
  cachedPath = join(dir, 'ellis-state.json')
  return cachedPath
}

export function loadState() {
  try {
    const p = statePath()
    if (!existsSync(p)) {
      const fresh = structuredClone(DEFAULT_STATE)
      writeFileSync(p, JSON.stringify(fresh, null, 2))
      return fresh
    }
    const raw = JSON.parse(readFileSync(p, 'utf-8'))
    const loaded = {
      settings: {
        ...DEFAULT_STATE.settings,
        ...(raw.settings || {}),
        localAI: { ...DEFAULT_STATE.settings.localAI, ...(raw.settings?.localAI || {}) },
        kimi: { ...DEFAULT_STATE.settings.kimi, ...(raw.settings?.kimi || {}) },
        smtp: { ...DEFAULT_STATE.settings.smtp, ...(raw.settings?.smtp || {}) },
        tripFiling: { ...DEFAULT_STATE.settings.tripFiling, ...(raw.settings?.tripFiling || {}) }
      },
      notifications: Array.isArray(raw.notifications) ? raw.notifications : [],
      trips: Array.isArray(raw.trips) ? raw.trips : []
    }
    // Migration: earlier builds shipped a personal address as the SMTP user
    // default; clear it unless the org completed the config with a password.
    if (loaded.settings.smtp?.user === 'sn3136@columbia.edu' && !loaded.settings.smtp?.appPassword) {
      loaded.settings.smtp.user = ''
    }
    // Migration: drop legacy three-role product state (cases, onboarding,
    // integrations, org defaults) from older installs.
    for (const k of ['onboarded', 'seeded', 'seedVersion', 'organizationName', 'defaultDestination', 'defaultLanguage', 'reminderDays', 'autoReview', 'integrations']) {
      delete loaded.settings[k]
    }
    writeFileSync(p, JSON.stringify(loaded, null, 2))
    return loaded
  } catch (err) {
    return structuredClone(DEFAULT_STATE)
  }
}

export function saveState(state) {
  const p = statePath()
  writeFileSync(p, JSON.stringify(state, null, 2))
  return true
}

let state = null
export function getState() {
  if (!state) state = loadState()
  return state
}
export function setState(next) {
  state = next
  saveState(state)
  return state
}
export function update(mutator) {
  const s = getState()
  mutator(s)
  saveState(s)
  return s
}
