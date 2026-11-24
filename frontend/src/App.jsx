import { useEffect } from 'react'
import './App.css'
import AISettingsModal from './components/AISettingsModal.jsx'
import Navbar from './components/Navbar.jsx'
import StudentIdentityBar from './components/StudentIdentityBar.jsx'
import { RoleProvider } from './context/RoleContext.jsx'
import { DEFAULT_FEATURE, findFeature } from './features/registry.js'
import { navigate, useLocation } from './router.jsx'

function Shell() {
  const { path } = useLocation()
  const matched = findFeature(path)
  const feature = matched || DEFAULT_FEATURE

  useEffect(() => {
    if (path === '/') {
      navigate(DEFAULT_FEATURE.path, { replace: true })
    }
  }, [path])

  const PageComponent = feature.component
  return (
    <div className="app-shell">
      <Navbar activeKey={feature.key} />
      <main className="app-main">
        <StudentIdentityBar />
        <PageComponent feature={feature} />
      </main>
      <AISettingsModal />
    </div>
  )
}

export default function App() {
  return (
    <RoleProvider>
      <Shell />
    </RoleProvider>
  )
}
