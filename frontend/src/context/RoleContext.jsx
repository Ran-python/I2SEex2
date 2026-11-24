// Role switcher with Lecturer PIN Gate (3358), Student Quick-Login Identity, and AI Settings state.
import { createContext, useCallback, useContext, useMemo, useState } from 'react'
import { getStoredAiSettings } from '../api/client.js'

export const ROLES = { LECTURER: 'lecturer', STUDENT: 'student' }
export const LECTURER_PIN_CODE = '3358'

const ROLE_KEY = 'demo_role'
const UNLOCK_KEY = 'lecturer_unlocked'
const PIN_KEY = 'lecturer_pin'
const STUDENT_ID_KEY = 'student_identity'
const AI_SETTINGS_KEY = 'ai_settings'

const RoleContext = createContext(null)

function readUnlocked() {
  try {
    return sessionStorage.getItem(UNLOCK_KEY) === 'true'
  } catch {
    return false
  }
}

function initialRole() {
  const path = window.location.pathname
  if (path.startsWith('/attendance/checkin')) return ROLES.STUDENT
  if (readUnlocked()) {
    try {
      return localStorage.getItem(ROLE_KEY) || ROLES.LECTURER
    } catch {
      return ROLES.LECTURER
    }
  }
  return ROLES.STUDENT
}

function readStudentIdentity() {
  try {
    const raw = localStorage.getItem(STUDENT_ID_KEY)
    if (!raw) return { courseId: null, courseName: '', studentId: null, studentName: '' }
    const parsed = JSON.parse(raw)
    return {
      courseId: parsed.courseId ?? null,
      courseName: parsed.courseName || '',
      studentId: parsed.studentId ?? null,
      studentName: parsed.studentName || '',
    }
  } catch {
    return { courseId: null, courseName: '', studentId: null, studentName: '' }
  }
}

export function RoleProvider({ children }) {
  const [unlocked, setUnlocked] = useState(readUnlocked)
  const [role, setRoleState] = useState(initialRole)
  const [pinModalOpen, setPinModalOpen] = useState(false)
  const [aiSettingsOpen, setAiSettingsOpen] = useState(false)
  const [studentIdentity, setStudentIdentityState] = useState(readStudentIdentity)
  const [aiSettings, setAiSettingsState] = useState(getStoredAiSettings)

  const setRole = useCallback(
    (next) => {
      if (next === ROLES.LECTURER && !unlocked) {
        setPinModalOpen(true)
        return false
      }
      setRoleState(next)
      try {
        localStorage.setItem(ROLE_KEY, next)
      } catch {
        /* ignore */
      }
      return true
    },
    [unlocked],
  )

  const unlockLecturer = useCallback((enteredPin) => {
    const clean = String(enteredPin || '').trim()
    if (clean !== LECTURER_PIN_CODE) {
      return false
    }
    setUnlocked(true)
    setRoleState(ROLES.LECTURER)
    setPinModalOpen(false)
    try {
      sessionStorage.setItem(UNLOCK_KEY, 'true')
      sessionStorage.setItem(PIN_KEY, clean)
      localStorage.setItem(ROLE_KEY, ROLES.LECTURER)
    } catch {
      /* ignore */
    }
    return true
  }, [])

  const lockLecturer = useCallback(() => {
    setUnlocked(false)
    setRoleState(ROLES.STUDENT)
    try {
      sessionStorage.removeItem(UNLOCK_KEY)
      sessionStorage.removeItem(PIN_KEY)
      localStorage.setItem(ROLE_KEY, ROLES.STUDENT)
    } catch {
      /* ignore */
    }
  }, [])

  const setStudentIdentity = useCallback((updater) => {
    setStudentIdentityState((prev) => {
      const next = typeof updater === 'function' ? updater(prev) : { ...prev, ...updater }
      try {
        localStorage.setItem(STUDENT_ID_KEY, JSON.stringify(next))
      } catch {
        /* ignore */
      }
      return next
    })
  }, [])

  const saveAiSettings = useCallback((next) => {
    const normalized = {
      provider: next.provider || 'gemini',
      apiKey: (next.apiKey || '').trim(),
      model: (next.model || '').trim(),
    }
    setAiSettingsState(normalized)
    try {
      localStorage.setItem(AI_SETTINGS_KEY, JSON.stringify(normalized))
    } catch {
      /* ignore */
    }
  }, [])

  const value = useMemo(
    () => ({
      role,
      setRole,
      isLecturer: role === ROLES.LECTURER && unlocked,
      isStudent: role === ROLES.STUDENT || !unlocked,
      unlocked,
      unlockLecturer,
      lockLecturer,
      pinModalOpen,
      setPinModalOpen,
      studentIdentity,
      setStudentIdentity,
      aiSettings,
      saveAiSettings,
      aiSettingsOpen,
      setAiSettingsOpen,
    }),
    [
      role,
      setRole,
      unlocked,
      unlockLecturer,
      lockLecturer,
      pinModalOpen,
      studentIdentity,
      setStudentIdentity,
      aiSettings,
      saveAiSettings,
      aiSettingsOpen,
    ],
  )
  return <RoleContext.Provider value={value}>{children}</RoleContext.Provider>
}

export function useRole() {
  const ctx = useContext(RoleContext)
  if (!ctx) throw new Error('useRole must be used inside <RoleProvider>')
  return ctx
}
