// Static, maintained localization for core product/legal UI (Phase 6). Dynamic
// content (official requirement text, portal instructions, emails) is translated
// on the BACKEND via Kimi K3 (see backend/app/i18n.py) — never here, and never
// with a provider key in the client. Core UI strings are static so legal and
// product wording is reviewable and stable across releases.

export const SUPPORTED = ['en', 'zh-CN', 'zh-Hant']
export const LANGUAGE_NAMES = { en: 'English', 'zh-CN': '简体中文', 'zh-Hant': '繁體中文' }
export const DEFAULT_LANG = 'en'

// Every locale MUST define the same keys as `en` (enforced by a test).
export const STRINGS = {
  en: {
    'section.workspace': 'Workspace',
    'section.account': 'Account',
    'nav.dashboard': 'Home',
    'nav.cases': 'Cases',
    'nav.visa': 'Visa Platform',
    'nav.admin': 'Adapter Admin',
    'nav.setup': 'Setup',
    'nav.assistant': 'Ask Ellis',
    'nav.notifications': 'Notifications',
    'nav.settings': 'Settings',
    'action.tour': 'Take a tour',
    'action.switchRole': 'Switch role',
    'action.continue': 'Continue',
    'action.start': 'Start application',
    'action.approve': 'Approve & continue',
    'common.language': 'Language',
    'common.loading': 'Loading…',
    'visa.title': 'Visa Platform',
    'visa.newCase': 'New case',
    'visa.documents': 'Documents',
    'visa.preferences': 'Preferences',
    'visa.activity': 'Activity',
    'visa.journey': 'Journey',
    'visa.readyTitle': 'Ready to submit?',
    'ocr.dropTitle': 'Drop your passport & supporting documents',
    'ocr.wrongPageRetry': 'Upload the biodata page and try again',
    'exec.notReal': 'Not a real government submission',
    'exec.mockDisclaimer': 'This case ran on a non-production portal. Nothing was really submitted, paid, or booked with any government.',
    'result.submittedReal': 'Application submitted',
    'result.mockReference': 'Mock reference (not a real visa reference)',
    'assistant.identity': "I'm Ellis, your visa-application assistant."
  },
  'zh-CN': {
    'section.workspace': '工作区',
    'section.account': '账户',
    'nav.dashboard': '主页',
    'nav.cases': '案件',
    'nav.visa': '签证平台',
    'nav.admin': '适配器管理',
    'nav.setup': '设置向导',
    'nav.assistant': '询问 Ellis',
    'nav.notifications': '通知',
    'nav.settings': '设置',
    'action.tour': '开始导览',
    'action.switchRole': '切换角色',
    'action.continue': '继续',
    'action.start': '开始申请',
    'action.approve': '批准并继续',
    'common.language': '语言',
    'common.loading': '加载中…',
    'visa.title': '签证平台',
    'visa.newCase': '新建案件',
    'visa.documents': '文件',
    'visa.preferences': '偏好设置',
    'visa.activity': '活动记录',
    'visa.journey': '流程',
    'visa.readyTitle': '准备好提交了吗？',
    'ocr.dropTitle': '拖入您的护照和辅助文件',
    'ocr.wrongPageRetry': '请上传资料页并重试',
    'exec.notReal': '非真实的政府提交',
    'exec.mockDisclaimer': '此案件在非生产门户上运行。没有向任何政府真正提交、付款或预约。',
    'result.submittedReal': '申请已提交',
    'result.mockReference': '模拟参考号（非真实签证参考号）',
    'assistant.identity': '我是 Ellis，您的签证申请助手。'
  },
  'zh-Hant': {
    'section.workspace': '工作區',
    'section.account': '帳戶',
    'nav.dashboard': '主頁',
    'nav.cases': '案件',
    'nav.visa': '簽證平台',
    'nav.admin': '適配器管理',
    'nav.setup': '設定精靈',
    'nav.assistant': '詢問 Ellis',
    'nav.notifications': '通知',
    'nav.settings': '設定',
    'action.tour': '開始導覽',
    'action.switchRole': '切換角色',
    'action.continue': '繼續',
    'action.start': '開始申請',
    'action.approve': '批准並繼續',
    'common.language': '語言',
    'common.loading': '載入中…',
    'visa.title': '簽證平台',
    'visa.newCase': '新增案件',
    'visa.documents': '文件',
    'visa.preferences': '偏好設定',
    'visa.activity': '活動記錄',
    'visa.journey': '流程',
    'visa.readyTitle': '準備好提交了嗎？',
    'ocr.dropTitle': '拖入您的護照和輔助文件',
    'ocr.wrongPageRetry': '請上傳資料頁並重試',
    'exec.notReal': '非真實的政府提交',
    'exec.mockDisclaimer': '此案件在非生產門戶上執行。沒有向任何政府真正提交、付款或預約。',
    'result.submittedReal': '申請已提交',
    'result.mockReference': '模擬參考號（非真實簽證參考號）',
    'assistant.identity': '我是 Ellis，您的簽證申請助手。'
  }
}

// Translate a key for a language, interpolating {vars}. Falls back to English,
// then to the raw key — never throws, never shows blank.
export function t(lang, key, vars) {
  const table = STRINGS[lang] || STRINGS[DEFAULT_LANG]
  let s = table[key]
  if (s == null) s = STRINGS[DEFAULT_LANG][key]
  if (s == null) return key
  if (vars) {
    for (const [k, v] of Object.entries(vars)) s = s.replace(new RegExp(`\\{${k}\\}`, 'g'), String(v))
  }
  return s
}

export function isSupported(lang) {
  return SUPPORTED.includes(lang)
}
