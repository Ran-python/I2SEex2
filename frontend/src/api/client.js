// Thin fetch wrapper. All calls go to relative /api URLs, which Vite proxies to
// FastAPI in dev and FastAPI serves directly in single-port mode.
import { getDeviceIdentity } from '../features/attendance/fingerprint.js'

export class ApiError extends Error {
  constructor(status, message, data) {
    super(message)
    this.status = status
    this.data = data
  }
}

async function request(method, path, { body, form, headers } = {}) {
  const { fingerprint, deviceId } = getDeviceIdentity()
  const init = {
    method,
    credentials: 'include', // send/receive the HttpOnly device_lock_token cookie
    headers: {
      'X-Device-Fingerprint': fingerprint,
      'X-Device-Id': deviceId,
      ...headers,
    },
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
    throw new ApiError(0, 'Cannot reach the server. Are you connected to the classroom Wi-Fi?')
  }
  const isJson = response.headers.get('content-type')?.includes('application/json')
  const data = isJson ? await response.json() : null
  if (!response.ok) {
    let message = data?.detail
    if (Array.isArray(message)) message = message.map((d) => d.msg).join(', ') // FastAPI validation errors
    throw new ApiError(response.status, message || `Request failed (${response.status})`, data)
  }
  return data
}

export const api = {
  get: (path) => request('GET', path),
  post: (path, body) => request('POST', path, { body }),
  patch: (path, body) => request('PATCH', path, { body }),
  del: (path) => request('DELETE', path),
  upload: (path, file) => {
    const form = new FormData()
    form.append('file', file)
    return request('POST', path, { form })
  },
}
