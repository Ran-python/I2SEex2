import { useEffect, useState } from 'react'
import { api } from '../../api/client.js'
import { useRole } from '../../context/RoleContext.jsx'
import { usePolling } from '../../hooks/usePolling.js'
import { useLocation } from '../../router.jsx'

export default function PollsPage() {
  const { isLecturer, studentIdentity, setStudentIdentity } = useRole()
  const { query } = useLocation()
  const urlPollId = query.get('poll_id') ? Number(query.get('poll_id')) : null
  const urlCourseId = query.get('course_id') ? Number(query.get('course_id')) : null

  const [courses, setCourses] = useState([])
  const [selectedCourseId, setSelectedCourseId] = useState(
    urlCourseId || studentIdentity?.courseId || null,
  )

  // Lecturer create poll state
  const [question, setQuestion] = useState('')
  const [options, setOptions] = useState(['Yes, completely clear', 'Mostly clear, need an example', 'Still confused'])
  const [busyAction, setBusyAction] = useState(null)
  const [banner, setBanner] = useState(null)
  const [selectedQrPollId, setSelectedQrPollId] = useState(null)
  const [copiedId, setCopiedId] = useState(null)

  useEffect(() => {
    if (urlCourseId) {
      setSelectedCourseId(urlCourseId)
    } else if (studentIdentity?.courseId && !isLecturer) {
      setSelectedCourseId(studentIdentity.courseId)
    }
  }, [urlCourseId, studentIdentity?.courseId, isLecturer])

  useEffect(() => {
    api
      .get('/api/courses')
      .then((list) => {
        if (!Array.isArray(list) || list.length === 0) return
        setCourses(list)
        if (!selectedCourseId || !list.some((c) => c.id === selectedCourseId)) {
          setSelectedCourseId(list[0].id)
        }
      })
      .catch(() => {})
  }, [selectedCourseId])

  const buildPollsUrl = () => {
    const params = new URLSearchParams()
    if (selectedCourseId) params.set('course_id', String(selectedCourseId))
    if (urlPollId) params.set('poll_id', String(urlPollId))
    if (studentIdentity?.studentId) params.set('student_id', String(studentIdentity.studentId))
    return `/api/polls?${params.toString()}`
  }

  const { data, loading, refresh } = usePolling(() => api.get(buildPollsUrl()), 3000)

  useEffect(() => {
    refresh()
  }, [selectedCourseId, urlPollId, studentIdentity?.studentId, refresh])

  const polls = data?.polls || []
  const activeQrPoll =
    polls.find((p) => p.id === selectedQrPollId) ||
    polls.find((p) => p.is_active) ||
    polls[0] ||
    null

  const handleOptionChange = (idx, value) => {
    setOptions((prev) => prev.map((o, i) => (i === idx ? value : o)))
  }

  const handleCreatePoll = async (event) => {
    event.preventDefault()
    const cleanOpts = options.map((o) => o.trim()).filter(Boolean)
    if (!question.trim() || cleanOpts.length < 2) {
      setBanner({ tone: 'danger', text: 'Please enter a question and at least 2 options.' })
      return
    }
    setBusyAction('create')
    setBanner(null)
    try {
      const created = await api.post('/api/polls', {
        course_id: selectedCourseId,
        question: question.trim(),
        options: cleanOpts,
        base_url: window.location.origin,
      })
      setQuestion('')
      setSelectedQrPollId(created.id)
      await refresh()
      setBanner({ tone: 'success', text: '📊 Poll created and opened! Students can scan the QR code to vote.' })
    } catch (err) {
      setBanner({ tone: 'danger', text: err.message })
    } finally {
      setBusyAction(null)
    }
  }

  const handleTogglePoll = async (poll) => {
    setBusyAction(`toggle-${poll.id}`)
    try {
      await api.patch(`/api/polls/${poll.id}/toggle`, {
        is_active: !poll.is_active,
        base_url: window.location.origin,
      })
      await refresh()
    } catch (err) {
      setBanner({ tone: 'danger', text: err.message })
    } finally {
      setBusyAction(null)
    }
  }

  const handleDeletePoll = async (pollId) => {
    setBusyAction(`del-${pollId}`)
    try {
      await api.del(`/api/polls/${pollId}`)
      await refresh()
    } catch (err) {
      setBanner({ tone: 'danger', text: err.message })
    } finally {
      setBusyAction(null)
    }
  }

  const handleVote = async (pollId, optionIndex) => {
    setBusyAction(`vote-${pollId}`)
    setBanner(null)
    try {
      await api.post(`/api/polls/${pollId}/vote`, {
        option_index: optionIndex,
        student_id: studentIdentity?.studentId || null,
      })
      await refresh()
      setBanner({ tone: 'success', text: '✅ Your vote has been recorded!' })
    } catch (err) {
      setBanner({ tone: 'danger', text: err.message })
    } finally {
      setBusyAction(null)
    }
  }

  const handleCopyUrl = async (poll) => {
    try {
      await navigator.clipboard.writeText(poll.checkin_url)
      setCopiedId(poll.id)
      window.setTimeout(() => setCopiedId(null), 2000)
    } catch {
      /* ignore */
    }
  }

  return (
    <div className="dashboard">
      <section className="card context-bar">
        <div className="context-bar__row">
          <div className="context-group">
            <label className="context-group__label" htmlFor="polls-course-select">
              📚 Course Polls
            </label>
            <select
              id="polls-course-select"
              className="select-input select-input--strong"
              value={selectedCourseId || ''}
              onChange={(e) => {
                const cid = Number(e.target.value)
                const found = courses.find((c) => c.id === cid)
                setSelectedCourseId(cid)
                if (!isLecturer) {
                  setStudentIdentity({
                    courseId: cid,
                    courseName: found?.name || '',
                    studentId: null,
                    studentName: '',
                  })
                }
              }}
              dir="auto"
            >
              {courses.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </div>
        </div>
      </section>

      {banner && (
        <div className={`notice notice--${banner.tone} notice--dismissible`}>
          <span>{banner.text}</span>
          <button type="button" className="notice__close" onClick={() => setBanner(null)}>
            ✕
          </button>
        </div>
      )}

      {isLecturer ? (
        <div className="dashboard__grid">
          {/* Lecturer Left: Create Poll + Active Poll QR Code */}
          <div className="dashboard__side">
            <section className="card">
              <h2>📊 Create On-the-Spot Poll (KISS)</h2>
              <p className="muted">
                Launch a quick multiple-choice comprehension check with an instant scannable QR code.
              </p>

              <form className="quick-form" onSubmit={handleCreatePoll}>
                <label className="field">
                  <span>
                    Poll Question <span className="required">*</span>
                  </span>
                  <input
                    type="text"
                    placeholder='e.g. "Did you understand the SOLID Dependency Inversion example?"'
                    value={question}
                    onChange={(e) => setQuestion(e.target.value)}
                    required
                    dir="auto"
                  />
                </label>

                <div className="field">
                  <span>Answer Options (2 to 5)</span>
                  {options.map((opt, idx) => (
                    <div key={idx} className="context-group__controls" style={{ marginBottom: '0.4rem' }}>
                      <input
                        type="text"
                        placeholder={`Option ${idx + 1}`}
                        value={opt}
                        onChange={(e) => handleOptionChange(idx, e.target.value)}
                        dir="auto"
                      />
                      {options.length > 2 && (
                        <button
                          type="button"
                          className="btn btn--ghost btn--sm"
                          onClick={() => setOptions((prev) => prev.filter((_, i) => i !== idx))}
                        >
                          ✕
                        </button>
                      )}
                    </div>
                  ))}
                  {options.length < 5 && (
                    <button
                      type="button"
                      className="btn btn--ghost btn--sm"
                      onClick={() => setOptions((prev) => [...prev, ''])}
                    >
                      + Add Option
                    </button>
                  )}
                </div>

                <button type="submit" className="btn btn--primary btn--block" disabled={busyAction === 'create'}>
                  {busyAction === 'create' ? 'Launching Poll…' : '▶ Launch Poll & Generate QR'}
                </button>
              </form>
            </section>

            {activeQrPoll && (
              <section className="card qr-card">
                <div className="card__header">
                  <div>
                    <h2 dir="auto">{activeQrPoll.question}</h2>
                    <p className="muted">Scan with phone camera to vote in real time</p>
                  </div>
                  <span className={`badge ${activeQrPoll.is_active ? 'badge--live' : 'badge--muted'}`}>
                    {activeQrPoll.is_active ? '● LIVE POLL' : 'CLOSED'}
                  </span>
                </div>

                <div className="qr-active">
                  <div className="qr-frame">
                    <img
                      src={`/api/polls/${activeQrPoll.id}/qr.png`}
                      alt="Poll voting QR code"
                      className="qr-image"
                    />
                  </div>
                  <div className="qr-url-box" style={{ width: '100%' }}>
                    <span className="qr-url-box__label">Direct Student Voting Link</span>
                    <code className="qr-url-box__code">{activeQrPoll.checkin_url}</code>
                    <div className="qr-url-box__actions">
                      <button
                        type="button"
                        className="btn btn--secondary btn--sm"
                        onClick={() => handleCopyUrl(activeQrPoll)}
                      >
                        {copiedId === activeQrPoll.id ? '✓ Copied' : '📋 Copy Voting Link'}
                      </button>
                    </div>
                  </div>
                </div>
              </section>
            )}
          </div>

          {/* Lecturer Right: Live Poll Results */}
          <section className="card">
            <h2>Live Poll Results ({polls.length})</h2>
            <p className="muted">Auto-updates every 3 seconds as students vote.</p>

            {loading && !data ? (
              <p className="empty-state">Loading polls…</p>
            ) : polls.length === 0 ? (
              <p className="empty-state">No polls created for this course yet.</p>
            ) : (
              <div className="polls-list">
                {polls.map((poll) => (
                  <article key={poll.id} className="poll-card">
                    <div className="poll-card__header">
                      <div>
                        <span className={`badge ${poll.is_active ? 'badge--live' : 'badge--muted'}`}>
                          {poll.is_active ? '● OPEN' : 'CLOSED'}
                        </span>
                        <h3 dir="auto">{poll.question}</h3>
                      </div>
                      <div className="row-actions">
                        <button
                          type="button"
                          className="btn btn--secondary btn--sm"
                          onClick={() => setSelectedQrPollId(poll.id)}
                        >
                          📱 Show QR
                        </button>
                        <button
                          type="button"
                          className={`btn btn--sm ${poll.is_active ? 'btn--danger' : 'btn--primary'}`}
                          onClick={() => handleTogglePoll(poll)}
                          disabled={busyAction === `toggle-${poll.id}`}
                        >
                          {poll.is_active ? '⏹ Close Poll' : '▶ Re-open'}
                        </button>
                        <button
                          type="button"
                          className="btn btn--ghost btn--sm"
                          onClick={() => handleDeletePoll(poll.id)}
                        >
                          🗑️
                        </button>
                      </div>
                    </div>

                    <div className="poll-bars">
                      {poll.results.map((opt) => (
                        <div key={opt.index} className="poll-bar">
                          <div className="poll-bar__label">
                            <span dir="auto">{opt.label}</span>
                            <strong>
                              {opt.votes} ({opt.percentage}%)
                            </strong>
                          </div>
                          <div className="poll-bar__track">
                            <div
                              className="poll-bar__fill"
                              style={{ width: `${opt.percentage}%` }}
                            />
                          </div>
                        </div>
                      ))}
                    </div>
                    <p className="muted">Total votes: <strong>{poll.total_votes}</strong></p>
                  </article>
                ))}
              </div>
            )}
          </section>
        </div>
      ) : (
        /* Student View: One-Tap Voting & Live Results */
        <div className="student-page" style={{ maxWidth: '620px' }}>
          {loading && !data ? (
            <section className="student-card">
              <h1>Loading polls…</h1>
            </section>
          ) : polls.length === 0 ? (
            <section className="student-card">
              <div className="student-card__icon">📊</div>
              <h1>No active polls right now</h1>
              <p className="muted">When the lecturer launches a poll, it will appear here automatically.</p>
            </section>
          ) : (
            <div className="polls-list">
              {polls.map((poll) => {
                const hasVoted = poll.my_vote !== null && poll.my_vote !== undefined
                return (
                  <section key={poll.id} className="card poll-card">
                    <div className="poll-card__header">
                      <span className={`badge ${poll.is_active ? 'badge--live' : 'badge--muted'}`}>
                        {poll.is_active ? '● LIVE POLL' : 'CLOSED'}
                      </span>
                      <span className="muted">{poll.total_votes} votes</span>
                    </div>
                    <h2 dir="auto" style={{ margin: '0.5rem 0 0.85rem' }}>
                      {poll.question}
                    </h2>

                    {poll.is_active && !hasVoted ? (
                      <div className="poll-vote-options">
                        {poll.options.map((label, idx) => (
                          <button
                            key={idx}
                            type="button"
                            className="btn btn--secondary btn--block poll-option-btn"
                            disabled={busyAction === `vote-${poll.id}`}
                            onClick={() => handleVote(poll.id, idx)}
                            dir="auto"
                          >
                            {label}
                          </button>
                        ))}
                      </div>
                    ) : (
                      <div className="poll-bars">
                        {poll.results.map((opt) => (
                          <div
                            key={opt.index}
                            className={`poll-bar ${poll.my_vote === opt.index ? 'poll-bar--mine' : ''}`}
                          >
                            <div className="poll-bar__label">
                              <span dir="auto">
                                {opt.label} {poll.my_vote === opt.index ? '✓ (Your vote)' : ''}
                              </span>
                              <strong>
                                {opt.votes} ({opt.percentage}%)
                              </strong>
                            </div>
                            <div className="poll-bar__track">
                              <div
                                className="poll-bar__fill"
                                style={{ width: `${opt.percentage}%` }}
                              />
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </section>
                )
              })}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
