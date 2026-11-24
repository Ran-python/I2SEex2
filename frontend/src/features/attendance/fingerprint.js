// Browser / device fingerprint used for the 15-minute device lockout.
//
// Browsers don't expose MAC addresses, so we derive a stable identifier from
// device characteristics (user agent, screen, timezone, CPU cores, canvas &
// WebGL rendering). It is deterministic, so it survives clearing cookies or
// opening an incognito window. It's cached in localStorage and sent with every
// request (header + check-in payload).
//
// NOTE: crypto.subtle / crypto.randomUUID are unavailable on plain-HTTP LAN
// pages (not a "secure context"), so we use a small non-crypto hash instead.

const FP_KEY = 'device_fingerprint'
const ID_KEY = 'device_id'

// cyrb53 - fast 53-bit string hash (public domain).
function cyrb53(str, seed = 0) {
  let h1 = 0xdeadbeef ^ seed
  let h2 = 0x41c6ce57 ^ seed
  for (let i = 0; i < str.length; i++) {
    const ch = str.charCodeAt(i)
    h1 = Math.imul(h1 ^ ch, 2654435761)
    h2 = Math.imul(h2 ^ ch, 1597334677)
  }
  h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507) ^ Math.imul(h2 ^ (h2 >>> 13), 3266489909)
  h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507) ^ Math.imul(h1 ^ (h1 >>> 13), 3266489909)
  return (4294967296 * (2097151 & h2) + (h1 >>> 0)).toString(16).padStart(14, '0')
}

function canvasSignature() {
  try {
    const canvas = document.createElement('canvas')
    canvas.width = 240
    canvas.height = 60
    const ctx = canvas.getContext('2d')
    ctx.textBaseline = 'top'
    ctx.font = "16px 'Arial'"
    ctx.fillStyle = '#f60'
    ctx.fillRect(100, 1, 62, 20)
    ctx.fillStyle = '#069'
    ctx.fillText('Attendance ✓ נוכחות 123', 2, 15)
    ctx.fillStyle = 'rgba(102, 204, 0, 0.7)'
    ctx.fillText('Attendance ✓ נוכחות 123', 4, 17)
    return cyrb53(canvas.toDataURL())
  } catch {
    return 'no-canvas'
  }
}

function webglSignature() {
  try {
    const gl = document.createElement('canvas').getContext('webgl')
    if (!gl) return 'no-webgl'
    const ext = gl.getExtension('WEBGL_debug_renderer_info')
    const vendor = ext ? gl.getParameter(ext.UNMASKED_VENDOR_WEBGL) : gl.getParameter(gl.VENDOR)
    const renderer = ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER)
    return `${vendor}~${renderer}`
  } catch {
    return 'no-webgl'
  }
}

export function computeFingerprint() {
  try {
    const nav = window.navigator || {}
    const scr = window.screen || { width: 0, height: 0, colorDepth: 0 }
    let tz = 'UTC'
    try {
      tz = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
    } catch {
      tz = 'UTC'
    }
    const parts = [
      nav.userAgent || 'ua',
      nav.language || 'en',
      (nav.languages || []).join(','),
      nav.platform || '',
      nav.hardwareConcurrency || '',
      nav.deviceMemory || '',
      nav.maxTouchPoints || '',
      `${scr.width}x${scr.height}x${scr.colorDepth}`,
      window.devicePixelRatio || 1,
      tz,
      new Date().getTimezoneOffset(),
      canvasSignature(),
      webglSignature(),
    ]
    const raw = parts.join('||')
    return `fp_${cyrb53(raw, 1)}${cyrb53(raw, 2)}`
  } catch {
    return `fp_fallback_${randomId()}`
  }
}

function randomId() {
  try {
    if (window.crypto && typeof window.crypto.getRandomValues === 'function') {
      const bytes = new Uint8Array(16)
      window.crypto.getRandomValues(bytes)
      return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
    }
  } catch {
    /* fallback below */
  }
  let out = ''
  for (let i = 0; i < 32; i++) {
    out += Math.floor(Math.random() * 16).toString(16)
  }
  return out
}

let cached = null

export function getDeviceIdentity() {
  if (cached && cached.fingerprint && cached.deviceId) return cached
  let fingerprint = computeFingerprint()
  if (!fingerprint || fingerprint.length < 8) {
    fingerprint = `fp_${randomId()}`
  }
  let deviceId = null
  try {
    localStorage.setItem(FP_KEY, fingerprint)
    deviceId = localStorage.getItem(ID_KEY)
    if (!deviceId) {
      deviceId = randomId()
      localStorage.setItem(ID_KEY, deviceId)
    }
  } catch {
    deviceId = randomId() // storage blocked (some private modes)
  }
  cached = { fingerprint, deviceId }
  return cached
}
