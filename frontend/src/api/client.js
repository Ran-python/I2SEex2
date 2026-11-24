// Thin fetch wrapper. Attaches device fingerprint, Lecturer PIN (when unlocked),
// and user-configured AI Provider / API Key headers from localStorage.
import { getDeviceIdentity } from '../features/attendance/fingerprint.js'

export class ApiError extends Error {
  constructor(status, message, data) {
    super(message)
    this.status = status
    this.data = data
  }
}

export function getStoredAiSettings() {
  try {
    const raw = localStorage.getItem('ai_settings')
    if (!raw) return { provider: 'gemini', apiKey: '', model: '' }
    const parsed = JSON.parse(raw)
    return {
      provider: parsed.provider || 'gemini',
      apiKey: parsed.apiKey || '',
      model: parsed.model || '',
    }
  } catch {
    return { provider: 'gemini', apiKey: '', model: '' }
  }
}

export function getStoredLecturerPin() {
  try {
    return sessionStorage.getItem('lecturer_pin') || '3358'
  } catch {
    return '3358'
  }
}

async function request(method, path, { body, form, headers } = {}) {
  const { fingerprint, deviceId } = getDeviceIdentity()
  const ai = getStoredAiSettings()
  const pin = getStoredLecturerPin()

  const reqHeaders = {
    'X-Device-Fingerprint': fingerprint,
    'X-Device-Id': deviceId,
    'X-Lecturer-Pin': pin,
    'X-AI-Provider': ai.provider || 'gemini',
    ...headers,
  }
  if (ai.apiKey) {
    reqHeaders['X-AI-Api-Key'] = ai.apiKey
  }
  if (ai.model) {
    reqHeaders['X-AI-Model'] = ai.model
  }

  const init = {
    method,
    credentials: 'include',
    headers: reqHeaders,
  }
  if (form) {
    init.body = form
  } else if (body !== undefined) {
    init.headers['Content-Type'] = 'application/json'
    init.body = JSON.stringify(body)
  }

  let response
  try {
    response = await fetch(path, init)
  } catch {
    throw new ApiError(0, 'Cannot reach the server. Please check your connection.')
  }
  const isJson = response.headers.get('content-type')?.includes('application/json')
  const data = isJson ? await response.json() : null
  if (!response.ok) {
    let message = data?.detail
    if (Array.isArray(message)) message = message.map((d) => d.msg).join(', ')
    if (!message && !isJson) {
      try {
        const rawText = (await response.text()).trim()
        if (rawText && !rawText.startsWith('<!DOCTYPE') && !rawText.startsWith('<html')) {
          message = rawText.slice(0, 240)
        }
      } catch {
        /* ignore */
      }
    }
    if (!message && response.status === 502) {
      message =
        'Gateway timeout (502): The video upload or AI processing took longer than the proxy timeout. Restart Vite (`npm run dev`) or try a shorter video clip.'
    }
    throw new ApiError(response.status, message || `Request failed (${response.status})`, data)
  }
  return data
}

export const api = {
  get: (path, options) => request('GET', path, options),
  post: (path, body, options) => request('POST', path, { ...options, body }),
  patch: (path, body, options) => request('PATCH', path, { ...options, body }),
  del: (path, options) => request('DELETE', path, options),
  upload: (path, file) => {
    const form = new FormData()
    form.append('file', file)
    return request('POST', path, { form })
  },
  uploadForm: (path, formData) => request('POST', path, { form: formData }),
}
