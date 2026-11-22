import { ROLES, useRole } from '../context/RoleContext.jsx'
import { navigate } from '../router.jsx'

export default function RoleSwitcher() {
  const { role, setRole } = useRole()

  const choose = (next) => {
    setRole(next)
    // Leaving the QR deep-link page when switching to the lecturer dashboard.
    if (next === ROLES.LECTURER && window.location.pathname.startsWith('/attendance/checkin')) {
      navigate('/attendance', { replace: true })
    }
  }

  return (
    <div className="role-switch" role="radiogroup" aria-label="Demo role">
      <span className="role-switch__label">Demo view</span>
      <div className="role-switch__track">
        {[
          { value: ROLES.LECTURER, label: '👩‍🏫 Lecturer' },
          { value: ROLES.STUDENT, label: '🎓 Student' },
        ].map((option) => (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={role === option.value}
            className={`role-switch__option ${role === option.value ? 'is-active' : ''}`}
            onClick={() => choose(option.value)}
          >
            {option.label}
          </button>
        ))}
      </div>
    </div>
  )
}
