// Minimal client-side router (pathname + history API) - no extra dependency needed.
import { useEffect, useState } from 'react'

const NAV_EVENT = 'app:navigate'

export function navigate(to, { replace = false } = {}) {
  if (replace) window.history.replaceState({}, '', to)
  else window.history.pushState({}, '', to)
  window.dispatchEvent(new Event(NAV_EVENT))
}

function snapshot() {
  return {
    path: window.location.pathname.replace(/\/+$/, '') || '/',
    query: new URLSearchParams(window.location.search),
  }
}

export function useLocation() {
  const [location, setLocation] = useState(snapshot)
  useEffect(() => {
    const update = () => setLocation(snapshot())
    window.addEventListener('popstate', update)
    window.addEventListener(NAV_EVENT, update)
    return () => {
      window.removeEventListener('popstate', update)
      window.removeEventListener(NAV_EVENT, update)
    }
  }, [])
  return location
}

export function Link({ to, children, ...props }) {
  const onClick = (event) => {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return
    event.preventDefault()
    navigate(to)
  }
  return (
    <a href={to} onClick={onClick} {...props}>
      {children}
    </a>
  )
}
