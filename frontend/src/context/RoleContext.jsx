// Demo-mode role switcher (no login): Lecturer <-> Student, persisted per browser.
import { createContext, useCallback, useContext, useMemo, useState } from 'react'

export const ROLES = { LECTURER: 'lecturer', STUDENT: 'student' }
const STORAGE_KEY = 'demo_role'

const RoleContext = createContext(null)

function initialRole() {
  // Students arriving from the QR code always start in Student View.
  if (window.location.pathname.startsWith('/attendance/checkin')) return ROLES.STUDENT
  try {
    return localStorage.getItem(STORAGE_KEY) || ROLES.LECTURER
  } catch {
    return ROLES.LECTURER
  }
}

export function RoleProvider({ children }) {
  const [role, setRoleState] = useState(initialRole)

  const setRole = useCallback((next) => {
    setRoleState(next)
    try {
      localStorage.setItem(STORAGE_KEY, next)
    } catch {
      /* storage unavailable */
    }
  }, [])

  const value = useMemo(
    () => ({ role, setRole, isLecturer: role === ROLES.LECTURER, isStudent: role === ROLES.STUDENT }),
    [role, setRole],
  )
  return <RoleContext.Provider value={value}>{children}</RoleContext.Provider>
}

export function useRole() {
  const ctx = useContext(RoleContext)
  if (!ctx) throw new Error('useRole must be used inside <RoleProvider>')
  return ctx
}
