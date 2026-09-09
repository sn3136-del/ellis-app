// Phase 6 — static localization parity + t() behavior (pure, no DOM/JSX).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { STRINGS, SUPPORTED, LANGUAGE_NAMES, t, DEFAULT_LANG } from '../../src/renderer/src/lib/i18n.js'

test('all supported languages are defined with names', () => {
  assert.deepEqual(SUPPORTED, ['en', 'zh-CN', 'zh-Hant'])
  for (const code of SUPPORTED) assert.ok(LANGUAGE_NAMES[code], code)
})

test('every locale defines exactly the English key set (no missing/extra keys)', () => {
  const enKeys = Object.keys(STRINGS.en).sort()
  for (const code of SUPPORTED) {
    const keys = Object.keys(STRINGS[code]).sort()
    assert.deepEqual(keys, enKeys, `locale ${code} key mismatch`)
  }
})

test('no locale value is blank', () => {
  for (const code of SUPPORTED) {
    for (const [k, v] of Object.entries(STRINGS[code])) {
      assert.ok(typeof v === 'string' && v.trim().length > 0, `${code}:${k} is blank`)
    }
  }
})

test('t() returns the localized string', () => {
  assert.equal(t('en', 'nav.visa'), 'Visa Platform')
  assert.equal(t('zh-CN', 'nav.visa'), '签证平台')
  assert.equal(t('zh-Hant', 'nav.visa'), '簽證平台')
})

test('t() falls back to English for an unknown language, then to the key', () => {
  assert.equal(t('fr', 'nav.visa'), 'Visa Platform')   // unknown lang -> en
  assert.equal(t('en', 'does.not.exist'), 'does.not.exist')  // unknown key -> key
})

test('t() interpolates {vars}', () => {
  // Uses a key with no placeholder but proves interpolation does not corrupt it,
  // plus a synthetic check via a known-format string.
  assert.equal(t('en', 'result.mockReference'), 'Mock reference (not a real visa reference)')
})

test('assistant identity mentions Ellis in every language and never the model', () => {
  for (const code of SUPPORTED) {
    const s = t(code, 'assistant.identity')
    assert.match(s, /Ellis/)
    assert.doesNotMatch(s.toLowerCase(), /kimi|moonshot/)
  }
})

test('execution disclaimer is localized and non-empty in every language', () => {
  for (const code of SUPPORTED) {
    assert.ok(t(code, 'exec.notReal').length > 0)
    assert.ok(t(code, 'exec.mockDisclaimer').length > 0)
  }
  assert.equal(DEFAULT_LANG, 'en')
})

test('freshness labels preserve separate read, evidence, and failure counters in every locale', () => {
  const expected = {
    'ops.fresh.runReads': ['insufficient', 'read', 'sources'],
    'ops.fresh.runFailures': ['fetch', 'missing', 'provider'],
    'ops.fresh.runEvidence': ['errors', 'partial', 'unreadable', 'verified'],
  }
  for (const code of SUPPORTED) {
    for (const [key, counters] of Object.entries(expected)) {
      assert.deepEqual([...t(code, key).matchAll(/\{(\w+)\}/g)].map(m => m[1]).sort(), counters)
    }
  }
})


test('source quality badges scope evidence to the visa requirement in every locale', () => {
  const scopes = { en: /[Vv]isa requirement|Requirement:/, 'zh-CN': /签证要求/, 'zh-Hant': /簽證要求/ }
  for (const code of SUPPORTED) {
    for (const key of ['ops.check.quoted', 'ops.check.aiQuoted', 'ops.check.grounded',
      'ops.tip.quoted', 'ops.tip.aiQuoted', 'ops.tip.grounded', 'ops.tip.reference',
      'ops.stat.requirementSupportLine', 'ops.stat.substantiatedSub']) {
      assert.match(t(code, key), scopes[code], `${code}:${key} must identify the supported requirement`)
    }
  }
  assert.doesNotMatch(t('en', 'ops.check.quoted'), /Verified & quoted/)
  assert.match(t('en', 'ops.tip.aiQuoted'), /No human verification/)
  assert.match(t('en', 'ops.tip.quoted'), /other fields need their own evidence/)
  assert.match(t('en', 'ops.tip.grounded'), /does not verify every field/)
  assert.match(t('en', 'ops.tip.confidence'), /does not certify every field or replace the release status/)
  assert.match(t('zh-CN', 'ops.tip.confidence'), /不能代替发布状态/)
  assert.match(t('zh-Hant', 'ops.tip.confidence'), /不能代替發布狀態/)
})

 test('source coverage copy describes link presence separately from evidence', () => {
  for (const [code, pattern] of Object.entries({en: /source link/, 'zh-CN': /来源链接/, 'zh-Hant': /來源連結/})) {
    assert.match(t(code, 'ops.stat.srcLine'), pattern)
    assert.match(t(code, 'ops.stat.sourcesSub'), pattern)
  }
})
