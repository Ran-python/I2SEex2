import { useEffect, useState } from 'react'

/**
 * Live countdown driven by the server's `secondsRemaining` value (immune to clock
 * differences between the lecturer's laptop and students' phones).
 * `syncKey` should change whenever a fresh value arrives from the server.
 */
export function useCountdown(secondsRemaining, syncKey) {
  const [remaining, setRemaining] = useState(() => Math.max(0, Math.ceil(secondsRemaining || 0)))

  useEffect(() => {
    const deadline = Date.now() + (secondsRemaining || 0) * 1000
    const tick = () => setRemaining(Math.max(0, Math.ceil((deadline - Date.now()) / 1000)))
    const immediate = window.setTimeout(tick, 0)
    const id = window.setInterval(tick, 250)
    return () => {
      window.clearTimeout(immediate)
      window.clearInterval(id)
    }
  }, [secondsRemaining, syncKey])

  return remaining
}

export function formatClock(totalSeconds) {
  const s = Math.max(0, Math.floor(totalSeconds))
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`
}

export function formatDuration(totalSeconds) {
  const s = Math.max(0, Math.floor(totalSeconds))
  const m = Math.floor(s / 60)
  const sec = s % 60
  if (m && sec) return `${m} min ${sec} sec`
  if (m) return `${m} min`
  return `${sec} sec`
}
