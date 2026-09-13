import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { createRequire } from 'node:module'
import { resolve } from 'node:path'
import { build } from 'esbuild'
import { createElement, StrictMode } from 'react'
import { act, create } from 'react-test-renderer'

// Actual QC translation hooks, with unrelated UI removed by tree shaking.
const compiled=await build({stdin:{contents:`import { useTypeNames, useValueTranslations } from './src/renderer/src/screens/QualityConsole.jsx'; export {useTypeNames,useValueTranslations}`,
  resolveDir:process.cwd(),loader:'js'},bundle:true,write:false,platform:'node',format:'cjs',jsx:'automatic',
  external:['react','react/jsx-runtime'],logLevel:'silent',plugins:[{name:'expose-real-hooks',setup(builder){
    builder.onLoad({filter:/QualityConsole\.jsx$/},async args=>({contents:(await readFile(args.path,'utf8'))+'\nexport {useTypeNames,useValueTranslations}',loader:'jsx'}))
  }}]})
const mod={exports:{}}
new Function('require','module','exports',compiled.outputFiles[0].text)(createRequire(import.meta.url),mod,mod.exports)
const {useTypeNames,useValueTranslations}=mod.exports
const sleep=ms=>new Promise(r=>setTimeout(r,ms))
function deferred(){let resolve;const promise=new Promise(r=>{resolve=r});return{resolve,promise}}
function Value({client,lang,text}){const T=useValueTranslations(client,lang);return createElement('span',null,T(text))}
function Names({client,lang,data}){const names=useTypeNames(client,data,lang);return createElement('span',null,names[data.records[0].visa_type_name]||data.records[0].visa_type_name)}
function output(renderer){return renderer.toJSON()?.children?.join('')}
function service(){const calls=[];return{calls,client:{i18nCatalog(lang,entries){const d=deferred();calls.push({...d,lang,entries});return d.promise}}}}
function finish(call,prefix){call.resolve({status:'ok',entries:Object.fromEntries(Object.entries(call.entries).map(([k,v])=>[k,prefix+v]))})}

test('actual QC value hook under StrictMode dispatches once and cannot show late wrong-language text',async()=>{
 const {client,calls}=service();let renderer;const text='Unique official condition for locale race'
 await act(async()=>{renderer=create(createElement(StrictMode,null,createElement(Value,{client,lang:'zh-CN',text})));await sleep(60)})
 assert.equal(calls.length,1);assert.equal(calls[0].lang,'zh-CN')
 await act(async()=>{renderer.update(createElement(StrictMode,null,createElement(Value,{client,lang:'zh-Hant',text})));await sleep(60)})
 assert.equal(calls.length,2)
 await act(async()=>{finish(calls[0],'简');await Promise.resolve()})
 assert.equal(output(renderer),text)
 await act(async()=>{finish(calls[1],'繁');await Promise.resolve()})
 assert.equal(output(renderer),'繁'+text)
 await act(async()=>{renderer.update(createElement(StrictMode,null,createElement(Value,{client,lang:'zh-CN',text})));await Promise.resolve()})
 assert.equal(output(renderer),'简'+text);assert.equal(calls.length,2)
 act(()=>renderer.unmount())
})

test('actual QC type names switch with exact language and share completed value cache',async()=>{
 const {client,calls}=service();let renderer;const text='Distinct tourist product translation'
 const data={records:[{visa_type_name:text}]}
 await act(async()=>{renderer=create(createElement(Names,{client,lang:'zh-CN',data}));await Promise.resolve()})
 await act(async()=>{renderer.update(createElement(Names,{client,lang:'zh-Hant',data}));await Promise.resolve()})
 await act(async()=>{finish(calls[0],'简');await Promise.resolve()})
 assert.equal(output(renderer),text)
 await act(async()=>{finish(calls[1],'繁');await Promise.resolve()})
 assert.equal(output(renderer),'繁'+text)
 await act(async()=>{renderer.update(createElement(Value,{client,lang:'zh-Hant',text}));await Promise.resolve()})
 assert.equal(output(renderer),'繁'+text);assert.equal(calls.length,2)
 act(()=>renderer.unmount())
})
