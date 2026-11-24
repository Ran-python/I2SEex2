import { useEffect, useState } from 'react'
import { api } from '../../../api/client.js'
import { useRole } from '../../../context/RoleContext.jsx'
import { formatClock, formatDuration, useCountdown } from '../../../hooks/useCountdown.js'
import { usePolling } from '../../../hooks/usePolling.js'
import { getDeviceIdentity } from '../fingerprint.js'

function formatLessonLabel(lesson) {
  if (!lesson) return null
  const base = `Lesson ${lesson.lesson_number}`
  if (!lesson.title || lesson.title.toLowerCase() === base.toLowerCase()) {
    return base
  }
  return `${base}: ${lesson.title}`
}

function ClassLessonBadge({ course, lesson, radiusMeters }) {
  if (!course && !lesson) return null
  const radius = radiusMeters ?? lesson?.radius_meters
  return (
    <div className="student-context-box" dir="auto">
      {course?.name && <div className="student-context-box__course">📚 {course.name}</div>}
      {lesson && <div className="student-context-box__lesson">{formatLessonLabel(lesson)}</div>}
      {radius !== null && radius !== undefined && (
        <div className="muted" style={{ fontSize: '0.84rem', marginTop: '0.2rem' }}>
          📍 Allowed Classroom GPS Radius: <strong>{Math.round(radius)}m</strong>
        </div>
      )}
    </div>
  )
}

function StatusCard({ icon, title, children, tone = 'neutral' }) {
  return (
    <section className={`student-card student-card--${tone}`}>
      <div className="student-card__icon" aria-hidden="true">{icon}</div>
      <h1>{title}</h1>
      {children}
    </section>
  )
}

function LockNotice({ seconds, syncKey }) {
  const remaining = useCountdown(seconds, syncKey)
  if (remaining <= 0) return <p className="muted">The device lock has expired. Refresh to register again.</p>
  return (
    <p className="lock-notice">
      🔒 This device is locked for <strong>{formatDuration(remaining)}</strong>
      <span className="lock-notice__clock">{formatClock(remaining)}</span>
    </p>
  )
}

function acquireStudentGps() {
  return new Promise((resolve, reject) => {
    if (!navigator.geolocation) {
      reject(
        new Error(
          'Geolocation is not supported by your browser. Please enable location services or use a modern mobile browser.',
        ),
      )
      return
    }
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        resolve({
          latitude: Number(pos.coords.latitude.toFixed(6)),
          longitude: Number(pos.coords.longitude.toFixed(6)),
          accuracy: Math.round(pos.coords.accuracy || 0),
        })
      },
      (err) => {
        if (err.code === 1) {
          reject(
            new Error(
              'Location access was denied. Please enable Location Services for your browser to verify that you are in the classroom.',
            ),
          )
        } else if (err.code === 3) {
          reject(
            new Error(
              'GPS location request timed out (10s). Please ensure GPS/Location Services are enabled on your device and try again.',
            ),
          )
        } else {
          reject(
            new Error(
              `Unable to retrieve your GPS coordinates (${err.message || 'Location unavailable'}). Please enable Location Services and try again.`,
            ),
          )
        }
      },
      { enableHighAccuracy: true, timeout: 10000, maximumAge: 0 },
    )
  })
}

export default function StudentCheckIn({ urlToken }) {
  const { studentIdentity } = useRole()
  const [form, setForm] = useState({
    full_name: studentIdentity?.studentName || '',
    id_number: '',
    email: '',
  })
  const [submitting, setSubmitting] = useState(false)
  const [locatingGps, setLocatingGps] = useState(false)
  const [error, setError] = useState(null)
  const [lock, setLock] = useState(null) // { seconds, at }
  const [success, setSuccess] = useState(null)

  useEffect(() => {
    if (studentIdentity?.studentName) {
      setForm((prev) => ({ ...prev, full_name: studentIdentity.studentName }))
    }
  }, [studentIdentity?.studentName])

  const effectiveUrlToken =
    urlToken || new URLSearchParams(window.location.search).get('token') || null

  const query = effectiveUrlToken ? `?token=${encodeURIComponent(effectiveUrlToken)}` : ''
  const { data: status, error: statusError, loading, refresh } = usePolling(
    () => api.get(`/api/attendance/status${query}`),
    5000,
    { enabled: !success },
  )

  const token = effectiveUrlToken || status?.token || null
  const tokenUsable = Boolean(status?.active && token && (effectiveUrlToken ? status.token_valid : true))
  const lockSeconds = lock?.seconds ?? status?.device_lock_seconds ?? 0

  const update = (field) => (event) => setForm((f) => ({ ...f, [field]: event.target.value }))

  const submit = async (event) => {
    event.preventDefault()
    if (!form.full_name.trim()) {
      setError('Please enter your full name.')
      return
    }
    setSubmitting(true)
    setLocatingGps(true)
    setError(null)
    const { fingerprint, deviceId } = getDeviceIdentity()
    try {
      const coords = await acquireStudentGps()
      setLocatingGps(false)
      const result = await api.post('/api/attendance/checkin', {
        token,
        full_name: form.full_name.trim(),
        id_number: form.id_number.trim() || null,
        email: form.email.trim() || null,
        latitude: coords.latitude,
        longitude: coords.longitude,
        fingerprint,
        device_id: deviceId,
      })
      setSuccess(result)
    } catch (err) {
      setLocatingGps(false)
      if (err.status === 429) {
        setLock({ seconds: err.data?.retry_after_seconds ?? 0, at: Date.now() })
      } else if (err.status === 410) {
        refresh()
      }
      setError(err.message)
    } finally {
      setLocatingGps(false)
      setSubmitting(false)
    }
  }

  // ---------------------------------------------------------------- screens
  if (success) {
    const time = new Date(success.checked_in_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    return (
      <div className="student-page">
        <StatusCard icon="✅" title="You're checked in!" tone="success">
          <ClassLessonBadge course={success.course} lesson={success.lesson} />
          <p className="student-card__name">{success.student.full_name}</p>
          <p className="muted">
            Registered at {time}
            {success.distance_meters !== null && success.distance_meters !== undefined
              ? ` · 📍 Verified distance: ${Math.round(success.distance_meters)}m`
              : ''}
          </p>
          {success.unlisted && (
            <p className="notice notice--warning">
              Your name wasn't found in the course roster, so the lecturer was notified to verify it.
            </p>
          )}
          <LockNotice seconds={success.device_lock_seconds} syncKey={success.checked_in_at} />
        </StatusCard>
      </div>
    )
  }

  if (loading && !status) {
    return (
      <div className="student-page">
        <StatusCard icon="⏳" title="Loading…" />
      </div>
    )
  }

  if (statusError && !status) {
    return (
      <div className="student-page">
        <StatusCard icon="📡" title="Can't reach the server" tone="danger">
          <p>{statusError.message}</p>
          <p className="muted">Please check your internet connection and try again.</p>
        </StatusCard>
      </div>
    )
  }

  if (!status?.active) {
    return (
      <div className="student-page">
        <StatusCard icon="🚪" title="Attendance is currently closed.">
          <p className="muted">Wait for the lecturer to open the attendance session, then scan the QR code.</p>
        </StatusCard>
      </div>
    )
  }

  if (lockSeconds > 0 && !error) {
    return (
      <div className="student-page">
        <StatusCard icon="🔒" title="Already registered from this device" tone="warning">
          <ClassLessonBadge course={status.course} lesson={status.lesson} radiusMeters={status.radius_meters} />
          <p>Each device can register only once every 15 minutes.</p>
          <LockNotice seconds={lockSeconds} syncKey={lock?.at ?? 'status'} />
        </StatusCard>
      </div>
    )
  }

  if (!tokenUsable) {
    return (
      <div className="student-page">
        <StatusCard icon="📷" title={effectiveUrlToken ? 'This QR code has expired' : 'Scan the QR code to check in'}>
          <ClassLessonBadge course={status.course} lesson={status.lesson} radiusMeters={status.radius_meters} />
          <p className="muted">
            {effectiveUrlToken
              ? 'Please scan the QR code currently shown on the lecturer’s screen.'
              : 'Use your phone camera to scan the QR code shown on the lecturer’s screen.'}
          </p>
        </StatusCard>
      </div>
    )
  }

  return (
    <div className="student-page">
      <form className="student-card checkin-form" onSubmit={submit} noValidate>
        <div className="student-card__icon" aria-hidden="true">🎓</div>
        <h1>Class Check-in</h1>
        <ClassLessonBadge course={status.course} lesson={status.lesson} radiusMeters={status.radius_meters} />
        <p className="muted checkin-form__timer">
          Attendance closes in <strong>{formatClock(status.seconds_remaining)}</strong>
        </p>

        <label className="field">
          <span>
            Full Name <span className="required">*</span>
          </span>
          <input
            type="text"
            autoComplete="name"
            placeholder="e.g. Dana Cohen / דנה כהן"
            value={form.full_name}
            onChange={update('full_name')}
            required
            autoFocus
            dir="auto"
          />
        </label>
        <label className="field">
          <span>
            ID Number (תעודת זהות) <span className="optional">optional</span>
          </span>
          <input
            type="text"
            inputMode="numeric"
            autoComplete="off"
            placeholder="123456789"
            value={form.id_number}
            onChange={update('id_number')}
            maxLength={20}
          />
        </label>
        <label className="field">
          <span>
            Email <span className="optional">optional</span>
          </span>
          <input
            type="email"
            inputMode="email"
            autoComplete="email"
            placeholder="you@example.com"
            value={form.email}
            onChange={update('email')}
            maxLength={200}
          />
        </label>

        {locatingGps && (
          <div className="notice notice--info" role="status">
            📍 Acquiring your GPS coordinates to verify classroom proximity…
          </div>
        )}

        {error && (
          <div className="notice notice--danger" role="alert">
            {error}
            {lock && <LockNotice seconds={lock.seconds} syncKey={lock.at} />}
          </div>
        )}

        <button type="submit" className="btn btn--primary btn--block btn--lg" disabled={submitting || !!lock}>
          {submitting
            ? locatingGps
              ? '📍 Verifying GPS Location…'
              : 'Submitting…'
            : '📍 Check in with GPS'}
        </button>
      </form>
    </div>
  )
}
