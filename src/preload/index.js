import { contextBridge, ipcRenderer } from 'electron'

// Ellis for Trip.com — the preload exposes only what the Trip.com-only shell
// uses: settings, notifications, document utilities, PDF export, the demo
// pipeline's trips/tripAgent channels (registered by the main process only in
// local_mock_demo mode; otherwise they reply { error: 'demo_disabled' }), and
// the support-chat AI channel plus engine status checks for Settings.
const api = {
  // settings
  getSettings: () => ipcRenderer.invoke('settings:get'),
  saveSettings: (partial) => ipcRenderer.invoke('settings:save', partial),

  // shell utilities
  exportPdf: (payload) => ipcRenderer.invoke('export:pdf', payload),
  exportPdfToDesktop: (payload) => ipcRenderer.invoke('export:pdfToDesktop', payload),
  revealFile: (path) => ipcRenderer.invoke('file:reveal', path),
  openExternal: (url) => ipcRenderer.invoke('open:external', url),

  // notifications
  listNotifs: (scope) => ipcRenderer.invoke('notifs:list', scope),
  addNotif: (n) => ipcRenderer.invoke('notifs:add', n),
  markNotifRead: (id) => ipcRenderer.invoke('notifs:markRead', id),
  markAllNotifsRead: (scope) => ipcRenderer.invoke('notifs:markAllRead', scope),

  // documents
  pickDocuments: () => ipcRenderer.invoke('docs:pickAndRead'),
  ocrDoc: (p) => ipcRenderer.invoke('ocr:extract', p),
  detectDocLanguage: (path) => ipcRenderer.invoke('doc:detectLanguage', { path }),
  translateDoc: (payload) => ipcRenderer.invoke('doc:translate', payload),

  // Trip.com demo portal (gated by runtime mode in the main process)
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

  // AI: support chat + engine status (Settings connection checks)
  ai: {
    assistantChat: (p) => ipcRenderer.invoke('ai:assistantChat', p),
    localStatus: (p) => ipcRenderer.invoke('ai:localStatus', p),
    kimiStatus: () => ipcRenderer.invoke('ai:kimiStatus')
  }
}

contextBridge.exposeInMainWorld('ellis', api)
