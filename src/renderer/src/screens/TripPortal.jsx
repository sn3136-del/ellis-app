import { useState, useEffect, useRef } from 'react'
import { ellis, COUNTRIES, fmtDate } from '../lib/api.js'
import { downloadArrivalPassPdfToDesktop, downloadTripReceiptPdfToDesktop, downloadTripApplicationPackPdfToDesktop, downloadTripOfficialFormPdfToDesktop, downloadAppointmentNoticePdfToDesktop } from '../lib/pdf.js'
import { useToast } from '../components/ui.jsx'
import { Icon } from '../components/icons.jsx'
import { PipelineIllustration } from '../components/visa/Illustrations.jsx'
import { tripcomLogo } from '../assets/logos.js'
import visaCompleteImg from '../assets/trip-visa-complete.png'

// Every country is a valid destination — the routing engine covers all
// ~199×199 nationality/destination pairs via the Passport Index matrix, with
// curated corridor rules (fees, portals, filing channels) for the majors.
const DESTS = COUNTRIES.filter((c) => c !== 'Other')

const FLAGS = {
  Afghanistan: '🇦🇫', Albania: '🇦🇱', Algeria: '🇩🇿', Argentina: '🇦🇷', Australia: '🇦🇺', Austria: '🇦🇹',
  Bangladesh: '🇧🇩', Belgium: '🇧🇪', Bolivia: '🇧🇴', Brazil: '🇧🇷', Bulgaria: '🇧🇬', Cambodia: '🇰🇭',
  Cameroon: '🇨🇲', Canada: '🇨🇦', Chile: '🇨🇱', China: '🇨🇳', Colombia: '🇨🇴', 'Costa Rica': '🇨🇷',
  Croatia: '🇭🇷', Cuba: '🇨🇺', Czechia: '🇨🇿', Denmark: '🇩🇰', 'Dominican Republic': '🇩🇴', Ecuador: '🇪🇨',
  Egypt: '🇪🇬', 'El Salvador': '🇸🇻', Ethiopia: '🇪🇹', Finland: '🇫🇮', France: '🇫🇷', Germany: '🇩🇪',
  Ghana: '🇬🇭', Greece: '🇬🇷', Guatemala: '🇬🇹', Haiti: '🇭🇹', Honduras: '🇭🇳', 'Hong Kong': '🇭🇰',
  Hungary: '🇭🇺', India: '🇮🇳', Indonesia: '🇮🇩', Iran: '🇮🇷', Iraq: '🇮🇶', Ireland: '🇮🇪', Israel: '🇮🇱',
  Italy: '🇮🇹', Jamaica: '🇯🇲', Japan: '🇯🇵', Jordan: '🇯🇴', Kazakhstan: '🇰🇿', Kenya: '🇰🇪',
  'South Korea': '🇰🇷', Kuwait: '🇰🇼', Lebanon: '🇱🇧', Malaysia: '🇲🇾', Mexico: '🇲🇽', Morocco: '🇲🇦',
  Nepal: '🇳🇵', Netherlands: '🇳🇱', 'New Zealand': '🇳🇿', Nigeria: '🇳🇬', Norway: '🇳🇴', Pakistan: '🇵🇰',
  Peru: '🇵🇪', Philippines: '🇵🇭', Poland: '🇵🇱', Portugal: '🇵🇹', Qatar: '🇶🇦', Romania: '🇷🇴',
  Russia: '🇷🇺', 'Saudi Arabia': '🇸🇦', Senegal: '🇸🇳', Singapore: '🇸🇬', 'South Africa': '🇿🇦',
  Spain: '🇪🇸', 'Sri Lanka': '🇱🇰', Sweden: '🇸🇪', Switzerland: '🇨🇭', Taiwan: '🇹🇼', Thailand: '🇹🇭',
  Tunisia: '🇹🇳', Turkey: '🇹🇷', Ukraine: '🇺🇦', 'United Arab Emirates': '🇦🇪', 'United Kingdom': '🇬🇧',
  'United States': '🇺🇸', USA: '🇺🇸', Uzbekistan: '🇺🇿', Venezuela: '🇻🇪', Vietnam: '🇻🇳', Zimbabwe: '🇿🇼'
}
const flag = (c) => FLAGS[c] || '🌍'

/* Destination arrival/entry registration systems — the traveler gets a
   case-specific email when theirs is processed (e.g. Visit Japan Web QR). */
const ARRIVAL_SYSTEMS = {
  Japan: { name: 'Visit Japan Web', doc: 'QR code', note: 'present it at immigration and customs on arrival' },
  Thailand: { name: 'Thailand Digital Arrival Card (TDAC)', doc: 'QR code', note: 'present it at passport control on arrival' },
  Singapore: { name: 'SG Arrival Card', doc: 'confirmation', note: 'have it ready at immigration' },
  'South Korea': { name: 'South Korea e-Arrival Card', doc: 'confirmation', note: 'have it ready at immigration' },
  Indonesia: { name: 'Indonesia electronic Customs Declaration (e-CD)', doc: 'QR code', note: 'present it at customs on arrival' },
  'New Zealand': { name: 'New Zealand Traveller Declaration (NZTD)', doc: 'traveller pass', note: 'have it ready at the border' },
  Canada: { name: 'ArriveCAN advance declaration', doc: 'receipt', note: 'have it ready at the arrival kiosk' }
}


// Loose name comparison mirroring the agent's verifier: order-insensitive
// token overlap, tolerant of one OCR-level character difference per token.
function looseNameMatch(a, b) {
  // Names are letters only: map common OCR digit-for-letter misreads back
  // (0→O, 1→I, 5→S, 8→B) BEFORE stripping, so 'N0EMI' normalizes to 'NOEMI'
  // instead of splitting into 'N' + 'EMI' and falsely mismatching 'NOEMI'.
  const NAME_DIGIT = { 0: 'O', 1: 'I', 5: 'S', 8: 'B' }
  const norm = (s) => String(s || '').toUpperCase()
    .replace(/[0158]/g, (d) => NAME_DIGIT[d])
    .replace(/[^A-Z ]/g, ' ').split(/\s+/).filter(Boolean)
  const dist = (x, y) => {
    const dp = Array.from({ length: x.length + 1 }, (_, i) => [i, ...Array(y.length).fill(0)])
    for (let j = 0; j <= y.length; j++) dp[0][j] = j
    for (let i = 1; i <= x.length; i++) for (let j = 1; j <= y.length; j++) dp[i][j] = Math.min(dp[i - 1][j] + 1, dp[i][j - 1] + 1, dp[i - 1][j - 1] + (x[i - 1] === y[j - 1] ? 0 : 1))
    return dp[x.length][y.length]
  }
  const ta = norm(a), tb = norm(b)
  if (!ta.length || !tb.length) return true
  const hit = (t) => tb.some((x) => x === t || (t.length >= 3 && dist(t, x) <= 1))
  const hits = ta.filter(hit).length
  return hits >= Math.min(ta.length, tb.length) - (ta.length > 2 ? 1 : 0) && hits >= 1
}

const isNoneVal = (v) => !v || /^none\b/i.test(String(v).trim()) || String(v).trim() === '—'

function portalDisplayName(plan, destination) {
  let raw = String(plan?.portal || '').trim()
  // Legacy bad values like "https://ceac.state.gov (DS-160)" encode as %20 in the UI.
  if (/^https?:\/\//i.test(raw)) {
    const host = (raw.match(/^https?:\/\/([^\s(/]+)/i) || [])[1]
    const form = (raw.match(/\(([^)]+)\)/) || [])[1]
    if (host) {
      const clean = host.replace(/^www\./, '')
      return form ? `${clean} (${form})` : clean
    }
  }
  if (raw) { try { return decodeURIComponent(raw.replace(/%20/g, ' ')).trim() } catch { return raw.trim() } }
  if (plan?.portalUrl) {
    try { return new URL(plan.portalUrl).hostname.replace(/^www\./, '') } catch { /* ignore */ }
  }
  if (ARRIVAL_SYSTEMS[destination]) return ARRIVAL_SYSTEMS[destination].name
  return `${destination} consulate`
}

function fmtTravelDate(d) {
  if (!d) return '—'
  const dt = new Date(`${d}T12:00:00`)
  if (Number.isNaN(dt.getTime())) return d
  return dt.toLocaleDateString([], { month: 'short', day: 'numeric', year: 'numeric' })
}


function arrivalPresentList(trip, plan, sys) {
  const items = ['• your passport']
  if (plan?.kind && plan.kind !== 'free') items.push('• your visa (attached)')
  else if (plan?.kind === 'free') items.push('• your entry confirmation (attached)')
  if (sys) items.push(`• your ${sys.name} ${sys.doc}`)
  items.push('• your Trip.com itinerary')
  return items.join('\n')
}

// "Track it yourself" block: the official portal, the application reference,
// and — when portal credentials were recorded during filing — the login the
// traveler can use to watch their own application alongside our monitoring.
function trackSection(trip, plan) {
  const url = trip.portalResearch?.applyUrl || null
  const pa = trip.portalAccess || null
  const ref = 'TRIP-' + (String(trip.id || '').split('_').pop() || '').toUpperCase()
  const lines = ['Track it yourself:', `• Application reference: ${ref}`]
  if (trip.govReference) lines.push(`• Official government reference: ${trip.govReference}`)
  if (url || plan?.portal) lines.push(`• Official portal: ${url || plan.portal}`)
  if (pa?.username) {
    lines.push(`• Portal login: ${pa.username}`)
    if (pa.password) lines.push(`• Portal password: ${pa.password}`)
    if (pa.note) lines.push(`• ${pa.note}`)
    lines.push('You can check the application yourself at any time — our AI agent keeps monitoring it 24/7 regardless.')
  } else {
    lines.push('Our AI agent monitors the application 24/7; if portal login credentials are created during filing, we will email them to you right away.')
  }
  return lines
}

function tripEmailSubmitted(firstName, channel, transmitted = true, trip = null, plan = null) {
  const selfFiled = !!trip?.govReference
  return [
    `Hi ${firstName},`,
    '',
    transmitted
      ? 'Good news — your application passed our full pre-submission review and has been submitted.'
      : 'Good news — your application passed our full pre-submission review and the complete package is ready.',
    '',
    selfFiled
      ? `It has been submitted on the official ${plan?.portal || 'government'} portal — your reference is ${trip.govReference}. We\'re now monitoring it for the decision.`
      : (transmitted ? `It was filed through ${channel}.` : `It will be filed through ${channel}.`),
    '',
    'Attached: your signed official visa application exactly as filed, and the supporting document package.',
    '',
    ...(trip ? [...trackSection(trip, plan), ''] : []),
    'If anything else is needed, we\'ll reach out right away.',
    '',
    'Warm regards,',
    'Trip.com'
  ].join('\n')
}

function tripEmailAppointment(firstName, when, where, transmitted = true, trip = null, plan = null) {
  return [
    `Hi ${firstName},`,
    '',
    transmitted
      ? 'Good news — your application passed our full pre-submission review and has been submitted to the embassy.'
      : 'Good news — your application passed our full pre-submission review and the complete package is ready for the embassy.',
    '',
    'We also booked the earliest appointment available for you:',
    '',
    `When: ${when}`,
    `Where: ${where}`,
    '',
    'Attached: the official appointment confirmation, the calendar invitation, and your signed visa application exactly as filed.',
    '',
    ...(trip ? [...trackSection(trip, plan), ''] : []),
    'We\'re here if you have any questions before then.',
    '',
    'Warm regards,',
    'Trip.com'
  ].join('\n')
}

function tripEmailFinal(firstName, plan, present) {
  const lead = plan?.kind === 'free'
    ? 'Your travel documents are ready — you\'ll find them attached.'
    : 'Your visa is ready — you\'ll find it attached.'
  return [
    `Hi ${firstName},`,
    '',
    lead,
    '',
    'When you arrive, please present:',
    '',
    present,
    '',
    'Safe travels, and enjoy your trip!',
    '',
    'Warm regards,',
    'Trip.com'
  ].join('\n')
}


/* "(tourism)" and similar qualifiers are engine detail — not shown in UI. */
const cleanVisaName = (s) => String(s || '')
  .replace(/\s*\((tourism|tourist|consular)\)\s*/i, ' ')
  .replace(/\s*[—–-]\s*online\b/i, '')
  .replace(/\s{2,}/g, ' ')
  .trim()

/* Portal UI strings — English and Mandarin (Simplified). */
const STRINGS = {
  en: {
    hero: 'Process your visa, in one click.',
    start: 'Get started', support: '24/7 support',
    yourApps: 'Your applications',
    fullName: 'Full name (as in passport)', emailLbl: 'Email', phoneLbl: 'Phone (for the application)',
    nationality: 'Nationality', destination: 'Destination', departure: 'Departure', ret: 'Return',
    docsHint: 'Passport is enough to start — PDF or JPG.',
    addDocs: 'Add documents', routeCheck: 'Your route',
    routeHint: 'Select nationality and destination to see your route.',
    checking: 'Checking', fee: 'Fee', processingLbl: 'Processing', validity: 'Validity', coveredLbl: 'covered automatically',
    continueBtn: 'Continue', back: 'Back', allApps: 'All applications',
    pipeline: 'Agent pipeline', progress: 'Progress', processBtn: 'Process my visa — one click',
    working: 'Working on your visa — you can sit back', visaDelivered: 'delivered', emailedTo: 'Emailed to',
    monitorNote: 'We keep monitoring the trip and alert you if anything changes before departure.',
    showPdf: 'Show PDF', resend: 'Resend', docsOnFile: 'Documents on file', verified: 'Verified',
    emailUpdates: 'Email confirmations', emailNote: 'Key milestones are confirmed by email to',
    switchWs: 'Switch workspace', kimiVerified: 'Kimi K3 verified', deleteCase: 'Delete case',
    sentLbl: 'Sent', mailFail: 'Mail unavailable', draftedLbl: 'Draft opened in Mail — press Send',
    backStep: 'Turn back a step', apptLbl: 'Interview appointment',
    taskDocs: 'Documents processed', taskForms: 'Forms completed', taskSubmit: 'Submitted via', taskPrepared: 'Package prepared for', taskEntry: 'Entry documents prepared',
    taskAppt: 'Appointment scheduled on', taskApptPending: 'Scheduling the earliest appointment',
    taskDone: 'Visa completed', visaComplete: 'Visa complete!', confirmSent: 'Confirmation email has been sent!',
    ocrRead: 'Passport read', ocrFail: 'Could not read — we will verify manually', ocrReading: 'Reading document',
    foreignDoc: 'in', translateBtn: 'Translate to', translating: 'Translating', translatedTo: 'Translated to', downloadTranslation: 'Download translation',
    agentRecord: 'Agent record', openFile: 'Open',
    addressLbl: 'Home address — used to select your nearest consulate / agency',
    gateTitle: 'We can\'t file this application yet',
    gateNote: 'The verification checks below failed. Fix the details or upload the correct passport, then process again.',
    gateFix: 'Upload corrected passport',
    nameMismatch: 'The name doesn\'t match the passport, which reads:',
    usePassportName: 'Use the passport name', fixNameHint: 'Correct the name above or use the passport name — applications must match the passport exactly.',
    decisionTitle: 'Authority decision',
    decisionNote: 'When the consulate / agency returns its decision, record it here — the traveler is notified automatically with the attached document.',
    approveBtn: 'Record approval — attach visa', refuseBtn: 'Record refusal',
    signTitle: 'Authorize Trip.com to process your visa',
    signNote: 'One signature covers everything: it authorizes Trip.com to prepare, sign, and file your application, and it is placed on the official form. Everything after this runs automatically.',
    signOpenForm: 'Open the filled form', signClear: 'Clear',
    signTyped: 'Type your full name as signature', signDraw: 'or draw your signature',
    signBtn: 'Sign & authorize Trip.com to process my visa',
    signedToast: 'Authorized — preparing your application now',
    reviewTitle: 'Review your application before we submit it',
    reviewNote: 'Check every field of the official application below. Correct anything that is wrong — this is exactly what we file with the embassy.',
    reviewFrom: 'from', reviewConfirmBtn: 'Confirm & submit to embassy', reviewEditHint: 'Tap a field to edit it.',
    reviewConfirmedToast: 'Confirmed — submitting your application now', reviewMissing: 'Needs your input', transmitNow: 'Transmit filing package now', transmittedToast: 'Filing package transmitted',
    docsGateTitle: 'Documents needed before filing', docsGateNote: 'Every uploaded file is identity-checked by AI before it can support your application. Upload the items below — processing resumes automatically.',
    docsMissingHdr: 'Still needed', docsFlaggedHdr: 'Failed verification — replace these', docsFlaggedGeneric: 'This file does not appear to be the required document.',
    docsUploadBtn: 'Upload documents', docsRemoveBtn: 'Remove', docsUploadedToast: 'Documents uploaded — re-verifying now',
    filingTitle: 'File on the official portal',
    filingNote: 'Your application is complete and passed our review. Filing is done on the government\'s own e-visa portal, and the final submit needs a real person: only you can complete the security check (CAPTCHA) and approve the payment. It takes about a minute — then we take over the monitoring.',
    filingOpenTitle: 'Official e-visa portal', filingOpen: 'Open the portal', filingForm: 'Your filled form',
    filingStep1: 'Open the official portal and start (or sign in to) your e-visa application.',
    filingStep2: 'Fill it using your prepared details below, or upload your filled application form.',
    filingStep3: 'Complete the security check (CAPTCHA) — this proves you\'re a real applicant and only you can do it.',
    filingStep4: 'Complete the payment step. The government fee is covered by Trip.com — use the provided payment method or approve the charge when prompted.',
    filingStep5: 'Submit, then copy the application/reference number the portal gives you and paste it below.',
    filingShowData: 'Show my prepared application details', filingCopy: 'Copy all', filingCopied: 'Copied to clipboard',
    filingRefTitle: 'Record your government reference', filingRefHint: 'The number the portal shows after you submit — it lets you and our AI agent both track the decision.',
    filingRefPlaceholder: 'e.g. EVN-2026-XXXXXX', filingConfirm: 'Confirm submission', filingConfirmed: 'Recorded — monitoring your application now', filingNeedRef: 'Enter the reference number the portal gave you',
    autoTitle: 'Automatic decision retrieval',
    transmitTitle: 'Transmit the filing package', transmitHint: 'Sends the signed application to the configured intake address without re-running the pipeline.',
    approveTitle: 'Visa granted', approveHint: 'Attach the visa document received from the authority — the traveler is emailed the complete official record automatically.',
    refuseTitle: 'Application refused', refuseHint: 'The AI prepares the fix plan and emails the traveler what to correct.',
    portalTitle: 'Embassy portal access', portalNote: 'If a portal account was created while filing (visa.go.kr, CEAC…), record it here — the login is emailed to the traveler so they can watch their own application alongside the 24/7 agent monitoring.',
    portalUrlLbl: 'Portal URL', portalUserLbl: 'Username / login', portalPassLbl: 'Password',
    portalSave: 'Save & email the traveler', portalSending: 'Sending…', portalSentToast: 'Portal access emailed to the traveler',
    portalNeedUser: 'Enter the portal username first', portalAdd: 'Record portal login', portalEdit: 'Edit', portalSaved: 'Portal login on file', portalSentHint: 'emailed to the traveler',
    autoNote: 'Monitoring runs automatically. When the decision document arrives, drop it in the decisions folder with reference', openFolder: 'Open decisions folder',
    approvedToast: 'Approval recorded — traveler notified', refusedToast: 'Refusal recorded — fix plan sent to the traveler',
    refuseReason: 'Refusal reason', reapplyBtn: 'Fix & reapply', reapplyToast: 'Corrected reapplication created',
    fixPlanTitle: 'Fix plan',
    chatHello: 'Hello {name} — {phrase}. I\'d love to answer any questions you have.',
    chatHelloDone: 'Hello {name} — good news, your visa has been issued and emailed to you. I\'d love to answer any questions you have.',
    chatHelloGeneric: 'Ask me anything about the visa process!',
    statusPhrase: {
      draft: 'we\'re still setting up your application',
      processing: 'your visa is processing right now',
      action_required: 'your application needs a quick fix from you before we can continue',
      awaiting_signature: 'your application is ready and just needs your signature',
      awaiting_review: 'your completed application is waiting for your review',
      awaiting_documents: 'we\'re waiting on a few documents from you',
      awaiting_filing: 'your application is ready to submit on the official portal — it just needs your CAPTCHA and payment',
      prepared: 'your application package is complete and ready for filing',
      submitted: 'your application has been submitted',
      monitoring: 'your application is with the embassy and we\'re monitoring it around the clock',
      refused: 'the embassy declined this application — we\'ve emailed you the fix plan and can reapply together',
      ready: 'you\'re all set — no visa is needed for this trip',
      issued: 'your visa has been issued and emailed to you'
    },
    chatPlaceholder: 'Ask about your visa, documents, timing…',
    status: { draft: 'Draft', processing: 'Processing', action_required: 'Action needed', awaiting_signature: 'Awaiting your signature', awaiting_review: 'Awaiting your review', awaiting_documents: 'Documents needed', awaiting_filing: 'Ready to file', prepared: 'Package prepared', submitted: 'Submitted', monitoring: 'Awaiting decision', issued: 'Visa issued', refused: 'Refused', ready: 'Ready to travel' }
  },
  zh: {
    hero: '一键办理您的签证',
    start: '立即开始', support: '24/7 在线客服',
    yourApps: '我的申请',
    fullName: '姓名（与护照一致）', emailLbl: '电子邮箱', phoneLbl: '电话（用于申请表）',
    nationality: '国籍', destination: '目的地', departure: '出发日期', ret: '回程日期',
    docsHint: '上传护照即可开始 — 支持 PDF 或 JPG。',
    addDocs: '添加文件', routeCheck: '您的路线',
    routeHint: '选择国籍和目的地，即可查看签证路线。',
    checking: '正在查询', fee: '费用', processingLbl: '处理时间', validity: '有效期', coveredLbl: '项已自动覆盖',
    continueBtn: '继续', back: '返回', allApps: '全部申请',
    pipeline: '办理流程', progress: '进度', processBtn: '一键办理签证',
    working: '正在处理 — 您无需任何操作', visaDelivered: '已送达', emailedTo: '已发送至',
    monitorNote: '我们将持续关注您的行程，出发前如有变化会及时提醒您。',
    showPdf: '查看 PDF', resend: '重新发送', docsOnFile: '已上传文件', verified: '已核验',
    emailUpdates: '邮件确认记录', emailNote: '关键节点均会通过邮件确认发送至',
    switchWs: '切换工作区', kimiVerified: 'Kimi K3 已核验', deleteCase: '删除申请',
    sentLbl: '已发送', mailFail: '邮件发送失败', draftedLbl: '草稿已在“邮件”中打开 — 请点击发送',
    backStep: '返回上一步', apptLbl: '面签预约',
    taskDocs: '文件已处理', taskForms: '表格已填写', taskSubmit: '已提交至', taskPrepared: '材料已备好，待提交至', taskEntry: '入境材料已备好',
    taskAppt: '预约已安排：', taskApptPending: '正在预约最早的可用时间',
    taskDone: '签证已完成', visaComplete: '签证办好啦！', confirmSent: '确认邮件已发送！',
    ocrRead: '护照已识别', ocrFail: '无法识别 — 将人工核验', ocrReading: '正在识别文件',
    foreignDoc: '语言：', translateBtn: '翻译为', translating: '翻译中', translatedTo: '已翻译为', downloadTranslation: '下载翻译件',
    agentRecord: '办理记录', openFile: '打开',
    addressLbl: '家庭住址 — 用于匹配最近的领馆 / 代办机构',
    gateTitle: '暂时无法提交申请',
    gateNote: '以下核验未通过。请修正信息或上传正确的护照后重新办理。',
    gateFix: '上传正确的护照',
    nameMismatch: '姓名与护照不一致，护照上为：',
    usePassportName: '使用护照姓名', fixNameHint: '请修改上方姓名或直接使用护照姓名 — 申请信息必须与护照完全一致。',
    decisionTitle: '审批结果',
    decisionNote: '领馆 / 代办机构出结果后在此记录 — 系统会自动通知旅客并附上文件。',
    approveBtn: '记录批准 — 附上签证', refuseBtn: '记录拒签',
    signTitle: '授权 Trip.com 办理您的签证',
    signNote: '一次签名即完成全部授权：授权 Trip.com 为您准备、签署并提交申请，签名同时用于官方申请表。此后全部流程自动进行。',
    signOpenForm: '打开已填写的申请表', signClear: '清除',
    signTyped: '输入您的全名作为签名', signDraw: '或手写签名',
    signBtn: '签署并授权 Trip.com 办理我的签证',
    signedToast: '已授权 — 正在准备您的申请',
    reviewTitle: '提交前请核对您的申请',
    reviewNote: '请核对下方官方申请表的每一项。如有错误请修改 — 这将是我们提交给使馆的内容。',
    reviewFrom: '来源：', reviewConfirmBtn: '确认并提交至使馆', reviewEditHint: '点击字段即可编辑。',
    reviewConfirmedToast: '已确认 — 正在提交您的申请', reviewMissing: '需要您填写', transmitNow: '立即发送申请材料', transmittedToast: '申请材料已发送',
    docsGateTitle: '递交前还需要材料', docsGateNote: '每份上传文件都会先经 AI 核验真实性，才能用于您的申请。请上传以下材料 — 上传后自动继续处理。',
    docsMissingHdr: '仍需提供', docsFlaggedHdr: '未通过核验 — 请替换', docsFlaggedGeneric: '该文件似乎不是所需的材料。',
    docsUploadBtn: '上传材料', docsRemoveBtn: '移除', docsUploadedToast: '材料已上传 — 正在重新核验',
    filingTitle: '在官方网站递交',
    filingNote: '您的申请已填好并通过审核。递交在政府官方电子签证网站上完成，最后一步需要本人操作：只有您能完成安全验证（验证码）并确认付款。大约一分钟即可 — 之后由我们接手跟进。',
    filingOpenTitle: '官方电子签证网站', filingOpen: '打开网站', filingForm: '您填好的表格',
    filingStep1: '打开官方网站，开始（或登录）您的电子签证申请。',
    filingStep2: '用下方为您准备好的资料填写，或上传您填好的申请表。',
    filingStep3: '完成安全验证（验证码）— 这用于证明您是真实申请人，只能由您本人完成。',
    filingStep4: '完成付款步骤。政府签证费由 Trip.com 承担 — 使用提供的付款方式或在提示时确认扣款。',
    filingStep5: '提交后，复制网站给出的申请/参考编号并粘贴到下方。',
    filingShowData: '查看我准备好的申请资料', filingCopy: '全部复制', filingCopied: '已复制到剪贴板',
    filingRefTitle: '记录您的官方参考编号', filingRefHint: '提交后网站显示的编号 — 供您和我们的 AI 代理共同跟进审批结果。',
    filingRefPlaceholder: '例如 EVN-2026-XXXXXX', filingConfirm: '确认已提交', filingConfirmed: '已记录 — 正在为您跟进申请', filingNeedRef: '请填写网站给出的参考编号',
    autoTitle: '自动获取审批结果',
    transmitTitle: '发送申请材料', transmitHint: '将已签名的申请发送至配置的受理地址，无需重新处理。',
    approveTitle: '签证已批准', approveHint: '附上使领馆签发的签证文件 — 系统自动将完整官方材料发送给旅客。',
    refuseTitle: '申请被拒', refuseHint: 'AI 自动生成补救方案并邮件告知旅客更正内容。',
    portalTitle: '使领馆网站账号', portalNote: '如递交时创建了官网账号（visa.go.kr、CEAC 等），请在此记录 — 登录信息将发送给旅客，旅客可自行查看申请进度，AI 代理仍全天候监控。',
    portalUrlLbl: '网站地址', portalUserLbl: '用户名 / 登录', portalPassLbl: '密码',
    portalSave: '保存并邮件旅客', portalSending: '发送中…', portalSentToast: '账号信息已发送给旅客',
    portalNeedUser: '请先填写用户名', portalAdd: '记录网站账号', portalEdit: '编辑', portalSaved: '账号已记录', portalSentHint: '已邮件旅客',
    autoNote: '系统自动跟进。收到签证文件后，请将其放入决定文件夹，文件名包含编号', openFolder: '打开决定文件夹',
    approvedToast: '已记录批准 — 旅客已收到通知', refusedToast: '已记录拒签 — 补救方案已发送给旅客',
    refuseReason: '拒签原因', reapplyBtn: '修正并重新申请', reapplyToast: '已创建修正后的新申请',
    fixPlanTitle: '补救方案',
    chatHello: '您好，{name} — {phrase}，有任何问题我都很乐意解答。',
    chatHelloDone: '您好，{name} — 好消息，您的签证已签发并发送至您的邮箱，有任何问题我都很乐意解答。',
    chatHelloGeneric: '签证办理有任何问题，随时问我！',
    statusPhrase: {
      draft: '您的申请正在准备中',
      processing: '您的签证正在办理中',
      action_required: '您的申请需要您先补充处理才能继续',
      awaiting_signature: '您的申请已就绪，只差您的签名',
      awaiting_review: '您的申请已填好，等待您核对确认',
      awaiting_documents: '我们还在等您补充几份材料',
      awaiting_filing: '您的申请已可在官方网站递交 — 只差您完成验证码和付款',
      prepared: '您的申请材料已备齐，随时可以递交',
      submitted: '您的申请已递交',
      monitoring: '您的申请已在使领馆审理，我们全天候为您跟进',
      refused: '使领馆未批准这次申请 — 补救方案已发送到您的邮箱，我们可以一起重新申请',
      ready: '您已一切就绪，本次行程无需签证',
      issued: '您的签证已签发并发送至您的邮箱'
    },
    chatPlaceholder: '咨询签证、材料、时间等问题…',
    status: { draft: '草稿', processing: '处理中', action_required: '需要补充', awaiting_signature: '待您签名', awaiting_review: '待您核对', awaiting_documents: '待补材料', awaiting_filing: '待递交', prepared: '材料已备好', submitted: '已提交', monitoring: '等待审批', issued: '签证已出', refused: '已拒签', ready: '可以出行' }
  }
}

const LANGS = [{ id: 'en', label: 'EN' }, { id: 'zh', label: '简体中文' }]

const STATUS_PCT = { draft: 8, processing: 55, action_required: 35, awaiting_signature: 58, awaiting_documents: 61, awaiting_review: 64, awaiting_filing: 70, prepared: 68, submitted: 75, monitoring: 85, issued: 100, refused: 78, ready: 100 }

export default function TripPortal({ onSwitchRole }) {
  const toast = useToast()
  const [trips, setTrips] = useState([])
  const [view, setView] = useState('home') // home | new | detail
  const [activeId, setActiveId] = useState(null)
  const [lang, setLang] = useState('en')
  const t = (k) => STRINGS[lang]?.[k] ?? STRINGS.en[k]

  const refresh = () => ellis.listTrips().then(setTrips)
  useEffect(() => { refresh() }, [])

  const active = trips.find((x) => x.id === activeId)

  return (
    <div className="trip-shell">
      <header className="trip-head">
        <img src={tripcomLogo} alt="Trip.com" style={{ height: 30 }} />
        <div className="trip-langs">
          {LANGS.map((l) => (
            <button key={l.id} className={`trip-lang${lang === l.id ? ' trip-lang--on' : ''}`} onClick={() => setLang(l.id)}>{l.label}</button>
          ))}
        </div>
      </header>

      <div className="page" style={{ maxWidth: 980, margin: '0 auto' }}>
        {view === 'home' && (
          <TripHome trips={trips} t={t}
            onNew={() => setView('new')}
            onOpen={(id) => { setActiveId(id); setView('detail') }}
            onDelete={async (id) => { await ellis.deleteTrip(id); toast(t('deleteCase')); refresh() }}
            onSwitchRole={onSwitchRole} />
        )}
        {view === 'new' && (
          <NewTrip toast={toast} t={t}
            onCancel={() => setView('home')}
            onCreated={async (trip) => { await refresh(); setActiveId(trip.id); setView('detail') }} />
        )}
        {view === 'detail' && active && (
          <TripDetail trip={active} toast={toast} t={t} onChanged={refresh} onBack={() => { setView('home'); refresh() }} />
        )}
      </div>

      {/* Support chatbox — present on every page of the portal */}
      <TripChat key={active?.id || 'global'} trip={view === 'detail' ? active : null} plan={active?.plan || null} t={t} />
    </div>
  )
}

/* ---------------- Home: one headline, one arrow ---------------- */
function TripHome({ trips, t, onNew, onOpen, onDelete, onSwitchRole }) {
  const [menu, setMenu] = useState(null) // { x, y, id }
  useEffect(() => {
    if (!menu) return
    const close = () => setMenu(null)
    window.addEventListener('click', close)
    window.addEventListener('contextmenu', close, true)
    return () => { window.removeEventListener('click', close); window.removeEventListener('contextmenu', close, true) }
  }, [menu])
  return (
    <>
      <button className="trip-back" onClick={onSwitchRole} title={t('switchWs')} aria-label={t('switchWs')}>
        <Icon.back style={{ width: 17, height: 17 }} />
      </button>
      <div className="trip-home">
        {/* The whole promise as one drawing — prepare, submit, done — so the
            headline needs no subline at all. */}
        <div className="anim-rise" style={{ marginBottom: 8 }}>
          <PipelineIllustration size={200} />
        </div>
        <h1 className="trip-home__title anim-rise-1">{t('hero')}</h1>
        <button className="trip-cta anim-rise-2" onClick={onNew}>
          {t('start')}
          <span className="trip-cta__arrow" style={{ display: 'inline-flex' }}><Icon.arrow style={{ width: 18, height: 18 }} /></span>
        </button>
      </div>

      {trips.length > 0 && (
        <div className="trip-apps anim-rise-3">
          <div className="eyebrow trip-eyebrow" style={{ marginBottom: 12 }}>{t('yourApps')}</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {trips.map((x) => (
              <button key={x.id} className="row card-hover" style={{ width: '100%', textAlign: 'left', cursor: 'pointer' }}
                onClick={() => onOpen(x.id)}
                onContextMenu={(e) => { e.preventDefault(); e.stopPropagation(); setMenu({ x: e.clientX, y: e.clientY, id: x.id }) }}>
                <div className="row__main">
                  <div className="row__title">{x.name} — {x.nationality} → {x.destination}</div>
                  <div className="row__sub">{x.departure || '—'}{x.return ? ` – ${x.return}` : ''} · {fmtDate(x.createdAt)}</div>
                  <div className="trip-minibar"><div className="trip-minibar__fill" style={{ width: `${STATUS_PCT[x.status] ?? 8}%` }} /></div>
                </div>
                <span className="chip">{t('status')[x.status] || x.status}</span>
                <Icon.arrow style={{ width: 16, height: 16, color: 'var(--trip-gray)', marginLeft: 10 }} />
              </button>
            ))}
          </div>
        </div>
      )}

      {menu && (
        <div className="trip-ctx" style={{ left: menu.x, top: menu.y }}>
          <button className="trip-ctx__item" onClick={() => { onDelete(menu.id); setMenu(null) }}>
            <Icon.trash style={{ width: 14, height: 14 }} /> {t('deleteCase')}
          </button>
        </div>
      )}

    </>
  )
}

/* Searchable country dropdown: always opens downward, scrolls instead of
   growing, commits typed text when it exactly matches an option. */
function Combo({ value, onChange, options, placeholder }) {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const filtered = options.filter((o) => o.toLowerCase().includes(q.toLowerCase()))
  return (
    <div className="trip-combo">
      <input className="input" value={open ? q : value} placeholder={placeholder}
        onFocus={() => { setOpen(true); setQ('') }}
        onBlur={() => setTimeout(() => setOpen(false), 120)}
        onChange={(e) => {
          const v = e.target.value
          setQ(v)
          const exact = options.find((o) => o.toLowerCase() === v.trim().toLowerCase())
          if (exact) { onChange(exact); setOpen(false) }
        }} />
      {open && filtered.length > 0 && (
        <div className="trip-combo__list">
          {filtered.map((o) => (
            <button key={o} className="trip-combo__opt" onMouseDown={(e) => { e.preventDefault(); onChange(o); setOpen(false) }}>
              <span className="trip-combo__flag">{flag(o)}</span> {o}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

/* ---------------- New application ---------------- */
function NewTrip({ toast, t, onCancel, onCreated }) {
  const [f, setF] = useState({ name: '', email: '', phone: '', address: '', nationality: '', destination: '', departure: '', return: '' })
  const [docs, setDocs] = useState([])
  const [plan, setPlan] = useState(null)
  const [planLoading, setPlanLoading] = useState(false)
  const set = (patch) => setF((x) => ({ ...x, ...patch }))
  const today = new Date().toISOString().slice(0, 10)

  // Live route classification as soon as the route is known.
  useEffect(() => {
    if (!f.nationality || !f.destination) { setPlan(null); return }
    let dead = false
    setPlanLoading(true)
    ellis.planTrip({ traveler: { ...f, documents: docs.map((d) => d.name) } }).then((res) => {
      if (!dead) { setPlan(res.ok ? res.data : null); setPlanLoading(false) }
    })
    return () => { dead = true }
  }, [f.nationality, f.destination]) // eslint-disable-line react-hooks/exhaustive-deps

  async function addDocs() {
    const picked = await ellis.pickDocuments()
    if (!picked.length) return
    const withState = picked.map((d) => ({ ...d, ocr: ['pdf', 'jpg', 'jpeg', 'png', 'heic', 'webp'].includes(d.kind) ? 'reading' : null }))
    setDocs((d) => [...d, ...withState])
    toast(`${picked.length} document(s) added`)
    // Real on-device OCR (Apple Vision) on every image/PDF; MRZ fields
    // auto-fill the form and travel with the document.
    for (const doc of withState) {
      if (doc.ocr !== 'reading') continue
      const res = await ellis.ocrDoc({ path: doc.path })
      setDocs((cur) => cur.map((d) => d.path === doc.path && d.name === doc.name
        ? { ...d, ocr: res.ok ? 'done' : 'failed', ocrError: res.ok ? null : res.error, extracted: res.ok ? res.fields : null, mrzFound: !!res.mrzFound, text: d.text || (res.ok ? (res.text || '').slice(0, 4000) : '') }
        : d))
      if (res.ok && res.mrzFound) {
        const fx = res.fields || {}
        setF((prev) => ({
          ...prev,
          name: prev.name.trim() ? prev.name : (fx.fullName || prev.name),
          nationality: prev.nationality ? prev.nationality : (fx.nationality && fx.nationality.length > 3 ? fx.nationality : prev.nationality)
        }))
      }
      // Detect the document's language so we can offer a translation if it's
      // not in the destination's filing language.
      if (doc.path) {
        ellis.detectDocLanguage(doc.path).then((lr) => {
          if (lr?.ok && lr.lang && lr.lang !== 'en' && lr.lang !== 'und') {
            setDocs((cur) => cur.map((d) => d.path === doc.path && d.name === doc.name ? { ...d, lang: lr.lang, langName: lr.langName } : d))
          }
        }).catch(() => {})
      }
    }
  }

  async function translateOne(doc, idx) {
    setDocs((cur) => cur.map((d, j) => j === idx ? { ...d, translating: true } : d))
    const res = await ellis.translateDoc({ path: doc.path, docName: doc.name, destination: f.destination || 'USA' })
    setDocs((cur) => cur.map((d, j) => j === idx ? { ...d, translating: false, translation: res.ok ? res : null, translateError: res.ok ? null : (res.reason || res.error) } : d))
    if (res.ok) toast(`${t('translatedTo')} ${res.targetLangName}`)
    else if (res.skipped) toast('Already in the required language')
    else toast(res.error || 'Translation unavailable')
  }

  function setDeparture(v) {
    const patch = { departure: v }
    if (f.return && v && f.return < v) patch.return = ''
    set(patch)
  }

  const passportName = docs.find((d) => d.mrzFound && d.extracted?.fullName)?.extracted?.fullName || null
  const nameMismatch = !!(passportName && f.name.trim() && !looseNameMatch(f.name, passportName))
  const ready = f.name.trim() && f.email.trim() && f.nationality && f.destination && !nameMismatch
  const startedRef = useRef(false)
  async function start(auto = false) {
    if (startedRef.current) return
    startedRef.current = true
    const passport = docs.find((d) => d.mrzFound)?.extracted || null
    const trip = await ellis.createTrip({
      ...f,
      documents: docs.map((d) => ({ name: d.name, path: d.path || null, kind: d.kind || 'text', text: (d.text || '').slice(0, 4000), extracted: d.extracted || null, mrzFound: !!d.mrzFound }))
        .concat(docs.filter((d) => d.translation?.path).map((d) => ({
          name: `${d.name.replace(/\.[^.]+$/, '')} — ${d.translation.targetLangName} translation.pdf`,
          path: d.translation.path, kind: 'pdf', text: '', extracted: null, mrzFound: false
        }))),
      passport,
      autoStart: !!auto,
      status: 'draft', emailLog: []
    })
    onCreated(trip)
  }

  const docIcon = (d) => (d.kind === 'pdf' ? Icon.form : ['jpg', 'jpeg', 'png', 'heic', 'webp'].includes(d.kind) ? Icon.eye : Icon.doc)

  return (
    <div className="trip-newwrap">
      <button className="trip-back" onClick={onCancel} title={t('back')} aria-label={t('back')}>
        <Icon.back style={{ width: 17, height: 17 }} />
      </button>

      <div className="trip-newgrid">
        <div>
          <div className="card trip-sec" style={{ animationDelay: '0ms' }}>
            <div className="field" style={{ marginBottom: nameMismatch ? 4 : undefined }}>
              <input className="input" value={f.name} onChange={(e) => set({ name: e.target.value })} placeholder={t('fullName')} autoFocus
                style={nameMismatch ? { borderColor: '#c0392b' } : undefined} /></div>
            {nameMismatch && (
              <div style={{ fontSize: 12.5, color: '#c0392b', margin: '0 0 12px', lineHeight: 1.5 }}>
                {t('nameMismatch')} <b>{passportName}</b>
                <button className="btn btn--ghost btn--sm" style={{ marginLeft: 8 }}
                  onClick={() => set({ name: passportName.replace(/\b\w/g, (c) => c.toUpperCase()).replace(/(?<=\w)\w+/g, (w) => w.toLowerCase()) })}>
                  {t('usePassportName')}
                </button>
                <div>{t('fixNameHint')}</div>
              </div>
            )}
            <div className="field">
              <input className="input" value={f.email} onChange={(e) => set({ email: e.target.value })} placeholder={t('emailLbl')} /></div>
            <div className="grid grid-2">
              <div className="field">
                <input className="input" value={f.phone} onChange={(e) => set({ phone: e.target.value })} placeholder={t('phoneLbl')} /></div>
              <div className="field">
                <input className="input" value={f.address} onChange={(e) => set({ address: e.target.value })} placeholder={t('addressLbl')} /></div>
            </div>
            <div className="grid grid-2">
              <div className="field">
                <Combo value={f.nationality} onChange={(v) => set({ nationality: v })} options={COUNTRIES} placeholder={t('nationality')} /></div>
              <div className="field">
                <Combo value={f.destination} onChange={(v) => set({ destination: v })} options={DESTS} placeholder={t('destination')} /></div>
            </div>
            <div className="grid grid-2" style={{ marginBottom: 0 }}>
              <div className="field" style={{ marginBottom: 0 }}><label>{t('departure')}</label>
                <input className="input" type="date" min={today} value={f.departure} onChange={(e) => setDeparture(e.target.value)} /></div>
              <div className="field" style={{ marginBottom: 0 }}><label>{t('ret')}</label>
                <input className="input" type="date" min={f.departure || today} value={f.return} onChange={(e) => set({ return: e.target.value })} /></div>
            </div>
          </div>

          <div className="card trip-sec" style={{ animationDelay: '70ms', marginTop: 14 }}>
            {docs.map((d, i) => {
              const DI = docIcon(d)
              return (
                <div key={i} className="trip-docrow" style={{ flexWrap: 'wrap' }}>
                  <DI style={{ width: 15, height: 15, flexShrink: 0 }} />
                  <span style={{ flex: 1, fontSize: 13.5, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{d.name}</span>
                  {d.kind && d.kind !== 'text' && <span className="chip" style={{ fontSize: 10.5, padding: '2px 8px' }}>{d.kind.toUpperCase()}</span>}
                  {d.ocr === 'reading' && <span className="chip" style={{ fontSize: 10.5, padding: '2px 8px' }}><span className="spinner spinner--ink" style={{ width: 9, height: 9 }} /> {t('ocrReading')}</span>}
                  {d.ocr === 'failed' && <span className="chip" style={{ fontSize: 10.5, padding: '2px 8px', color: '#a15c00' }}>{t('ocrFail')}</span>}
                  <button className="iconbtn" onClick={() => setDocs(docs.filter((_, j) => j !== i))} aria-label="remove"><Icon.trash style={{ width: 14, height: 14 }} /></button>
                  {d.ocr === 'done' && d.mrzFound && d.extracted && (
                    <div style={{ width: '100%', display: 'flex', gap: 6, alignItems: 'center', fontSize: 12, color: 'var(--trip-blue)', fontWeight: 600 }}>
                      <Icon.check style={{ width: 13, height: 13 }} />
                      {t('ocrRead')}: {d.extracted.fullName || '—'}{d.extracted.passportNumber ? ` · ${d.extracted.passportNumber}` : ''}{d.extracted.nationality ? ` · ${d.extracted.nationality}` : ''}{d.extracted.expiryDate ? ` · exp ${d.extracted.expiryDate}` : ''}
                    </div>
                  )}
                  {d.lang && !d.translation && (
                    <div style={{ width: '100%', display: 'flex', gap: 8, alignItems: 'center', fontSize: 12, color: 'var(--trip-gray)' }}>
                      <span className="chip" style={{ fontSize: 10.5, padding: '2px 8px' }}>{t('foreignDoc')} {d.langName}</span>
                      <button className="btn btn--ghost btn--sm" disabled={d.translating} onClick={() => translateOne(d, i)}>
                        {d.translating ? <><span className="spinner spinner--ink" style={{ width: 9, height: 9 }} /> {t('translating')}…</> : `${t('translateBtn')} ${f.destination || 'English'}`}
                      </button>
                    </div>
                  )}
                  {d.translation && (
                    <div style={{ width: '100%', display: 'flex', gap: 8, alignItems: 'center', fontSize: 12, color: '#1a7f37', fontWeight: 600 }}>
                      <Icon.check style={{ width: 13, height: 13 }} /> {t('translatedTo')} {d.translation.targetLangName}
                      <button className="btn btn--ghost btn--sm" onClick={() => ellis.revealFile(d.translation.path)}>{t('downloadTranslation')}</button>
                    </div>
                  )}
                </div>
              )
            })}
            <button className="trip-drop" onClick={addDocs}>
              <Icon.plus style={{ width: 18, height: 18 }} />
              <span>{t('addDocs')}</span>
            </button>
            <div style={{ fontSize: 12, color: 'var(--trip-gray)', marginTop: 10, textAlign: 'center' }}>{t('docsHint')}</div>
          </div>
        </div>

        <div>
          <div className="card trip-sec" style={{ animationDelay: '140ms', marginBottom: 14 }}>
            {!f.nationality || !f.destination ? (
              <div style={{ color: 'var(--trip-gray)', fontSize: 13.5, lineHeight: 1.6, textAlign: 'center', padding: '18px 0' }}>{t('routeHint')}</div>
            ) : planLoading && !plan ? (
              <div style={{ color: 'var(--trip-gray)', fontSize: 13.5, textAlign: 'center', padding: '18px 0' }}><span className="spinner spinner--ink" /> {t('checking')}…</div>
            ) : plan ? (
              <RouteGraph plan={plan} f={f} docs={docs} t={t} />
            ) : null}
          </div>

          <button className="btn btn--block trip-continue" disabled={!ready} onClick={() => start(true)}>{t('continueBtn')}</button>
        </div>
      </div>
    </div>
  )
}

/* Route visual: origin -> destination line, classification, three stat tiles,
   and a coverage bar instead of a wall of text. */
function RouteGraph({ plan, f, docs, t }) {
  const covered = plan.requirements.filter((r) =>
    /trip\.com|auto|ellis|pre-fill|appointment/i.test(r) || docs.some((d) => d.name.toLowerCase().includes(r.toLowerCase().split(' ')[0]))
  ).length
  const total = plan.requirements.length
  const pct = Math.round((covered / Math.max(1, total)) * 100)
  return (
    <div className="trip-fadein">
      <div className="trip-route">
        <div className="trip-route__pt"><span className="trip-route__flag">{flag(f.nationality)}</span></div>
        <div className="trip-route__line"><span className="trip-route__plane"><Icon.plane style={{ width: 15, height: 15 }} /></span></div>
        <div className="trip-route__pt"><span className="trip-route__flag">{flag(f.destination)}</span></div>
      </div>
      <div className="trip-route__visa">{cleanVisaName(plan.headline)}</div>
      {plan.summary && (
        <div style={{ fontSize: 12.5, color: 'var(--trip-gray)', lineHeight: 1.45, textAlign: 'center', margin: '8px 0 12px', padding: '0 6px' }}>
          {plan.summary}
        </div>
      )}
      {(() => {
        const stats = [
          [t('fee'), plan.fee],
          ...(!isNoneVal(plan.processing) ? [[t('processingLbl'), plan.processing]] : []),
          [t('validity'), plan.validity]
        ]
        return (
          <div className={`trip-stats3${stats.length === 2 ? ' trip-stats3--2' : ''}`}>
            {stats.map(([k, v]) => (
              <div key={k} className="trip-stat3"><div className="trip-stat3__k">{k}</div><div className="trip-stat3__v">{v}</div></div>
            ))}
          </div>
        )
      })()}
      <div className="trip-ready">
        <div className="trip-ready__bar"><div className="trip-ready__fill" style={{ width: `${pct}%` }} /></div>
      </div>
    </div>
  )
}

/* Review-and-confirm: the traveler sees the completed official application,
   edits any field, and confirms submission before it is filed. */
function ReviewCard({ trip, plan, t, toast, onConfirmed }) {
  const form = trip.applicationForm || { fields: [], form: `${trip.destination} visa application` }
  const [fields, setFields] = useState(() => (form.fields || []).map((f) => ({ ...f })))
  const [busy, setBusy] = useState(false)

  // Fields verified against the machine-read passport cannot be free-edited
  // here — a wrong passport goes through the replace-passport flow so the
  // verification gate re-runs. Trip details remain editable.
  const LOCKED = ['Surname', 'Given names', 'Full name', 'Nationality', 'Passport number', 'Date of birth', 'Sex', 'Passport expiry', 'Issuing country', 'Contact email', 'Filing channel']
  const locked = (f) => LOCKED.includes(f.label)
  const edit = (i, v) => setFields((cur) => cur.map((f, j) => j === i ? { ...f, value: v, source: v && v !== f.value ? 'edited by applicant' : f.source } : f))

  async function confirm() {
    setBusy(true)
    const missing = fields.filter((f) => !f.value || !String(f.value).trim()).map((f) => f.label)
    const updatedForm = { ...form, fields, missing, completeness: fields.length ? Math.round(((fields.length - missing.length) / fields.length) * 100) : 0 }
    const live = { ...trip, applicationForm: updatedForm }
    // Regenerate the official form PDF with the confirmed/corrected values.
    const official = await downloadTripOfficialFormPdfToDesktop(live, plan, updatedForm)
    await ellis.tripAgent.record(trip.id, {
      step: 'review',
      title: 'Application reviewed and confirmed by the traveler',
      detail: 'The traveler reviewed every field and confirmed submission. This is the version filed with the embassy.',
      artifact: official.ok ? official.path : null
    })
    await ellis.updateTrip(trip.id, { applicationForm: updatedForm, reviewConfirmed: true, formPath: official.ok ? official.path : trip.formPath })
    toast(t('reviewConfirmedToast'))
    onConfirmed()
  }

  return (
    <div className="card" style={{ marginTop: 14, textAlign: 'left', padding: '18px 20px', borderLeft: '3px solid var(--trip-blue, #287dfa)' }}>
      <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 4 }}>{t('reviewTitle')}</div>
      <div style={{ fontSize: 12.5, color: 'var(--trip-gray)', marginBottom: 4 }}>{t('reviewNote')}</div>
      <div style={{ fontSize: 11.5, color: 'var(--trip-gray)', marginBottom: 12 }}>{form.form} · {t('reviewEditHint')}</div>
      {trip.formPath && (
        <button className="btn btn--ghost btn--sm" style={{ marginBottom: 12 }} onClick={() => ellis.revealFile(trip.formPath)}>{t('signOpenForm')}</button>
      )}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginBottom: 14 }}>
        {fields.map((f, i) => (
          <div key={i} className="field" style={{ marginBottom: 0 }}>
            <label style={{ fontSize: 10.5 }}>{f.label}{f.source ? <span style={{ color: 'var(--trip-gray)', fontWeight: 400 }}> · {t('reviewFrom')} {f.source}</span> : ''}</label>
            <input className="input" value={f.value || ''} placeholder={t('reviewMissing')}
              disabled={locked(f)}
              style={!f.value ? { borderColor: '#c96b00' } : locked(f) ? { opacity: 0.75 } : undefined}
              onChange={(e) => edit(i, e.target.value)} />
          </div>
        ))}
      </div>
      <button className="btn" disabled={busy} onClick={confirm}>
        {busy ? <><span className="spinner" /> {t('reviewConfirmedToast')}</> : t('reviewConfirmBtn')}
      </button>
    </div>
  )
}

/* Documents gate: every requirement must be satisfied by a verified document
   before the application is filed. The traveler uploads what's missing (each
   upload is identity-checked by Kimi K3 vision on the next run) or explicitly
   chooses to bring the remainder to the appointment. */
function DocumentsCard({ trip, t, toast, onResume }) {
  const [busy, setBusy] = useState(false)
  const missing = trip.gapAnalysis?.missing || []
  const flagged = (trip.documents || []).filter((d) => d.docCheck?.plausible === false && !d.docCheck?.overridden)

  async function upload() {
    const picked = await ellis.pickDocuments()
    if (!picked.length) return
    setBusy(true)
    const newDocs = picked.map((d) => ({ name: d.name, path: d.path || null, kind: d.kind || 'text', text: (d.text || '').slice(0, 4000), extracted: null, mrzFound: false }))
    await ellis.updateTrip(trip.id, { documents: [...(trip.documents || []), ...newDocs], docsReadyAt: Date.now() })
    await ellis.tripAgent.record(trip.id, { step: 'gate', title: `${newDocs.length} additional document(s) uploaded`, detail: newDocs.map((d) => d.name).join(', ') + ' — verification re-runs automatically.' })
    toast(t('docsUploadedToast'))
    onResume()
  }

  async function removeFlagged(name) {
    await ellis.updateTrip(trip.id, { documents: (trip.documents || []).filter((d) => d.name !== name) })
    onResume()
  }

  return (
    <div className="card" style={{ marginTop: 14, textAlign: 'left', padding: '18px 20px', borderLeft: '3px solid #c96b00' }}>
      <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 4 }}>{t('docsGateTitle')}</div>
      <div style={{ fontSize: 12.5, color: 'var(--trip-gray)', marginBottom: 12 }}>{t('docsGateNote')}</div>
      {missing.length > 0 && (
        <div style={{ marginBottom: 12 }}>
          <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: 0.5, textTransform: 'uppercase', color: 'var(--trip-gray)', marginBottom: 6 }}>{t('docsMissingHdr')}</div>
          {missing.map((m, i) => (
            <div key={i} style={{ fontSize: 12.5, padding: '5px 0', borderBottom: '1px solid var(--trip-line, #eef2f8)' }}>
              <b>{m.requirement}</b>{m.why ? <span style={{ color: 'var(--trip-gray)' }}> — {m.why}</span> : null}
            </div>
          ))}
        </div>
      )}
      {flagged.length > 0 && (
        <div style={{ marginBottom: 12 }}>
          <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: 0.5, textTransform: 'uppercase', color: '#c96b00', marginBottom: 6 }}>{t('docsFlaggedHdr')}</div>
          {flagged.map((d, i) => (
            <div key={i} style={{ fontSize: 12.5, padding: '5px 0', borderBottom: '1px solid var(--trip-line, #eef2f8)', display: 'flex', justifyContent: 'space-between', gap: 8 }}>
              <span><b>{d.name}</b><span style={{ color: 'var(--trip-gray)' }}> — {(d.docCheck?.issues || []).join('; ') || d.docCheck?.summary || t('docsFlaggedGeneric')}</span></span>
              <button className="btn btn--ghost btn--sm" onClick={() => removeFlagged(d.name)}>{t('docsRemoveBtn')}</button>
            </div>
          ))}
        </div>
      )}
      <button className="btn" disabled={busy} onClick={upload}>{busy ? <span className="spinner" /> : t('docsUploadBtn')}</button>
    </div>
  )
}

/* Assisted online filing: for e-visa / eTA routes the whole application is a
   government web form. Ellis has prepared and reviewed everything; the one
   step that must be human — proving you're a person (CAPTCHA), authorizing the
   payment, and pressing submit — is handed to the traveler on the official
   portal. They then record the government reference and monitoring resumes.
   This is the legitimate pattern: the applicant completes their own CAPTCHA on
   the real government site; Ellis never defeats bot-detection. */
function AssistedFilingCard({ trip, plan, t, toast, onResume }) {
  // Prefer a real https URL (researched applyUrl, then a URL-shaped portalUrl);
  // some routes only carry a portal *name*, in which case we show the name and
  // don't render a link that would open a non-URL.
  const candidates = [trip.portalResearch?.applyUrl, plan?.portalUrl, plan?.portal].filter(Boolean)
  const portalUrl = candidates.find((u) => /^https?:\/\//i.test(u)) || ''
  const portalName = portalUrl || candidates[0] || plan?.portal || ''
  const [ref, setRef] = useState('')
  const [busy, setBusy] = useState(false)
  const fields = (trip.applicationForm?.fields || []).filter((f) => f.value)

  async function copyAll() {
    const text = fields.map((f) => `${f.label}: ${f.value}`).join('\n')
    try { await navigator.clipboard.writeText(text); toast(t('filingCopied')) } catch { /* clipboard blocked */ }
  }

  async function confirmFiled() {
    const reference = ref.trim()
    if (!reference) { toast(t('filingNeedRef')); return }
    setBusy(true)
    await ellis.updateTrip(trip.id, { govReference: reference, filedAt: Date.now() })
    await ellis.tripAgent.record(trip.id, {
      step: 'submit',
      title: 'Filed on the official portal by the traveler',
      detail: `Government reference ${reference} recorded on ${plan?.portal || 'the official portal'}. Monitoring resumes automatically.`
    })
    toast(t('filingConfirmed'))
    onResume()
  }

  const steps = [t('filingStep1'), t('filingStep2'), t('filingStep3'), t('filingStep4'), t('filingStep5')]
  return (
    <div className="card" style={{ marginTop: 14, textAlign: 'left', padding: '18px 22px', borderLeft: '3px solid var(--trip-blue, #287dfa)' }}>
      <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 4 }}>{t('filingTitle')}</div>
      <div className="trip-ops__note">{t('filingNote')}</div>

      <div className="trip-ops__row">
        <div className="trip-ops__rowmain">
          <div className="trip-ops__rowtitle">{t('filingOpenTitle')}</div>
          <div className="trip-ops__rowhint">{portalName}</div>
        </div>
        <div className="trip-ops__rowactions">
          {trip.formPath && <button className="btn btn--ghost btn--sm" onClick={() => ellis.revealFile(trip.formPath)}>{t('filingForm')}</button>}
          {portalUrl && <button className="btn btn--sm" onClick={() => ellis.openExternal(portalUrl)}>{t('filingOpen')}</button>}
        </div>
      </div>

      <ol style={{ margin: '12px 0 12px', paddingLeft: 20 }}>
        {steps.map((s, i) => <li key={i} style={{ fontSize: 12.5, marginBottom: 5, lineHeight: 1.5 }}>{s}</li>)}
      </ol>

      {fields.length > 0 && (
        <details style={{ marginBottom: 12 }}>
          <summary style={{ fontSize: 12.5, cursor: 'pointer', color: 'var(--trip-blue)', fontWeight: 600 }}>{t('filingShowData')}</summary>
          <div style={{ marginTop: 8, background: '#f7fafd', border: '1px solid #e9eef5', borderRadius: 10, padding: '10px 14px' }}>
            {fields.map((f, i) => (
              <div key={i} style={{ fontSize: 12, padding: '3px 0', display: 'flex', justifyContent: 'space-between', gap: 12 }}>
                <span style={{ color: 'var(--trip-gray)' }}>{f.label}</span><span style={{ fontWeight: 600, textAlign: 'right' }}>{f.value}</span>
              </div>
            ))}
            <button className="btn btn--ghost btn--sm" style={{ marginTop: 8 }} onClick={copyAll}>{t('filingCopy')}</button>
          </div>
        </details>
      )}

      <div className="trip-ops__row" style={{ background: '#f0f6ff' }}>
        <div className="trip-ops__rowmain">
          <div className="trip-ops__rowtitle">{t('filingRefTitle')}</div>
          <div className="trip-ops__rowhint">{t('filingRefHint')}</div>
        </div>
      </div>
      <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
        <input className="input" value={ref} onChange={(e) => setRef(e.target.value)} placeholder={t('filingRefPlaceholder')} style={{ flex: 1 }} />
        <button className="btn" disabled={busy} onClick={confirmFiled}>{busy ? <span className="spinner" /> : t('filingConfirm')}</button>
      </div>
    </div>
  )
}

/* Embassy portal access: when the filing created a portal account (ops files
   through visa.go.kr, CEAC, etc. and registers there), the credentials are
   recorded here and relayed — automatically and only — to the email on the
   application, so the traveler can watch their own application alongside the
   24/7 agent monitoring. */
function PortalAccessCard({ trip, t, toast, onChanged }) {
  const pa = trip.portalAccess || {}
  const [url, setUrl] = useState(pa.url || trip.portalResearch?.applyUrl || '')
  const [user, setUser] = useState(pa.username || '')
  const [pass, setPass] = useState(pa.password || '')
  const [busy, setBusy] = useState(false)
  const [open, setOpen] = useState(!!pa.username)

  async function save() {
    if (!user.trim()) { toast(t('portalNeedUser')); return }
    setBusy(true)
    const portalAccess = { url: url.trim(), username: user.trim(), password: pass, at: Date.now() }
    await ellis.updateTrip(trip.id, { portalAccess })
    const firstName = ((trip.name || '').trim().split(/\s+/)[0]) || 'there'
    const ref = 'TRIP-' + (String(trip.id || '').split('_').pop() || '').toUpperCase()
    const body = [
      `Hi ${firstName},`, '',
      `Your ${trip.destination} visa application is filed — and you can now watch it yourself, directly on the embassy portal:`, '',
      ...(portalAccess.url ? [`Portal: ${portalAccess.url}`] : []),
      `Login: ${portalAccess.username}`,
      ...(portalAccess.password ? [`Password: ${portalAccess.password}`] : []),
      `Application reference: ${ref}`, '',
      'Your signed application as filed is attached for your records. Our AI agent keeps monitoring the application 24/7 either way — the moment a decision is returned, you\'ll hear from us.', '',
      'Warm regards,', 'Trip.com'
    ].join('\n')
    const r = await ellis.sendTripEmail(trip.id, { subject: `${trip.destination} — track your application yourself`, body, attachmentPaths: [trip.formPath].filter(Boolean) })
    await ellis.tripAgent.record(trip.id, {
      step: 'submit',
      title: 'Embassy portal access recorded and emailed to the traveler',
      detail: `The traveler can monitor the application directly${portalAccess.url ? ` at ${portalAccess.url}` : ''} in parallel with the 24/7 agent monitoring.`
    })
    const log = [...(trip.emailLog || []), { title: `${trip.destination} portal access`, at: Date.now(), sent: r.ok ? true : r.drafted ? 'draft' : false, subject: `${trip.destination} — track your application yourself` }]
    await ellis.updateTrip(trip.id, { emailLog: log })
    setBusy(false)
    toast(t('portalSentToast'))
    onChanged()
  }

  return (
    <div className="card" style={{ marginTop: 14, textAlign: 'left', padding: '18px 22px' }}>
      <div className="eyebrow trip-eyebrow" style={{ marginBottom: 6 }}>{t('portalTitle')}</div>
      <div className="trip-ops__note">{t('portalNote')}</div>
      {pa.username && (
        <div className="trip-ops__row">
          <div className="trip-ops__rowmain">
            <div className="trip-ops__rowtitle">{pa.username}</div>
            <div className="trip-ops__rowhint">{pa.url || t('portalSaved')} · {t('portalSentHint')}</div>
          </div>
          <div className="trip-ops__rowactions">
            <button className="btn btn--ghost btn--sm" onClick={() => setOpen((v) => !v)}>{t('portalEdit')}</button>
          </div>
        </div>
      )}
      {!pa.username && !open && (
        <button className="btn btn--ghost btn--sm" onClick={() => setOpen(true)}>{t('portalAdd')}</button>
      )}
      {open && (
        <>
          <div className="trip-ops__grid" style={{ marginTop: pa.username ? 12 : 0 }}>
            <div className="field"><label style={{ fontSize: 10.5 }}>{t('portalUrlLbl')}</label>
              <input className="input" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://…" /></div>
            <div className="field"><label style={{ fontSize: 10.5 }}>{t('portalUserLbl')}</label>
              <input className="input" value={user} onChange={(e) => setUser(e.target.value)} placeholder={trip.email} /></div>
            <div className="field"><label style={{ fontSize: 10.5 }}>{t('portalPassLbl')}</label>
              <input className="input" value={pass} onChange={(e) => setPass(e.target.value)} /></div>
          </div>
          <button className="btn btn--sm" disabled={busy} onClick={save}>
            {busy ? <><span className="spinner" /> {t('portalSending')}</> : t('portalSave')}
          </button>
        </>
      )}
    </div>
  )
}

/* Signature pad: draw with mouse/trackpad, or fall back to the typed name. */
function SignaturePad({ trip, plan, t, toast, onSigned }) {
  const canvasRef = useRef(null)
  const [typed, setTyped] = useState(trip.name || '')
  const [drawn, setDrawn] = useState(false)
  const drawing = useRef(false)
  const last = useRef(null)

  const pos = (e) => {
    const r = canvasRef.current.getBoundingClientRect()
    return { x: e.clientX - r.left, y: e.clientY - r.top }
  }
  const start = (e) => { drawing.current = true; last.current = pos(e) }
  const move = (e) => {
    if (!drawing.current) return
    const ctx = canvasRef.current.getContext('2d')
    const pnow = pos(e)
    ctx.strokeStyle = '#0f294d'; ctx.lineWidth = 2; ctx.lineCap = 'round'
    ctx.beginPath(); ctx.moveTo(last.current.x, last.current.y); ctx.lineTo(pnow.x, pnow.y); ctx.stroke()
    last.current = pnow
    if (!drawn) setDrawn(true)
  }
  const end = () => { drawing.current = false }
  const clear = () => {
    const c = canvasRef.current
    c.getContext('2d').clearRect(0, 0, c.width, c.height)
    setDrawn(false)
  }

  async function sign() {
    const name = typed.trim() || trip.name
    const image = drawn ? canvasRef.current.toDataURL('image/png') : null
    const signature = { name, image, at: Date.now(), method: drawn ? 'drawn' : 'typed' }
    // Regenerate the official form with the signature embedded, then resume.
    const signedTrip = { ...trip, signature }
    // Only render the signed form when the application has been assembled —
    // at the authorization stage there is no form yet (it's embedded later).
    const official = trip.applicationForm
      ? await downloadTripOfficialFormPdfToDesktop(signedTrip, plan, trip.applicationForm)
      : { ok: false }
    await ellis.tripAgent.record(trip.id, {
      step: 'sign',
      title: `Application signed by ${name} (${signature.method})`,
      detail: 'Electronic signature captured in the Trip.com portal; embedded in the application form. Filing continues automatically.',
      artifact: official.ok ? official.path : null
    })
    await ellis.updateTrip(trip.id, { signature, formPath: official.ok ? official.path : trip.formPath })
    toast(t('signedToast'))
    onSigned()
  }

  return (
    <div className="card" style={{ marginTop: 14, textAlign: 'left', padding: '18px 20px', borderLeft: '3px solid var(--trip-blue, #287dfa)' }}>
      <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 6 }}>{t('signTitle')}</div>
      <div style={{ fontSize: 12.5, color: 'var(--trip-gray)', marginBottom: 10 }}>{t('signNote')}</div>
      {trip.formPath && (
        <button className="btn btn--ghost btn--sm" style={{ marginBottom: 12 }} onClick={() => ellis.revealFile(trip.formPath)}>
          {t('signOpenForm')}
        </button>
      )}
      <div className="field" style={{ marginBottom: 10 }}>
        <label>{t('signTyped')}</label>
        <input className="input" value={typed} onChange={(e) => setTyped(e.target.value)} />
      </div>
      <div style={{ fontSize: 11.5, color: 'var(--trip-gray)', marginBottom: 4 }}>{t('signDraw')}</div>
      <div style={{ display: 'flex', gap: 10, alignItems: 'flex-end', flexWrap: 'wrap' }}>
        <canvas ref={canvasRef} width={340} height={90}
          style={{ border: '1px dashed #b9c6da', borderRadius: 8, background: '#fff', cursor: 'crosshair', touchAction: 'none' }}
          onPointerDown={start} onPointerMove={move} onPointerUp={end} onPointerLeave={end} />
        <button className="btn btn--ghost btn--sm" onClick={clear}>{t('signClear')}</button>
      </div>
      <button className="btn" style={{ marginTop: 14 }} disabled={!typed.trim() && !drawn} onClick={sign}>{t('signBtn')}</button>
    </div>
  )
}

/* ---------------- Detail: pipeline + progress + email confirmations ---------------- */
function TripDetail({ trip, toast, t, onChanged, onBack }) {
  const [plan, setPlan] = useState(trip.plan || null)
  const [running, setRunning] = useState(false)
  const done = trip.status === 'issued' || trip.status === 'ready'
  const [stepIdx, setStepIdx] = useState(done ? 99 : -1)
  const [, setEmailLog] = useState(trip.emailLog || [])
  const [apptAt, setApptAt] = useState(trip.appointmentAt || null)
  const [decisions, setDecisions] = useState(null)
  const [refuseReason, setRefuseReason] = useState('Not specified')
  useEffect(() => { ellis.decisionsInfo(trip.id).then(setDecisions).catch(() => {}) }, [trip.id])
  const timer = useRef(null)
  const backReq = useRef(false)

  const mailState = (r) => (r.ok ? true : r.drafted ? 'draft' : false)

  useEffect(() => {
    if (!plan) ellis.planTrip({ traveler: { ...trip, documents: (trip.documents || []).map((d) => d.name) } }).then((res) => { if (res.ok) setPlan(res.data) })
    // NOTE: no clearTimeout cleanup here — an in-flight pipeline must finish
    // its awaited work and persist state even if the user navigates away
    // (setState on an unmounted component is a no-op in React 18).
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // Fresh applications created from the upload flow start processing on
  // their own — the traveler lands here and watches the checkmarks appear.
  const autoRan = useRef(false)
  useEffect(() => {
    if (plan && trip.autoStart && trip.status === 'draft' && !autoRan.current && !running) {
      autoRan.current = true
      ellis.updateTrip(trip.id, { autoStart: false })
      runAgent()
    }
  }, [plan]) // eslint-disable-line react-hooks/exhaustive-deps

  const resumeAfterSign = useRef(false)
  useEffect(() => {
    // Fires whenever the pause's artifact is persisted (signature/confirm) —
    // including after an app restart, so a signed/confirmed trip never sits
    // stuck showing its gate card.
    if (!running && plan &&
        ((trip.status === 'awaiting_signature' && trip.signature) ||
         (trip.status === 'awaiting_review' && trip.reviewConfirmed) ||
         (trip.status === 'awaiting_documents' && trip.docsReadyAt) ||
         (trip.status === 'awaiting_filing' && trip.govReference))) {
      resumeAfterSign.current = false
      runAgent()
    }
  }, [trip.signature, trip.reviewConfirmed, trip.docsReadyAt, trip.govReference, trip.status, running, plan]) // eslint-disable-line react-hooks/exhaustive-deps

  const steps = plan?.steps || []
  const pct = done || stepIdx === 99 ? 100 : running ? Math.round((stepIdx / Math.max(1, steps.length)) * 100) : (STATUS_PCT[trip.status] ?? 0) === 8 ? 0 : STATUS_PCT[trip.status] ?? 0

  // The real agent loop. Every visible step dispatches actual work through the
  // main process (OCR + verification, LLM gap review, form assembly, ICS
  // booking) and persists its result + artifacts on the trip before the
  // checkmark appears. Emails carry the generated documents as attachments.
  async function runAgent() {
    if (!plan || running) return
    setRunning(true)
    const log = [...(trip.emailLog || [])]
    try {
    const firstName = ((trip.name || '').trim().split(/\s+/)[0]) || 'there'
    let gap = trip.gapAnalysis || null
    let form = trip.applicationForm || null
    let packPath = trip.packPath || null
    let formPath = trip.formPath || null
    let appt = trip.appointment || null
    let mission = trip.mission || null
    let submission = trip.submission || null
    // Seed the milestone-email guards from persisted state so re-running the
    // pipeline (Process pressed again, or "back a step") never re-sends a
    // traveler email or re-transmits a filing.
    let emailedSubmit = !!trip.emailedSubmit
    let emailedAppt = !!trip.emailedAppt
    let riskChecked = false
    // Online e-visa / eTA: filed by the applicant on the official portal (they
    // solve the CAPTCHA + pay + submit). Excludes embassy routes (they carry
    // plan.appointment) and accredited-agency routes like Japan-for-Chinese
    // (channel === 'agency'), which a human agency files, not the traveler.
    const onlineFiling = plan.needsFiling && !plan.appointment && plan.channel !== 'agency' && (plan.kind === 'evisa' || plan.kind === 'eta')
    let assembledOnce = false
    let reviewP = null
    let missionP = null

    const pause = (ms) => new Promise((r) => { timer.current = setTimeout(r, ms) })
    const record = (entry) => ellis.tripAgent.record(trip.id, entry).catch(() => {})
    // All traveler updates go through the main process, which resolves the
    // recipient from the application record — updates can only reach the
    // email entered on the application.
    async function sendSafe(payload) {
      try {
        return await ellis.sendTripEmail(trip.id, payload)
      } catch (err) {
        console.error('sendTripEmail failed', err)
        return { ok: false, error: String(err?.message || err) }
      }
    }
    async function logEmail(title, r) {
      log.push({ title, at: Date.now(), sent: mailState(r), subject: title })
      setEmailLog([...log])
      await ellis.updateTrip(trip.id, { emailLog: log })
    }

    // A snapshot that accumulates agent results so PDFs generated mid-run use
    // the freshest extracted data (the `trip` prop lags behind persistence).
    const live = { ...trip }

    // Authorization first: one signature up front covers the whole process
    // and is reused on the official application form.
    if (plan.needsFiling && !trip.signature) {
      await ellis.updateTrip(trip.id, { status: 'awaiting_signature', statusReason: 'Waiting for the traveler to authorize Trip.com to process the visa', plan })
      await record({ step: 'sign', title: 'Authorization requested', detail: 'One signature authorizes Trip.com to prepare, sign, and file the application; it is embedded in the official form. The pipeline continues automatically after signing.' })
      onChanged()
      toast(t('signTitle'))
      return
    }
    await ellis.updateTrip(trip.id, { status: 'processing', statusReason: 'Agent pipeline started', plan })
    onChanged()

    // Nearest agency / consulate for this traveler's address — selected once,
    // reused for filing, appointment, and traveler communications.
    async function ensureMission() {
      if (mission) return mission
      const res = missionP ? await missionP : await ellis.tripAgent.mission(trip.id).catch(() => null)
      if (res?.ok) { mission = res.mission; live.mission = mission }
      return mission
    }

    function channelLabel() {
      if (plan.channel === 'agency' && mission) return `${mission.name}, ${mission.city} (MOFA-accredited agency)`
      if (mission) return `${mission.name}, ${mission.city}`
      return plan.portal || 'the official channel'
    }

    // Transmit the filing package to the configured intake endpoint. Without
    // an endpoint the package is honestly recorded as prepared, not filed.
    // When the traveler filed it themselves on the official e-visa portal
    // (online-filing handoff), that IS the real submission — record it as
    // transmitted with the government reference they entered.
    async function transmitFiling() {
      if (submission?.transmitted) return submission
      if (trip.govReference) {
        submission = { at: Date.now(), channel: channelLabel(), transmitted: true, selfFiled: true, reference: trip.govReference, ok: true, prepared: false }
        await ellis.updateTrip(trip.id, { submission })
        return submission
      }
      try {
        const res = await ellis.tripAgent.submit(trip.id, [formPath, packPath].filter(Boolean), channelLabel())
        if (res.ok) submission = res.submission
      } catch (err) { console.error('filing transmission failed', err) }
      return submission
    }

    // Online e-visa / eTA routes file on an official government portal that is
    // driven entirely by the applicant: the ONE step that must be human is the
    // CAPTCHA + payment + final submit. Ellis prepares everything and hands the
    // traveler a live checkpoint; once they record the government reference the
    // pipeline resumes to monitoring. Embassy routes (in-person appointment)
    // and endpoint-transmit routes are unaffected.
    async function awaitOnlineFiling() {
      if (trip.govReference) return true
      await ellis.updateTrip(trip.id, {
        status: 'awaiting_filing',
        statusReason: 'Ready to file on the official portal — the traveler completes the CAPTCHA and payment to submit'
      })
      await record({ step: 'submit', title: 'Ready for online filing', detail: 'The application is complete and reviewed. The traveler opens the official portal, completes the CAPTCHA and payment, submits, and records the government reference — then monitoring resumes automatically.' })
      onChanged()
      toast(t('filingTitle'))
      return false
    }

    // Final officer-style review of the COMPLETE application against refusal
    // grounds (deterministic consular rules + Kimi K3 judgment) — nothing is
    // filed while a blocking risk stands. Document-fixable risks reopen the
    // documents gate with the exact items to correct.
    async function ensureRiskCleared() {
      // Skip once already filed (self-filed on the portal, or a milestone email
      // sent) — the review runs before filing, never after.
      if (riskChecked || trip.emailedSubmit || trip.emailedAppt || trip.govReference) return true
      riskChecked = true
      const rr = await ellis.tripAgent.riskReview(trip.id).catch(() => null)
      const review = rr?.riskReview
      if (review?.verdict !== 'fix_first') return true
      const items = review.risks.filter((r) => r.severity === 'high' && r.docFix)
        .map((r) => ({ requirement: r.item.replace(/^Missing: /, ''), why: r.fix }))
      const gapNow = live.gapAnalysis || gap || trip.gapAnalysis || { covered: [], engine: 'builtin' }
      const missing = [...(gapNow.missing || [])]
      for (const it of items) if (!missing.some((m) => m.requirement === it.requirement)) missing.push(it)
      await ellis.updateTrip(trip.id, {
        gapAnalysis: { ...gapNow, missing },
        status: 'awaiting_documents',
        statusReason: 'Pre-submission review found risks that must be fixed before filing',
        docsReadyAt: null
      })
      await record({ step: 'gate', title: 'Filing held — pre-submission review requires fixes', detail: items.map((i) => i.requirement).join('; ').slice(0, 480) })
      onChanged()
      toast(t('docsGateTitle'))
      return false
    }

    async function sendSubmittedEmail() {
      if (emailedSubmit) return
      emailedSubmit = true
      await ellis.updateTrip(trip.id, { emailedSubmit: true })
      await ensureMission()
      await transmitFiling()
      const wasSent = !!submission?.transmitted
      const receipt = await downloadTripReceiptPdfToDesktop(live, plan, 'submitted')
      const subject = `${trip.destination} ${wasSent ? 'application submitted' : 'application package ready'}`
      const r1 = await sendSafe({
        subject,
        body: tripEmailSubmitted(firstName, channelLabel(), wasSent, { ...trip, ...live }, plan),
        attachmentPaths: [formPath, packPath, receipt.ok ? receipt.path : null].filter(Boolean)
      })
      if (!r1.ok && !r1.drafted) await ellis.updateTrip(trip.id, { emailedSubmit: false })
      await logEmail(subject, r1)
      await ellis.updateTrip(trip.id, {
        status: wasSent ? 'submitted' : 'prepared',
        statusReason: wasSent ? `Filing package transmitted to ${channelLabel()}` : 'Filing package prepared — no intake endpoint configured',
        emailedSubmit: true
      })
      onChanged()
    }

    async function bookAndEmailAppointment() {
      if (emailedAppt) return
      emailedAppt = true
      await ellis.updateTrip(trip.id, { emailedAppt: true })
      await ensureMission()
      await transmitFiling()
      const res = await ellis.tripAgent.appointment(trip.id)
      if (res.ok) { appt = res.appointment; setApptAt(appt.at) }
      const where = appt?.where || (mission ? `${mission.name}, ${mission.address}` : `${trip.destination} consulate`)
      const when = appt ? new Date(appt.at).toLocaleString([], { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit' }) : '—'
      // Official consular-styled appointment confirmation — the document the
      // traveler presents at the mission (re-attached to the approval email).
      const notice = await downloadAppointmentNoticePdfToDesktop(live, plan, appt, mission)
      if (notice.ok) {
        await ellis.updateTrip(trip.id, { apptNoticePath: notice.path })
        await record({ step: 'appointment', title: 'Official appointment confirmation issued', artifact: notice.path })
      }
      const rAppt = await sendSafe({
        subject: `${trip.destination} appointment`,
        body: tripEmailAppointment(firstName, when, where, !!submission?.transmitted, { ...trip, ...live }, plan),
        attachmentPaths: [notice.ok ? notice.path : null, appt?.icsPath, formPath, packPath].filter(Boolean)
      })
      if (!rAppt.ok && !rAppt.drafted) await ellis.updateTrip(trip.id, { emailedAppt: false })
      await logEmail(`${trip.destination} appointment`, rAppt)
      const wasSent = !!submission?.transmitted
      await ellis.updateTrip(trip.id, {
        status: wasSent ? 'submitted' : 'prepared',
        statusReason: wasSent ? 'Filing transmitted; appointment booked' : 'Package prepared; appointment booked',
        emailedAppt: true
      })
      onChanged()
    }

    for (let i = 0; i < steps.length; i++) {
      setStepIdx(i)
      const s = steps[i]
      await pause(120)
      if (backReq.current) {
        backReq.current = false
        i = Math.max(-1, i - 2)
        setStepIdx(Math.max(0, i))
        continue
      }
      if (/^verify/i.test(s)) {
        const res = await ellis.tripAgent.ingest(trip.id)
        if (res.ok) {
          if (res.passport) live.passport = res.passport
          live.docChecks = res.checks
          live.flaggedDocs = res.flaggedDocs || []
          // Hard gate: never file with a wrong, mismatched, or invalid
          // passport. Failed critical checks stop the pipeline here.
          const critical = (res.checks || []).filter((c) => !c.ok && ['expiry', 'nationality', 'name'].includes(c.id))
          // A missing/unreadable passport only hard-blocks routes that FILE an
          // application; visa-free routes proceed with a manual-verify note.
          const noPassport = !res.passport && plan.needsFiling
          if (!res.passport && !plan.needsFiling) {
            await record({ step: 'gate', title: 'Passport not machine-read — manual verification', detail: 'No MRZ could be read. This visa-free route continues; border control verifies the physical passport.' })
          }
          if (!noPassport && !critical.length) {
            // Verification passed — start the slow externals concurrently so
            // the LLM review, portal research, and mission selection overlap
            // with form assembly instead of running back-to-back.
            reviewP = ellis.tripAgent.review(trip.id).catch(() => null)
            missionP = ellis.tripAgent.mission(trip.id).catch(() => null)
            if (plan.needsFiling && !trip.portalResearch) ellis.tripAgent.research(trip.id).catch(() => {})
          }
          if (noPassport || critical.length) {
            const reasons = noPassport
              ? 'No machine-readable passport found among the uploads.'
              : critical.map((c) => c.detail).join(' ')
            await ellis.updateTrip(trip.id, { status: 'action_required', statusReason: reasons })
            await record({ step: 'gate', title: 'Verification failed — filing blocked', detail: reasons })
            onChanged()
            toast(t('gateTitle'))
            return
          }
        }
      } else if (/classify|confirm visa-free|match jurisdiction/i.test(s)) {
        if (!reviewP) reviewP = ellis.tripAgent.review(trip.id).catch(() => null)
      } else if (/fill|assemble|complete thailand digital|attach trip\.com/i.test(s)) {
        // Runs for filing routes AND visa-free routes (the "Attach Trip.com
        // bookings" step) so every trip gets a real document package to
        // deliver, not an email that implies attachments it doesn't have.
        if (assembledOnce) { onChanged(); continue }
        assembledOnce = true
        const rev = reviewP ? await reviewP : null
        if (rev?.ok) { gap = rev.gap; live.gapAnalysis = gap }
        // Documents gate: a filing route needs every requirement satisfied by
        // a VERIFIED document before the application goes anywhere. Pause and
        // ask the traveler to upload what's missing — or replace a file that
        // failed Kimi K3's document verification. There is no waiver: a
        // filing route cannot proceed until the package is complete and every
        // document passed verification.
        const flaggedNow = live.flaggedDocs !== undefined
          ? live.flaggedDocs.map((f) => ({ name: f.name, docCheck: { issues: f.issues, plausible: false } }))
          : (trip.documents || []).filter((d) => d.docCheck?.plausible === false && !d.docCheck?.overridden)
        const neededNow = (gap?.missing || [])
        if (plan.needsFiling && (neededNow.length || flaggedNow.length)) {
          await ellis.updateTrip(trip.id, {
            status: 'awaiting_documents',
            statusReason: `${neededNow.length + flaggedNow.length} document item(s) needed before the application can be filed`,
            gapAnalysis: gap || trip.gapAnalysis,
            docsReadyAt: null // cleared so the next upload (not this pause) triggers the resume
          })
          await record({
            step: 'gate', title: 'Documents needed before filing',
            detail: [
              neededNow.length ? `Still needed: ${neededNow.map((m) => m.requirement).join('; ')}` : '',
              flaggedNow.length ? `Failed verification: ${flaggedNow.map((d) => `${d.name} (${(d.docCheck?.issues || []).join(', ') || 'not usable'})`).join('; ')}` : ''
            ].filter(Boolean).join(' · ')
          })
          onChanged()
          toast(t('docsGateTitle'))
          return
        }
        // On resume after the traveler confirmed their review, keep THEIR
        // corrected application — never re-assemble over their edits.
        const res = trip.reviewConfirmed && trip.applicationForm
          ? { ok: true, form: trip.applicationForm }
          : await ellis.tripAgent.assemble(trip.id)
        if (res.ok) {
          form = res.form
          live.applicationForm = form
          live.signature = trip.signature || live.signature
          // The reviewed-and-confirmed form PDF already exists (built by the
          // review step); only generate it here on the first assembly.
          const official = trip.reviewConfirmed && trip.formPath
            ? { ok: true, path: trip.formPath }
            : await downloadTripOfficialFormPdfToDesktop(live, plan, form)
          if (official.ok) {
            formPath = official.path
            await ellis.updateTrip(trip.id, { formPath })
            await record({ step: 'assemble', title: `${form.form} filled out`, detail: `Official form completed from machine-read passport data (${form.completeness}%).`, artifact: official.path })
          }
          const pack = await downloadTripApplicationPackPdfToDesktop(live, plan, form, gap)
          if (pack.ok) {
            packPath = pack.path
            await ellis.updateTrip(trip.id, { packPath })
            await record({ step: 'assemble', title: 'Supporting document package generated', detail: `Verification results, requirement checklist, and documents on file.`, artifact: pack.path })
          }
          // (fallthrough guarded below)
          // Review-and-confirm gate: for filing routes, the traveler reviews
          // the completed official application, corrects any field, and
          // confirms submission BEFORE anything is filed with the embassy.
          if (plan.needsFiling && !trip.reviewConfirmed) {
            await ellis.updateTrip(trip.id, { status: 'awaiting_review', statusReason: 'Application filled — waiting for the traveler to review and confirm before submission', formPath, packPath })
            await record({ step: 'review', title: 'Application ready for your review', detail: 'The completed official application is ready. The traveler reviews every field, corrects anything, and confirms submission before it is filed.' })
            onChanged()
            toast(t('reviewTitle'))
            return
          }
        } else if (plan.needsFiling) {
          // Assembly failed on a filing route — never proceed to submission
          // without a form.
          await ellis.updateTrip(trip.id, { status: 'action_required', statusReason: 'The application could not be assembled from the documents — check the uploads and process again.' })
          await record({ step: 'gate', title: 'Assembly failed — filing blocked', detail: 'The application form could not be assembled. Fix the documents and process again.' })
          onChanged()
          return
        }
      } else if (/submit/i.test(s)) {
        if (plan.needsFiling && !plan.appointment) {
          if (!await ensureRiskCleared()) return
          if (onlineFiling && !await awaitOnlineFiling()) return
          await sendSubmittedEmail()
        }
      } else if (/appointment/i.test(s)) {
        if (plan.appointment) {
          if (plan.needsFiling && !await ensureRiskCleared()) return
          await bookAndEmailAppointment()
        }
      } else if (/monitor/i.test(s)) {
        if (submission?.transmitted) {
          await ellis.updateTrip(trip.id, { status: 'monitoring', statusReason: plan.channel === 'agency' ? 'Agency filed with the consulate — awaiting decision' : 'Awaiting decision from the authority' })
        }
        await pause(200)
      }
      onChanged()
    }
    // Safety nets: milestones must fire even if a step label changed — but
    // never around the pre-submission risk review.
    if (plan.appointment && !emailedAppt) {
      if (plan.needsFiling && !await ensureRiskCleared()) return
      await bookAndEmailAppointment()
    }
    if (!plan.appointment && plan.needsFiling && !emailedSubmit) {
      if (!await ensureRiskCleared()) return
      if (onlineFiling && !await awaitOnlineFiling()) return
      await sendSubmittedEmail()
    }

    if (!plan.needsFiling) {
      // Visa-free routes: Ellis genuinely completes the work — entry brief and
      // arrival-registration pack are the real deliverables.
      await pause(300)
      const sys = ARRIVAL_SYSTEMS[trip.destination]
      const present = arrivalPresentList(trip, plan, sys)
      const finalBody = tripEmailFinal(firstName, plan, present)
      let arrivalPassPath = null
      let r2 = { ok: false }
      try {
        if (sys) {
          const pass = await downloadArrivalPassPdfToDesktop(live, sys)
          arrivalPassPath = pass.ok ? pass.path : null
          if (arrivalPassPath) await record({ step: 'deliver', title: `${sys.name} arrival pack generated`, artifact: arrivalPassPath })
        }
        const attachments = [arrivalPassPath, packPath, formPath].filter(Boolean)
        r2 = await sendSafe({ subject: `${trip.destination} — you're ready to travel`, body: finalBody, attachmentPaths: attachments })
        if (!r2.ok && !r2.drafted) {
          await pause(2000)
          r2 = await sendSafe({ subject: `${trip.destination} — you're ready to travel`, body: finalBody, attachmentPaths: attachments })
        }
      } catch (err) { console.error('final email failed', err) }
      log.push({ title: `${trip.destination} ready to travel`, at: Date.now(), final: true, sent: mailState(r2), subject: `${trip.destination} — you're ready to travel` })
      setEmailLog([...log])
      await ellis.updateTrip(trip.id, { status: 'ready', statusReason: plan.kind === 'voa' ? 'Entry pack delivered — the visa is issued on arrival at the border' : 'Entry pack delivered — no visa required for this route', arrivalPassPath, emailSentAt: Date.now(), plan, emailLog: log })
      await record({ step: 'deliver', title: `Entry pack delivered to ${trip.email}`, detail: r2.ok ? 'Email sent with attachments.' : r2.drafted ? 'Draft opened in Mail.' : 'Email delivery failed — documents remain on the Desktop.' })
      await ellis.addNotif({ forRole: 'tripcom', fromRole: 'tripcom', caseId: trip.id, caseName: trip.name, title: `Entry pack delivered to ${trip.email}` })
      setStepIdx(99)
      onChanged()
      toast(r2.ok ? `${t('emailedTo')} ${trip.email}` : r2.drafted ? t('draftedLbl') : t('mailFail'))
    } else {
      // Visa-required routes stop honestly at the decision boundary: the
      // application is filed (or prepared) and monitored. Issuance happens
      // only when the authority's decision is recorded below.
      setStepIdx(steps.length)
      onChanged()
      toast(t('monitorNote'))
    }
    } finally {
      setRunning(false)
    }
  }


  // Real remediation for a blocked application: replace the wrong/expired
  // passport and reset the pipeline for a fresh verification run.
  async function replacePassport() {
    const picked = await ellis.pickDocuments()
    if (!picked.length) return
    const newDocs = picked.map((d) => ({ name: d.name, path: d.path || null, kind: d.kind || 'text', text: (d.text || '').slice(0, 4000), extracted: null, mrzFound: false }))
    const kept = (trip.documents || []).filter((d) => !(d.mrzFound || /passport/i.test(d.name)))
    await ellis.updateTrip(trip.id, { documents: [...kept, ...newDocs], passport: null, docChecks: null, status: 'draft', statusReason: 'Corrected documents uploaded' })
    await ellis.tripAgent.record(trip.id, { step: 'gate', title: 'Corrected passport uploaded — ready to re-run processing' })
    setStepIdx(-1)
    onChanged()
  }

  // Escape hatch from "prepared": once the org configures a filing intake
  // endpoint, this transmits the already-assembled package without re-running
  // the pipeline (and without re-emailing the traveler).
  async function transmitNow() {
    const channel = trip.mission ? `${trip.mission.name}, ${trip.mission.city}` : (plan?.portal || 'the filing channel')
    const res = await ellis.tripAgent.submit(trip.id, [trip.formPath, trip.packPath].filter(Boolean), channel)
    if (res?.submission?.transmitted) {
      await ellis.updateTrip(trip.id, { status: 'submitted', statusReason: `Filing package transmitted to ${channel}` })
      toast(t('transmittedToast'))
    } else {
      toast(res?.submission?.prepared ? 'Configure the filing intake address in Settings first' : (res?.error || 'Transmission failed'))
    }
    onChanged()
  }

  // The authority's decision is the only path to "issued" — recorded here by
  // staff when the consulate/agency responds, with the real visa attached.
  async function recordDecision(approved) {
    const firstName = ((trip.name || '').trim().split(/\s+/)[0]) || 'there'
    if (approved) {
      const picked = await ellis.pickDocuments()
      const attach = picked[0]?.path || null
      if (attach) {
        await ellis.tripAgent.record(trip.id, { step: 'decision', title: 'Issued visa document attached', artifact: attach })
      }
      // issueTrip renders the full visa grant notice main-side and attaches it
      // together with the authority's document and the signed application.
      await ellis.issueTrip(trip.id, attach, 'Authority decision recorded: approved')
      setStepIdx(99)
      toast(t('approvedToast'))
    } else {
      // Build the fix plan first so the refusal email carries the remedy.
      const rem = await ellis.tripAgent.remedy(trip.id, refuseReason)
      const steps = rem?.remediation?.steps || []
      const body = [
        `Hi ${firstName},`, '',
        `We're sorry — the ${trip.destination} authority did not approve this application${refuseReason !== 'Not specified' ? ` (reason: ${refuseReason.toLowerCase()})` : ''}.`, '',
        'Here is how we fix it together:', '',
        ...steps.map((x, i) => `${i + 1}. ${x}`), '',
        'Reply to this email or reopen your application on Trip.com and tap "Fix & reapply" — your verified documents and signature are reused, so the corrected application takes minutes.', '',
        'Any refundable fees return to your original payment method.', '',
        'Warm regards,', 'Trip.com'
      ].join('\n')
      const r = await ellis.sendTripEmail(trip.id, { subject: `${trip.destination} visa decision`, body })
      const log = [...(trip.emailLog || []), { title: `${trip.destination} decision: refused`, at: Date.now(), sent: mailState(r), subject: `${trip.destination} visa decision` }]
      await ellis.updateTrip(trip.id, { status: 'refused', statusReason: 'Authority decision recorded: refused', emailLog: log })
      await ellis.tripAgent.record(trip.id, { step: 'decision', title: 'Refusal recorded — traveler notified' })
      toast(t('refusedToast'))
    }
    onChanged()
  }

  const isDone = done || stepIdx === 99

  // The pipeline is summarized as five human tasks; each maps onto the
  // underlying agent steps so checkmarks appear as the agent works.
  const idxOf = (re) => steps.findIndex((s) => re.test(s))
  const passedStep = (idx) => isDone || (idx >= 0 && stepIdx > idx)
  const portalRaw = plan?.portal || ''
  const formMatch = (portalRaw.match(/\(([^)]+)\)/) || [])[1]
  const formLooksLikeCode = formMatch && (/^(DS-|I-|IMM|Form)/i.test(formMatch) || /^[A-Z][A-Z0-9-]{1,14}$/.test(formMatch))
  const formName = cleanVisaName(formLooksLikeCode ? formMatch : (plan?.headline || `${trip.destination} visa`))
  const portalName = portalDisplayName(plan, trip.destination)
  const hasAppt = plan ? !!plan.appointment : false
  const apptStr = apptAt
    ? new Date(apptAt).toLocaleDateString([], { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' })
    : null
  const tasks = [
    { label: t('taskDocs'), done: passedStep(idxOf(/verify|extract/i)) },
    { label: `${t('taskForms')} — ${formName}`, done: passedStep(idxOf(/fill|attach/i)) },
    { label: plan?.needsFiling ? `${trip.submission && !trip.submission.transmitted ? t('taskPrepared') : t('taskSubmit')} ${portalName}` : t('taskEntry'), done: passedStep(idxOf(/submit|deliver/i)) },
    ...(hasAppt ? [{ label: apptStr ? `${t('taskAppt')} ${apptStr}` : t('taskApptPending'), done: !!apptAt }] : []),
    { label: t('taskDone'), done: isDone }
  ]
  const activeIdx = running ? tasks.findIndex((x) => !x.done) : -1

  return (
    <div className="trip-newwrap">
      <button className="trip-back" onClick={onBack} title={t('allApps')} aria-label={t('allApps')}>
        <Icon.back style={{ width: 17, height: 17 }} />
      </button>

      <div className="trip-detail">
        <div className="trip-detail__route">
          <span className="trip-detail__flag">{flag(trip.nationality)}</span>
          <Icon.arrow style={{ width: 26, height: 26, color: 'var(--trip-blue)' }} />
          <span className="trip-detail__flag">{flag(trip.destination)}</span>
        </div>
        <div className="trip-detail__name">{trip.name}</div>
        <div className="trip-detail__dates">{fmtTravelDate(trip.departure)}{trip.return ? `  →  ${fmtTravelDate(trip.return)}` : ''}</div>
        <div className="trip-progress" style={{ margin: '18px 0 26px' }}><div className="trip-progress__fill" style={{ width: `${pct}%` }} /></div>

        <div className="card trip-tasks">
          {tasks.map((x, i) => {
            const state = x.done ? 'done' : i === activeIdx ? 'active' : 'todo'
            return (
              <div key={i} className={`trip-task trip-task--${state}`}>
                <div className="trip-task__dot">
                  {state === 'done' ? <Icon.check style={{ width: 13, height: 13 }} /> : state === 'active' ? <span className="spinner" style={{ borderTopColor: '#fff' }} /> : null}
                </div>
                <div className="trip-task__txt">{x.label}</div>
              </div>
            )
          })}
          {!isDone && (
            <div style={{ display: 'flex', gap: 10, marginTop: 20, justifyContent: 'center' }}>
              {running && stepIdx > 0 && (
                <button className="btn btn--ghost" onClick={() => { backReq.current = true }}>
                  <Icon.back style={{ width: 14, height: 14 }} /> {t('backStep')}
                </button>
              )}
              <button className="btn" style={{ minWidth: 240 }} onClick={runAgent} disabled={running || !plan || ['prepared', 'submitted', 'monitoring'].includes(trip.status)}>
                {running ? <><span className="spinner" /> {t('working')}</> : t('processBtn')}
              </button>
            </div>
          )}
        </div>

        {trip.status === 'action_required' && !running && (
          <div className="card" style={{ marginTop: 14, textAlign: 'left', padding: '16px 20px', borderLeft: '3px solid #c96b00' }}>
            <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 6 }}>{t('gateTitle')}</div>
            <div style={{ fontSize: 12.5, color: 'var(--trip-gray)', marginBottom: 8 }}>{t('gateNote')}</div>
            {(trip.docChecks || []).filter((c) => !c.ok).map((c, i) => (
              <div key={i} style={{ fontSize: 12.5, margin: '4px 0' }}><b>{c.label}:</b> {c.detail}</div>
            ))}
            {!(trip.docChecks || []).some((c) => !c.ok) && trip.statusReason && (
              <div style={{ fontSize: 12.5, margin: '4px 0' }}>{trip.statusReason}</div>
            )}
            <div style={{ display: 'flex', gap: 10, marginTop: 10, flexWrap: 'wrap' }}>
              {trip.passport?.fullName && (trip.docChecks || []).some((c) => c.id === 'name' && !c.ok) && (
                <button className="btn btn--sm" onClick={async () => {
                  const fixed = trip.passport.fullName.replace(/\b\w/g, (c) => c.toUpperCase()).replace(/(?<=\w)\w+/g, (w) => w.toLowerCase())
                  await ellis.updateTrip(trip.id, { name: fixed, status: 'draft', statusReason: 'Name corrected to match the passport' })
                  await ellis.tripAgent.record(trip.id, { step: 'gate', title: `Name corrected to "${fixed}" to match the passport — ready to re-run` })
                  setStepIdx(-1)
                  onChanged()
                }}>{t('usePassportName')} — {trip.passport.fullName}</button>
              )}
              <button className="btn btn--ghost btn--sm" onClick={replacePassport}>{t('gateFix')}</button>
            </div>
          </div>
        )}

        {trip.status === 'awaiting_signature' && !running && (
          <SignaturePad trip={trip} plan={plan} t={t} toast={toast}
            onSigned={() => { resumeAfterSign.current = true; onChanged() }} />
        )}

        {trip.status === 'awaiting_review' && !running && (
          <ReviewCard trip={trip} plan={plan} t={t} toast={toast}
            onConfirmed={() => { resumeAfterSign.current = true; onChanged() }} />
        )}

        {trip.status === 'awaiting_documents' && !running && (
          <DocumentsCard trip={trip} t={t} toast={toast}
            onResume={() => { resumeAfterSign.current = true; onChanged() }} />
        )}

        {trip.status === 'awaiting_filing' && !running && (
          <AssistedFilingCard trip={trip} plan={plan} t={t} toast={toast}
            onResume={() => { resumeAfterSign.current = true; onChanged() }} />
        )}

        {plan?.needsFiling && ['prepared', 'submitted', 'monitoring'].includes(trip.status) && !running && (
          <PortalAccessCard trip={trip} t={t} toast={toast} onChanged={onChanged} />
        )}

        {plan?.needsFiling && ['prepared', 'submitted', 'monitoring'].includes(trip.status) && !running && (
          <div className="card" style={{ marginTop: 14, textAlign: 'left', padding: '18px 22px' }}>
            <div className="eyebrow trip-eyebrow" style={{ marginBottom: 6 }}>{t('decisionTitle')}</div>
            <div className="trip-ops__note">{t('decisionNote')}</div>

            {decisions && (
              <div className="trip-ops__row">
                <div className="trip-ops__rowmain">
                  <div className="trip-ops__rowtitle">{t('autoTitle')}</div>
                  <div className="trip-ops__rowhint">{t('autoNote')} <b>{decisions.ref}</b></div>
                </div>
                <div className="trip-ops__rowactions">
                  <button className="btn btn--ghost btn--sm" onClick={() => ellis.openDecisionsDir()}>{t('openFolder')}</button>
                </div>
              </div>
            )}

            {trip.status === 'prepared' && (
              <div className="trip-ops__row">
                <div className="trip-ops__rowmain">
                  <div className="trip-ops__rowtitle">{t('transmitTitle')}</div>
                  <div className="trip-ops__rowhint">{t('transmitHint')}</div>
                </div>
                <div className="trip-ops__rowactions">
                  <button className="btn btn--sm" onClick={() => transmitNow()}>{t('transmitNow')}</button>
                </div>
              </div>
            )}

            <div className="trip-ops__row">
              <div className="trip-ops__rowmain">
                <div className="trip-ops__rowtitle">{t('approveTitle')}</div>
                <div className="trip-ops__rowhint">{t('approveHint')}</div>
              </div>
              <div className="trip-ops__rowactions">
                <button className="btn btn--sm" onClick={() => recordDecision(true)}>{t('approveBtn')}</button>
              </div>
            </div>

            <div className="trip-ops__row">
              <div className="trip-ops__rowmain">
                <div className="trip-ops__rowtitle">{t('refuseTitle')}</div>
                <div className="trip-ops__rowhint">{t('refuseHint')}</div>
              </div>
              <div className="trip-ops__rowactions">
                <select className="select" value={refuseReason} onChange={(e) => setRefuseReason(e.target.value)} aria-label={t('refuseReason')}>
                  {['Not specified', 'Expired passport', 'Insufficient funds', 'Incomplete documents', 'Purpose of visit doubts', 'Prior overstay or refusal'].map((r) => <option key={r}>{r}</option>)}
                </select>
                <button className="btn btn--ghost btn--sm" onClick={() => recordDecision(false)}>{t('refuseBtn')}</button>
              </div>
            </div>
          </div>
        )}

        {trip.status === 'refused' && (
          <div className="card" style={{ marginTop: 14, textAlign: 'left', padding: '16px 20px', borderLeft: '3px solid #c0392b' }}>
            <div className="eyebrow trip-eyebrow" style={{ marginBottom: 6 }}>{t('fixPlanTitle')}</div>
            {(trip.remediation?.steps || []).map((x, i) => (
              <div key={i} style={{ fontSize: 12.5, margin: '4px 0' }}>{i + 1}. {x}</div>
            ))}
            <button className="btn btn--sm" style={{ marginTop: 10 }} onClick={async () => {
              const clone = await ellis.createTrip({
                name: trip.name, email: trip.email, address: trip.address || '',
                nationality: trip.nationality, destination: trip.destination,
                departure: trip.departure, return: trip.return,
                documents: trip.documents || [], passport: trip.passport || null,
                signature: trip.signature || null,
                reappliedFrom: trip.id, autoStart: false, status: 'draft', emailLog: []
              })
              await ellis.tripAgent.record(trip.id, { step: 'remedy', title: 'Corrected reapplication created', detail: `New application ${clone.id} reuses the verified documents and signature.` })
              await ellis.tripAgent.record(clone.id, { step: 'remedy', title: `Reapplication of refused case (${trip.destination})`, detail: `Carries the fix plan${trip.remediation?.reason ? ` for: ${trip.remediation.reason}` : ''}. Address the fix items, then process.` })
              toast(t('reapplyToast'))
              onBack()
            }}>{t('reapplyBtn')}</button>
          </div>
        )}

        {(trip.portalResearch?.applyUrl || plan?.portalUrl) && (
          <div style={{ marginTop: 12 }}>
            <button className="btn btn--ghost btn--sm" onClick={() => ellis.openExternal(trip.portalResearch?.applyUrl || plan.portalUrl)}>
              <Icon.globe style={{ width: 14, height: 14 }} /> {(() => {
                if (trip.portalResearch?.applyUrl) { try { return new URL(trip.portalResearch.applyUrl).hostname.replace(/^www\./, '') } catch { /* fall through */ } }
                return portalDisplayName(plan, trip.destination)
              })()}
            </button>
          </div>
        )}

        {isDone && (
          <div className="trip-complete">
            <img src={visaCompleteImg} alt="" className="trip-complete__img" />
            <div className="trip-complete__title">{t('visaComplete')}</div>
            <div className="trip-complete__sub">{t('confirmSent')}</div>
          </div>
        )}

        {(trip.agentLog || []).length > 0 && (
          <div className="card" style={{ marginTop: 16, textAlign: 'left', padding: '16px 20px' }}>
            <div className="eyebrow trip-eyebrow" style={{ marginBottom: 8 }}>{t('agentRecord')}</div>
            {(() => {
              const logAll = trip.agentLog || []
              const lastCheck = [...logAll].reverse().find((e) => e.step === 'check')
              const filtered = logAll.filter((e) => e.step !== 'check' || e === lastCheck)
              return filtered.slice(-12)
            })().map((e, i) => (
              <div key={i} style={{ display: 'flex', gap: 10, alignItems: 'baseline', padding: '7px 0', borderBottom: i === Math.min((trip.agentLog || []).length, 12) - 1 ? 'none' : '1px solid var(--line, #eef2f8)' }}>
                <span style={{ fontSize: 11, color: 'var(--trip-gray)', flexShrink: 0, width: 62 }}>
                  {new Date(e.at).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}
                </span>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 13, fontWeight: 600 }}>{e.title}</div>
                  {e.detail && <div style={{ fontSize: 12, color: 'var(--trip-gray)', lineHeight: 1.45 }}>{e.detail}</div>}
                </div>
                {e.artifact && (
                  <button className="btn btn--ghost btn--sm" style={{ flexShrink: 0 }} onClick={() => ellis.revealFile(e.artifact)}>
                    {t('openFile')}
                  </button>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

/* ---------------- Floating support chatbox (every page) ---------------- */
function TripChat({ trip, plan, t }) {
  const stage = trip ? (t('status')[trip.status] || trip.status) : ''
  // Spoken, natural sentence for the greeting — never the raw status label.
  const phrase = trip ? (t('statusPhrase')[trip.status] || t('statusPhrase').processing) : ''
  const firstName = trip ? ((trip.name || '').trim().split(/\s+/)[0] || 'traveler') : ''
  const hello = trip
    ? (trip.status === 'issued' ? t('chatHelloDone') : t('chatHello')).replace('{name}', firstName).replace('{phrase}', phrase)
    : t('chatHelloGeneric')
  const [msgs, setMsgs] = useState([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const scrollRef = useRef(null)

  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [msgs, busy])

  async function send() {
    const msg = input.trim()
    if (!msg || busy) return
    setInput('')
    const next = [...msgs, { role: 'user', content: msg }]
    setMsgs(next)
    setBusy(true)
    // The trip file is injected as leading context so every answer is about
    // THIS traveler's route, status, and documents.
    const gapMissing = (trip?.gapAnalysis?.missing || []).map((m) => m.requirement).join('; ')
    const ctx = trip
      ? `CONTEXT (Trip.com tourist-visa case): Traveler ${trip.name}, nationality ${trip.nationality}, destination ${trip.destination}, travel ${trip.departure || '?'} to ${trip.return || '?'}. Route: ${plan?.headline || 'pending classification'} (${plan?.classification || '?'}). Fee ${plan?.fee || '?'}, processing ${plan?.processing || '?'}. Status in plain words: ${phrase} (internal stage label: ${stage}) — when telling the traveler their status, phrase it naturally like that, never as "at ${stage}". Documents on file: ${(trip.documents || []).map((d) => d.name).join(', ') || 'none'}.${trip.passport?.passportNumber ? ` Passport on file: ${trip.passport.passportNumber}, expires ${trip.passport.expiryDate || '?'}.` : ''}${gapMissing ? ` Documents still needed: ${gapMissing}.` : trip.gapAnalysis ? ' All required documents are covered.' : ''}${trip.appointmentAt ? ` Interview appointment: ${new Date(trip.appointmentAt).toLocaleString()}${trip.appointment?.where ? ` at ${trip.appointment.where}` : ''}.` : ''}`
      : 'CONTEXT: General Trip.com tourist-visa support chat — the traveler has not opened a specific application.'
    const history = [
      { role: 'user', content: ctx },
      { role: 'assistant', content: 'Understood — I have the context.' },
      ...next.slice(-7, -1)
    ]
    const res = await ellis.ai.assistantChat({ role: 'tripcom', message: msg, history })
    setBusy(false)
    setMsgs((m) => [...m, { role: 'assistant', content: res.ok ? res.data.reply : (res.error || 'Something went wrong — please try again.') }])
  }

  return (
    <div className="trip-chatfloat">
      <div className="eyebrow trip-eyebrow" style={{ padding: '16px 20px 8px' }}>{t('support')}</div>
      <div ref={scrollRef} className="trip-chat__scroll">
        <div className="trip-chat__msg trip-chat__msg--ai">{hello}</div>
        {msgs.map((m, i) => (
          <div key={i} className={`trip-chat__msg ${m.role === 'user' ? 'trip-chat__msg--user' : 'trip-chat__msg--ai'}`}>{m.content}</div>
        ))}
        {busy && <div className="trip-chat__msg trip-chat__msg--ai"><span className="spinner spinner--ink" /></div>}
      </div>
      <div className="trip-chat__inputrow">
        <input className="input" value={input} placeholder={t('chatPlaceholder')}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') send() }} />
        <button className="btn" onClick={send} disabled={busy || !input.trim()}><Icon.arrow style={{ width: 15, height: 15 }} /></button>
      </div>
    </div>
  )
}
