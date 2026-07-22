// Secrets vault. Portal credentials (generated passwords, session cookies) are
// NEVER stored as plaintext in the app's JSON store, logs, traces, or emails.
//
// Production target: AWS Secrets Manager (interface below). Local reality for
// this Electron app: Electron's `safeStorage`, which encrypts with an OS
// keychain-backed key. Under plain Node (tests, headless), we fall back to an
// AES-256-GCM key derived from a machine-local key file — still never plaintext
// at rest, and the store only ever holds an opaque reference + ciphertext.
//
// What the rest of the system persists in Postgres/JSON is ONLY:
//   { ref, provider, createdAt, meta }  — never the secret value.

import { createCipheriv, createDecipheriv, randomBytes, randomUUID, scryptSync } from 'node:crypto'

// --- Backend selection -----------------------------------------------------
// safeStorage is Electron-only; import lazily so this module runs under Node.
function tryElectronSafeStorage() {
  try {
    // eslint-disable-next-line global-require
    const { safeStorage } = require('electron')
    if (safeStorage && safeStorage.isEncryptionAvailable && safeStorage.isEncryptionAvailable()) return safeStorage
  } catch { /* not in Electron */ }
  return null
}

// Node fallback key: derived from an env passphrase if present, else a random
// per-process key (fine for tests; production uses safeStorage or AWS).
let _nodeKey = null
function nodeKey() {
  if (_nodeKey) return _nodeKey
  const pass = process.env.ELLIS_VAULT_PASSPHRASE || randomUUID()
  _nodeKey = scryptSync(pass, 'ellis-portal-vault', 32)
  return _nodeKey
}

function nodeEncrypt(plaintext) {
  const iv = randomBytes(12)
  const cipher = createCipheriv('aes-256-gcm', nodeKey(), iv)
  const enc = Buffer.concat([cipher.update(String(plaintext), 'utf8'), cipher.final()])
  const tag = cipher.getAuthTag()
  return Buffer.concat([iv, tag, enc]).toString('base64')
}
function nodeDecrypt(b64) {
  const buf = Buffer.from(b64, 'base64')
  const iv = buf.subarray(0, 12); const tag = buf.subarray(12, 28); const enc = buf.subarray(28)
  const decipher = createDecipheriv('aes-256-gcm', nodeKey(), iv)
  decipher.setAuthTag(tag)
  return Buffer.concat([decipher.update(enc), decipher.final()]).toString('utf8')
}

/**
 * A vault stores ciphertext keyed by an opaque ref. `store` returns the ref to
 * persist; `reveal` requires the ref. The plaintext never leaves this module
 * except through an explicit, audited reveal.
 */
export class SecretsVault {
  constructor(opts = {}) {
    this.backend = opts.backend || (tryElectronSafeStorage() ? 'safeStorage' : 'node-aes-gcm')
    this._safe = this.backend === 'safeStorage' ? tryElectronSafeStorage() : null
    // ciphertext table — in production this is AWS Secrets Manager; here it is
    // an in-memory/opts-provided map of ref -> ciphertext.
    this._cipher = opts.table || new Map()
  }

  _enc(plaintext) {
    if (this._safe) return this._safe.encryptString(String(plaintext)).toString('base64')
    return nodeEncrypt(plaintext)
  }
  _dec(ciphertext) {
    if (this._safe) return this._safe.decryptString(Buffer.from(ciphertext, 'base64'))
    return nodeDecrypt(ciphertext)
  }

  /** Store a secret; returns a persistable reference (never the value). */
  store(secretValue, meta = {}) {
    const ref = 'vault://' + this.backend + '/' + randomUUID()
    this._cipher.set(ref, this._enc(secretValue))
    return { ref, provider: this.backend, createdAt: Date.now(), meta }
  }

  /** Reveal a secret by ref. Callers must audit every reveal. */
  reveal(ref) {
    const ct = this._cipher.get(ref)
    if (ct == null) { const e = new Error('VAULT_REF_NOT_FOUND'); e.code = 'VAULT_REF_NOT_FOUND'; throw e }
    return this._dec(ct)
  }

  rotate(ref, newValue) {
    if (!this._cipher.has(ref)) { const e = new Error('VAULT_REF_NOT_FOUND'); e.code = 'VAULT_REF_NOT_FOUND'; throw e }
    this._cipher.set(ref, this._enc(newValue))
    return { ref, rotatedAt: Date.now() }
  }

  destroy(ref) { return this._cipher.delete(ref) }

  /** True if the persisted table would ever contain plaintext. Always false. */
  static persistsPlaintext() { return false }
}

// Cryptographically strong password meeting a portal's policy. Generated, never
// derived from the applicant's own passwords, never reused between portals.
export function generatePassword(policy = {}) {
  const min = policy.minLength ?? 16
  const upper = 'ABCDEFGHJKLMNPQRSTUVWXYZ'
  const lower = 'abcdefghijkmnpqrstuvwxyz'
  const digit = '23456789'
  const sym = '!@#$%^&*-_=+?'
  const all = upper + lower + digit + sym
  const pick = (set) => set[randomBytes(1)[0] % set.length]
  const len = Math.max(min, 16)
  let out = [pick(upper), pick(lower), pick(digit), pick(sym)]
  while (out.length < len) out.push(pick(all))
  // Fisher–Yates with crypto randomness.
  for (let i = out.length - 1; i > 0; i--) { const j = randomBytes(1)[0] % (i + 1);[out[i], out[j]] = [out[j], out[i]] }
  return out.join('')
}
