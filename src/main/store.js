import { app } from 'electron'
import { join } from 'path'
import { readFileSync, writeFileSync, existsSync, mkdirSync } from 'fs'
import { seedCases } from './seed.js'

// Lightweight JSON persistence in the OS user-data directory.
// Keeps a single state file: settings + all cases.

// Bump when the template/seed cases change so existing installs refresh them.
const SEED_VERSION = 4

const DEFAULT_STATE = {
  settings: {
    onboarded: false,
    seeded: false,
    seedVersion: SEED_VERSION,
    organizationName: 'Ellis Immigration',
    defaultDestination: 'USA',
    defaultLanguage: 'English',
    reminderDays: 60,
    autoReview: true,
    anthropicKey: '',
    anthropicModel: 'claude-fable-5',
    localAI: { enabled: false, endpoint: 'http://127.0.0.1:11434', model: 'llama3.1:8b' },
    kimi: { enabled: true, apiKey: '', endpoint: 'https://api.moonshot.ai/v1', model: 'kimi-k3' },
    // The org's own sender address for traveler emails (blank = use the local
    // Mail app). Trial users enter their internal email + app password here.
    smtp: { user: '', appPassword: '', host: 'smtp.gmail.com', port: 587 },
    // Where the Trip.com agent transmits filing packages (agency/visa-centre
    // intake address). Empty = demo inbox, clearly labeled in the filing.
    tripFiling: { endpoint: '' },
    integrations: { slack: false, email: false, hris: false, calendar: false, drive: false }
  },
  notifications: [],
  cases: [],
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
      fresh.cases = seedCases()
      fresh.settings.seeded = true
      writeFileSync(p, JSON.stringify(fresh, null, 2))
      return fresh
    }
    const raw = JSON.parse(readFileSync(p, 'utf-8'))
    const loaded = {
      settings: {
        ...DEFAULT_STATE.settings,
        ...(raw.settings || {}),
        integrations: { ...DEFAULT_STATE.settings.integrations, ...(raw.settings?.integrations || {}) },
        localAI: { ...DEFAULT_STATE.settings.localAI, ...(raw.settings?.localAI || {}) },
        kimi: { ...DEFAULT_STATE.settings.kimi, ...(raw.settings?.kimi || {}) },
        smtp: { ...DEFAULT_STATE.settings.smtp, ...(raw.settings?.smtp || {}) },
        tripFiling: { ...DEFAULT_STATE.settings.tripFiling, ...(raw.settings?.tripFiling || {}) }
      },
      notifications: Array.isArray(raw.notifications) ? raw.notifications : [],
      cases: Array.isArray(raw.cases) ? raw.cases : [],
      trips: Array.isArray(raw.trips) ? raw.trips : []
    }
    // Migration: earlier builds shipped a personal address as the SMTP user
    // default; clear it unless the org completed the config with a password.
    if (loaded.settings.smtp?.user === 'sn3136@columbia.edu' && !loaded.settings.smtp?.appPassword) {
      loaded.settings.smtp.user = ''
      writeFileSync(p, JSON.stringify(loaded, null, 2))
    }
    if (!loaded.settings.seeded && loaded.cases.length === 0) {
      loaded.cases = seedCases()
      loaded.settings.seeded = true
      loaded.settings.seedVersion = SEED_VERSION
      writeFileSync(p, JSON.stringify(loaded, null, 2))
    } else if ((loaded.settings.seedVersion || 0) < SEED_VERSION) {
      // Refresh template cases (id starts with "seed_") while keeping user-created ones.
      const custom = loaded.cases.filter((c) => !String(c.id).startsWith('seed_'))
      loaded.cases = [...seedCases(), ...custom]
      loaded.settings.seedVersion = SEED_VERSION
      loaded.settings.seeded = true
      writeFileSync(p, JSON.stringify(loaded, null, 2))
    }
    return loaded
  } catch (err) {
    const fresh = structuredClone(DEFAULT_STATE)
    fresh.cases = seedCases()
    fresh.settings.seeded = true
    return fresh
  }
}

export function saveState(state) {
  const p = statePath()
  writeFileSync(p, JSON.stringify(state, null, 2))
  return true
}

export function resetDemo() {
  const s = getState()
  s.cases = seedCases()
  s.settings.seeded = true
  saveState(s)
  return s.cases
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
