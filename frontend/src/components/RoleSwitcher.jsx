import { useState } from 'react'
import { ROLES, useRole } from '../context/RoleContext.jsx'
import { navigate } from '../router.jsx'

export default function RoleSwitcher() {
  const {
    isLecturer,
    unlocked,
    setRole,
    unlockLecturer,
    lockLecturer,
    pinModalOpen,
    setPinModalOpen,
    aiSettings,
    setAiSettingsOpen,
  } = useRole()

  const [pinInput, setPinInput] = useState('')
  const [pinError, setPinError] = useState('')

  const choose = (next) => {
    setPinError('')
    const ok = setRole(next)
    if (ok && next === ROLES.LECTURER && window.location.pathname.startsWith('/attendance/checkin')) {
      navigate('/attendance', { replace: true })
    }
  }

  const handlePinSubmit = (event) => {
    event.preventDefault()
    if (unlockLecturer(pinInput)) {
      setPinInput('')
      setPinError('')
      if (window.location.pathname.startsWith('/attendance/checkin')) {
        navigate('/attendance', { replace: true })
      }
    } else {
      setPinError('Incorrect PIN code. Enter the 4-digit lecturer code.')
    }
  }

  const handleLock = () => {
    lockLecturer()
    if (window.location.pathname.startsWith('/summary')) {
      navigate('/attendance', { replace: true })
    }
  }

  const hasApiKey = Boolean(aiSettings?.apiKey)

  return (
    <>
      <div className="navbar__controls">
        <button
          type="button"
          className={`btn btn--sm ${hasApiKey ? 'btn--secondary' : 'btn--ghost'}`}
          onClick={() => setAiSettingsOpen(true)}
          title="Configure AI Provider & API Key (Gemini / Claude / OpenAI)"
        >
          ⚙️ AI Settings{' '}
          <span className={`badge badge--tiny ${hasApiKey ? 'badge--success' : 'badge--warning'}`}>
            {(aiSettings?.provider || 'gemini').toUpperCase()}
          </span>
        </button>

        <div className="role-switch" role="radiogroup" aria-label="Role view">
          <div className="role-switch__track">
            <button
              type="button"
              role="radio"
              aria-checked={isLecturer}
              className={`role-switch__option ${isLecturer ? 'is-active' : ''}`}
              onClick={() => choose(ROLES.LECTURER)}
            >
              {unlocked ? '👩‍🏫 Lecturer' : '🔒 Lecturer'}
            </button>
            <button
              type="button"
              role="radio"
              aria-checked={!isLecturer}
              className={`role-switch__option ${!isLecturer ? 'is-active' : ''}`}
              onClick={() => choose(ROLES.STUDENT)}
            >
              🎓 Student
            </button>
          </div>
          {unlocked && (
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              onClick={handleLock}
              title="Lock Lecturer View and require PIN 3358 again"
            >
              🔒 Lock
            </button>
          )}
        </div>
      </div>

      {pinModalOpen && (
        <div className="modal-backdrop" onClick={() => setPinModalOpen(false)}>
          <div className="modal-card card" onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true">
            <div className="card__header">
              <div>
                <h2>🔒 Unlock Lecturer Mode</h2>
                <p className="muted">Enter the 4-digit Lecturer Access PIN to manage attendance, polls & settings.</p>
              </div>
              <button
                type="button"
                className="notice__close"
                onClick={() => setPinModalOpen(false)}
                aria-label="Close"
              >
                ✕
              </button>
            </div>
            <form className="modal-form" onSubmit={handlePinSubmit}>
              <label className="field">
                <span>Lecturer PIN Code</span>
                <input
                  type="password"
                  inputMode="numeric"
                  maxLength={8}
                  placeholder="••••"
                  value={pinInput}
                  onChange={(e) => {
                    setPinInput(e.target.value)
                    setPinError('')
                  }}
                  autoFocus
                  required
                />
              </label>
              {pinError && <div className="notice notice--danger">{pinError}</div>}
              <div className="modal-actions">
                <button type="button" className="btn btn--ghost" onClick={() => setPinModalOpen(false)}>
                  Cancel
                </button>
                <button type="submit" className="btn btn--primary">
                  Unlock Lecturer View
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </>
  )
}
