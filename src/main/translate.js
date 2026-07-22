// Document translation: detect the language of an uploaded document, translate
// every text block to the destination's primary language, and rebuild a
// look-alike document — the same layout (each translated phrase placed where
// the original sat), so it reads as the original with the words changed.
//
// OCR is done with Apple Vision returning per-line bounding boxes and a
// multi-language recognizer (Latin, Chinese, Japanese, Korean, Arabic,
// Cyrillic). Translation runs through the LLM chain (Kimi K3 → Claude →
// local). The look-alike page is HTML with absolutely-positioned blocks that
// the existing renderPdf turns into a PDF.

// Vision OCR that returns line text + normalized bounding boxes + the
// dominant language, as JSON. Vision's origin is bottom-left; we flip Y to
// top-left for HTML/PDF layout.
export const OCR_LAYOUT_JXA = `
ObjC.import('Foundation'); ObjC.import('Vision');
function run(argv) {
  const url = $.NSURL.fileURLWithPath(argv[0]);
  const handler = $.VNImageRequestHandler.alloc.initWithURLOptions(url, $());
  const req = $.VNRecognizeTextRequest.alloc.init;
  req.recognitionLevel = 1; // accurate
  req.usesLanguageCorrection = true;
  try { req.automaticallyDetectsLanguage = true; } catch (e) {}
  try { req.recognitionLanguages = $(['en-US','zh-Hans','zh-Hant','ja-JP','ko-KR','ar-SA','ru-RU','fr-FR','es-ES','de-DE','pt-BR','it-IT','th-TH']); } catch (e) {}
  const err = Ref();
  handler.performRequestsError($.NSArray.arrayWithObject(req), err);
  const lines = [];
  const results = req.results;
  if (results) for (let i = 0; i < results.count; i++) {
    const obs = results.objectAtIndex(i);
    const cands = obs.topCandidates(1);
    if (cands.count === 0) continue;
    const str = ObjC.unwrap(cands.objectAtIndex(0).string);
    const bb = obs.boundingBox; // normalized, origin bottom-left
    lines.push({
      text: str,
      x: bb.origin.x,
      y: 1 - (bb.origin.y + bb.size.height),
      w: bb.size.width,
      h: bb.size.height
    });
  }
  // Dominant language of the concatenated text.
  const full = lines.map(function(l){return l.text}).join(' ');
  let lang = 'und';
  try {
    const ns = $.NSString.stringWithString(full);
    const tagger = $.NSLinguisticTagger.alloc.initWithTagSchemesOptions($(['Language']), 0);
    tagger.string = ns;
    const t = tagger.tagAtIndexSchemeTokenRangeSentenceRange(0, 'Language', $(), $());
    if (t && !t.isNil()) lang = ObjC.unwrap(t);
  } catch (e) {}
  return JSON.stringify({ lang: lang, lines: lines });
}`

// ISO language code -> English name, for prompts and UI.
export const LANG_NAMES = {
  en: 'English', zh: 'Chinese', 'zh-Hans': 'Simplified Chinese', 'zh-Hant': 'Traditional Chinese',
  ja: 'Japanese', ko: 'Korean', ar: 'Arabic', ru: 'Russian', fr: 'French', es: 'Spanish',
  de: 'German', pt: 'Portuguese', it: 'Italian', th: 'Thai', tr: 'Turkish', vi: 'Vietnamese',
  id: 'Indonesian', hi: 'Hindi', fa: 'Persian', ur: 'Urdu', nl: 'Dutch', pl: 'Polish',
  uk: 'Ukrainian', el: 'Greek', he: 'Hebrew', bn: 'Bengali', ta: 'Tamil', und: 'unknown'
}

// Primary language each destination files visa documents in (for the target).
export const DEST_LANG = {
  China: 'zh', Japan: 'ja', 'South Korea': 'ko', Thailand: 'th', Taiwan: 'zh',
  France: 'fr', Germany: 'de', Italy: 'it', Spain: 'es', Portugal: 'pt',
  Brazil: 'pt', Mexico: 'es', Argentina: 'es', Russia: 'ru', Turkey: 'tr',
  'Saudi Arabia': 'ar', 'United Arab Emirates': 'ar', Egypt: 'ar', Morocco: 'ar',
  Vietnam: 'vi', Indonesia: 'id', Netherlands: 'nl', Greece: 'el', Israel: 'he',
  Ukraine: 'uk', Poland: 'pl'
  // Everything else defaults to English (most consulates accept English).
}

export function normLang(code) {
  const c = String(code || '').toLowerCase()
  if (c.startsWith('zh')) return c.includes('hant') ? 'zh-Hant' : 'zh'
  return c.split('-')[0] || 'und'
}

// Script-based language detection from the OCR text. Passports and IDs mix
// Latin MRZ/labels with the native script, which confuses statistical
// language detectors — counting characters by Unicode block is far more
// reliable for deciding whether a document needs translation.
export function detectScriptLang(text, taggerHint) {
  const t = String(text || '')
  const counts = {
    zh: (t.match(/[一-鿿]/g) || []).length,
    ja: (t.match(/[぀-ヿ]/g) || []).length, // kana (Han shared with zh)
    ko: (t.match(/[가-힯]/g) || []).length,
    ar: (t.match(/[؀-ۿ]/g) || []).length,
    ru: (t.match(/[Ѐ-ӿ]/g) || []).length,
    th: (t.match(/[฀-๿]/g) || []).length,
    el: (t.match(/[Ͱ-Ͽ]/g) || []).length,
    he: (t.match(/[֐-׿]/g) || []).length,
    hi: (t.match(/[ऀ-ॿ]/g) || []).length
  }
  // Japanese needs kana to distinguish from Chinese Han.
  if (counts.ja >= 2) return 'ja'
  const han = counts.zh
  const best = Object.entries(counts).filter(([k]) => k !== 'ja').sort((a, b) => b[1] - a[1])[0]
  if (best && best[1] >= 2) return best[0] === 'zh' ? 'zh' : best[0]
  if (han >= 2) return 'zh'
  // No strong non-Latin script — trust the OS tagger if it's confident.
  const hint = normLang(taggerHint)
  if (hint && hint !== 'und' && LANG_NAMES[hint]) return hint
  return 'en'
}

// Which language should this document be translated INTO for this trip?
// Consulates in the destination country generally require their own language
// or English; we target the destination's primary language, English default.
export function targetLangFor(destination) {
  const d = destination === 'USA' ? 'United States' : destination
  return DEST_LANG[d] || 'en'
}

export function langName(code) {
  const n = normLang(code)
  return LANG_NAMES[n] || LANG_NAMES[code] || code || 'unknown'
}

// Does this document need translation for this trip? Only when the detected
// source language differs from the target (and both are known).
export function needsTranslation(sourceLang, targetLang) {
  const s = normLang(sourceLang)
  const t = normLang(targetLang)
  if (s === 'und' || !s) return false
  if (s === t) return false
  // Latin-English documents going to English destinations don't need it.
  if (t === 'en' && s === 'en') return false
  return true
}

// Build the translation prompt: the LLM returns one translated string per
// input line, index-aligned, as a JSON array.
export function translationPrompt(lines, targetName) {
  const numbered = lines.map((l, i) => `${i}\t${l.text}`).join('\n')
  return `Translate each of the following document lines into ${targetName}. These are lines from an official identity/travel document (passport, ID, certificate). Preserve names, numbers, dates, and codes EXACTLY as-is (transliterate personal names only if the target script requires it, otherwise keep them in Latin script). Return ONLY a JSON array of strings, one per input line, in the same order and same count. Do not merge or split lines.\n\nLines (index<TAB>text):\n${numbered}\n\nJSON array of ${lines.length} translated strings:`
}

// Reconstruct a look-alike page: a white page the aspect ratio of the
// original, with each translated line absolutely positioned at the original
// line's box and font-sized to its height. Reads as the original document
// with the words changed.
export function lookAlikeHtml(doc, translated, meta) {
  const esc = (s) => String(s ?? '').replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]))
  const W = 800
  const H = Math.round(W * (meta.aspect || 1.4))
  const blocks = (doc.lines || []).map((l, i) => {
    const text = translated[i] != null ? translated[i] : l.text
    const top = Math.round(l.y * H)
    const left = Math.round(l.x * W)
    // Give each line room to the right page margin so translated text (usually
    // wider than the original) doesn't run off the page; wrap if still too long.
    const boxW = Math.max(120, Math.min(W - left - 12, Math.round(Math.max(l.w, 0.5) * W)))
    const fontPx = Math.max(9, Math.min(30, Math.round(l.h * H * 0.7)))
    return `<div style="position:absolute;top:${top}px;left:${left}px;width:${boxW}px;font-size:${fontPx}px;line-height:1.25;white-space:normal;overflow:visible;color:#111">${esc(text)}</div>`
  }).join('')
  const date = new Date().toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric' })
  return `<!doctype html><html><head><meta charset="utf-8"><style>
    * { box-sizing: border-box; }
    body { margin: 0; font-family: -apple-system, "Helvetica Neue", Arial, "PingFang SC", "Hiragino Sans", sans-serif; }
    .wrap { padding: 28px 34px; }
    .banner { display: flex; justify-content: space-between; align-items: baseline; border-bottom: 2px solid #0f294d; padding-bottom: 8px; margin-bottom: 14px; }
    .banner .t { font-size: 15px; font-weight: 800; color: #0f294d; }
    .banner .s { font-size: 11px; color: #5a6b85; }
    .page { position: relative; width: ${W}px; height: ${H}px; margin: 0 auto; border: 1px solid #d7e3f4; border-radius: 8px; background: #fff; }
    .foot { margin-top: 14px; font-size: 10px; color: #8592a6; line-height: 1.5; }
  </style></head><body><div class="wrap">
    <div class="banner"><div class="t">Trip.com · Certified Translation</div><div class="s">${esc(meta.sourceName)} → ${esc(meta.targetName)} · ${esc(date)}</div></div>
    <div class="page">${blocks}</div>
    <div class="foot">Machine translation prepared by Trip.com visa services (${esc(meta.engine || 'AI')}) from the uploaded ${esc(meta.docLabel || 'document')}. Layout mirrors the original; personal names, numbers, and dates are preserved. For consular filing, a certified human translation may additionally be required.</div>
  </div></body></html>`
}
