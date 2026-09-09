import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { build, loadConfigFromFile } from 'vite'

test('production web configuration executes JSX without a global React variable', async () => {
  const fixture = mkdtempSync(join(tmpdir(), 'ellis-web-build-'))
  try {
    const entry = join(fixture, 'badge.jsx')
    writeFileSync(entry, 'export default function Badge(){return <div>Quality Control</div>}')
    const loaded = await loadConfigFromFile({ command: 'build', mode: 'production' }, resolve('vite.web.config.mjs'))
    const result = await build({ ...loaded.config, configFile: false, logLevel: 'silent',
      resolve: { alias: { 'react/jsx-runtime': resolve('node_modules/react/jsx-runtime.js') } },
      define: { 'process.env.NODE_ENV': JSON.stringify('production') },
      build: { write: false, minify: false, lib: { entry, formats: ['es'] } } })
    const output = (Array.isArray(result) ? result : [result]).flatMap(bundle => bundle.output)
    const chunk = output.find(item => item.type === 'chunk' && item.isEntry)
    const compiled = await import('data:text/javascript;base64,' + Buffer.from(chunk.code).toString('base64'))
    const element = compiled.default()
    assert.equal(element.type, 'div')
    assert.equal(element.props.children, 'Quality Control')
  } finally { rmSync(fixture, { recursive: true, force: true }) }
})
