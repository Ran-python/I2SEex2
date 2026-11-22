import { useState } from 'react'
import { api } from '../../../api/client.js'
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

function ClassLessonBadge({ course, lesson }) {
  if (!course && !lesson) return null
  return (
    <div className="student-context-box" dir="auto">
      {course?.name && <div className="student-context-box__course">📚 {course.name}</div>}
      {lesson && <div className="student-context-box__lesson">{formatLessonLabel(lesson)}</div>}
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

export default function StudentCheckIn({ urlToken }) {
  const [form, setForm] = useState({ full_name: '', id_number: '', email: '' })
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState(null)
  const [lock, setLock] = useState(null) // { seconds, at }
  const [success, setSuccess] = useState(null)

  const query = urlToken ? `?token=${encodeURIComponent(urlToken)}` : ''
  const { data: status, error: statusError, loading, refresh } = usePolling(
    () => api.get(`/api/attendance/status${query}`),
    5000,
    { enabled: !success },
  )

  const token = urlToken || status?.token || null
  const tokenUsable = Boolean(status?.active && token && (urlToken ? status.token_valid : true))
  const lockSeconds = lock?.seconds ?? status?.device_lock_seconds ?? 0

  const update = (field) => (event) => setForm((f) => ({ ...f, [field]: event.target.value }))

  const submit = async (event) => {
    event.preventDefault()
    if (!form.full_name.trim()) {
      setError('Please enter your full name.')
      return
    }
    setSubmitting(true)
    setError(null)
    const { fingerprint, deviceId } = getDeviceIdentity()
    try {
      const result = await api.post('/api/attendance/checkin', {
        token,
        full_name: form.full_name.trim(),
        id_number: form.id_number.trim() || null,
        email: form.email.trim() || null,
        fingerprint,
        device_id: deviceId,
      })
      setSuccess(result)
    } catch (err) {
      if (err.status === 429) {
        setLock({ seconds: err.data?.retry_after_seconds ?? 0, at: Date.now() })
      } else if (err.status === 410) {
        refresh()
      }
      setError(err.message)
    } finally {
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
          <p className="muted">Registered at {time}</p>
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
        <StatusCard icon="📡" title="Can't reach the class server" tone="danger">
          <p>{statusError.message}</p>
          <p className="muted">Make sure you're connected to the same Wi-Fi network as the lecturer.</p>
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
          <ClassLessonBadge course={status.course} lesson={status.lesson} />
          <p>Each device can register only once every 15 minutes.</p>
          <LockNotice seconds={lockSeconds} syncKey={lock?.at ?? 'status'} />
        </StatusCard>
      </div>
    )
  }

  if (!tokenUsable) {
    return (
      <div className="student-page">
        <StatusCard icon="📷" title={urlToken ? 'This QR code has expired' : 'Scan the QR code to check in'}>
          <ClassLessonBadge course={status.course} lesson={status.lesson} />
          <p className="muted">
            {urlToken
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
        <ClassLessonBadge course={status.course} lesson={status.lesson} />
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

        {error && (
          <div className="notice notice--danger" role="alert">
            {error}
            {lock && <LockNotice seconds={lock.seconds} syncKey={lock.at} />}
          </div>
        )}

        <button type="submit" className="btn btn--primary btn--block btn--lg" disabled={submitting || !!lock}>
          {submitting ? 'Submitting…' : 'Check in'}
        </button>
      </form>
    </div>
  )
}
