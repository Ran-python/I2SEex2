import { useRole } from '../context/RoleContext.jsx'
import { FEATURES } from '../features/registry.js'
import { Link } from '../router.jsx'
import RoleSwitcher from './RoleSwitcher.jsx'

export default function Navbar({ activeKey }) {
  const { isLecturer } = useRole()
  const visibleFeatures = FEATURES.filter((f) => !f.lecturerOnly || isLecturer)

  return (
    <header className="navbar">
      <div className="navbar__top">
        <Link to="/attendance" className="navbar__brand">
          <span className="navbar__logo" aria-hidden="true">🎓</span>
          <span>ClassPulse</span>
        </Link>
        <RoleSwitcher />
      </div>
      <nav className="navbar__tabs" aria-label="Features">
        {visibleFeatures.map((feature) => (
          <Link
            key={feature.key}
            to={feature.path}
            className={`navbar__tab ${activeKey === feature.key ? 'is-active' : ''}`}
            aria-current={activeKey === feature.key ? 'page' : undefined}
          >
            <span aria-hidden="true">{feature.icon}</span>
            <span>{feature.label}</span>
            {feature.lecturerOnly && (
              <span className="badge badge--info badge--tiny">Lecturer</span>
            )}
          </Link>
        ))}
      </nav>
    </header>
  )
}
