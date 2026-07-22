import { contextBridge, ipcRenderer } from 'electron'

const api = {
  // settings & state
  getState: () => ipcRenderer.invoke('state:get'),
  getSettings: () => ipcRenderer.invoke('settings:get'),
  saveSettings: (partial) => ipcRenderer.invoke('settings:save', partial),
  resetDemo: () => ipcRenderer.invoke('demo:reset'),
  exportPdf: (payload) => ipcRenderer.invoke('export:pdf', payload),
  exportPdfToDesktop: (payload) => ipcRenderer.invoke('export:pdfToDesktop', payload),
  revealFile: (path) => ipcRenderer.invoke('file:reveal', path),
  openExternal: (url) => ipcRenderer.invoke('open:external', url),
  sendEmail: (payload) => ipcRenderer.invoke('email:send', payload),
  smtpStatus: () => ipcRenderer.invoke('email:smtpStatus'),

  // notifications
  listNotifs: (role) => ipcRenderer.invoke('notifs:list', role),
  addNotif: (n) => ipcRenderer.invoke('notifs:add', n),
  markNotifRead: (id) => ipcRenderer.invoke('notifs:markRead', id),
  markAllNotifsRead: (role) => ipcRenderer.invoke('notifs:markAllRead', role),

  // cases
  listCases: () => ipcRenderer.invoke('cases:list'),
  getCase: (id) => ipcRenderer.invoke('cases:get', id),
  createCase: (data) => ipcRenderer.invoke('cases:create', data),
  updateCase: (id, patch) => ipcRenderer.invoke('cases:update', id, patch),
  deleteCase: (id) => ipcRenderer.invoke('cases:delete', id),

  // documents
  pickDocuments: () => ipcRenderer.invoke('docs:pickAndRead'),
  ocrDoc: (p) => ipcRenderer.invoke('ocr:extract', p),
  detectDocLanguage: (path) => ipcRenderer.invoke('doc:detectLanguage', { path }),
  translateDoc: (payload) => ipcRenderer.invoke('doc:translate', payload),

  // Trip.com portal
  listTrips: () => ipcRenderer.invoke('trips:list'),
  createTrip: (data) => ipcRenderer.invoke('trips:create', data),
  updateTrip: (id, patch) => ipcRenderer.invoke('trips:update', id, patch),
  deleteTrip: (id) => ipcRenderer.invoke('trips:delete', id),
  planTrip: (payload) => ipcRenderer.invoke('trips:plan', payload),
  sendTripEmail: (tripId, payload) => ipcRenderer.invoke('trips:email', { tripId, ...payload }),
  decisionsInfo: (tripId) => ipcRenderer.invoke('trips:decisionsInfo', { tripId }),
  openDecisionsDir: () => ipcRenderer.invoke('trips:openDecisionsDir'),
  issueTrip: (tripId, attachmentPath, detail) => ipcRenderer.invoke('trips:issue', { tripId, attachmentPath, detail }),
  tripAgent: {
    ingest: (tripId) => ipcRenderer.invoke('trips:agent:ingest', { tripId }),
    review: (tripId) => ipcRenderer.invoke('trips:agent:review', { tripId }),
    assemble: (tripId) => ipcRenderer.invoke('trips:agent:assemble', { tripId }),
    appointment: (tripId, where) => ipcRenderer.invoke('trips:agent:appointment', { tripId, where }),
    mission: (tripId) => ipcRenderer.invoke('trips:agent:mission', { tripId }),
    research: (tripId) => ipcRenderer.invoke('trips:agent:research', { tripId }),
    remedy: (tripId, reason) => ipcRenderer.invoke('trips:agent:remedy', { tripId, reason }),
    submit: (tripId, attachments, channelLabel) => ipcRenderer.invoke('trips:agent:submit', { tripId, attachments, channelLabel }),
    riskReview: (tripId) => ipcRenderer.invoke('trips:agent:riskReview', { tripId }),
    record: (tripId, entry) => ipcRenderer.invoke('trips:agent:record', { tripId, entry })
  },

  // AI capabilities
  ai: {
    extractDocument: (p) => ipcRenderer.invoke('ai:extractDocument', p),
    answerQuestion: (p) => ipcRenderer.invoke('ai:answerQuestion', p),
    riskFlags: (p) => ipcRenderer.invoke('ai:riskFlags', p),
    summarizeNotice: (p) => ipcRenderer.invoke('ai:summarizeNotice', p),
    prepareForm: (p) => ipcRenderer.invoke('ai:prepareForm', p),
    evidencePacket: (p) => ipcRenderer.invoke('ai:evidencePacket', p),
    complianceAudit: (p) => ipcRenderer.invoke('ai:complianceAudit', p),
    travelRisk: (p) => ipcRenderer.invoke('ai:travelRisk', p),
    lifecyclePlan: (p) => ipcRenderer.invoke('ai:lifecyclePlan', p),
    translateDocument: (p) => ipcRenderer.invoke('ai:translateDocument', p),
    authenticityCheck: (p) => ipcRenderer.invoke('ai:authenticityCheck', p),
    assistantChat: (p) => ipcRenderer.invoke('ai:assistantChat', p),
    localStatus: (p) => ipcRenderer.invoke('ai:localStatus', p),
    kimiStatus: () => ipcRenderer.invoke('ai:kimiStatus')
  }
}

contextBridge.exposeInMainWorld('ellis', api)
