import { useEffect, useState } from 'react'
import { api } from '../../api/client.js'
import { useRole } from '../../context/RoleContext.jsx'
import { usePolling } from '../../hooks/usePolling.js'

export default function CommentsPage() {
  const { isLecturer, studentIdentity, setStudentIdentity } = useRole()
  const [courses, setCourses] = useState([])
  const [selectedCourseId, setSelectedCourseId] = useState(studentIdentity?.courseId || null)
  const [lessons, setLessons] = useState([])
  const [scope, setScope] = useState('all') // all | general | lesson | private
  const [filterLessonId, setFilterLessonId] = useState('')

  // New comment form
  const [contextLessonId, setContextLessonId] = useState('') // '' = General Course Comment
  const [authorName, setAuthorName] = useState(studentIdentity?.studentName || '')
  const [isAnonymous, setIsAnonymous] = useState(false)
  const [visibility, setVisibility] = useState('public') // public | private
  const [content, setContent] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [banner, setBanner] = useState(null)

  // Lecturer reply draft state
  const [replyingId, setReplyingId] = useState(null)
  const [replyDraft, setReplyDraft] = useState('')

  useEffect(() => {
    if (!isLecturer) {
      setAuthorName(studentIdentity?.studentName || '')
    }
    if (studentIdentity?.courseId && !isLecturer) {
      setSelectedCourseId(studentIdentity.courseId)
    }
  }, [studentIdentity?.studentName, studentIdentity?.courseId, isLecturer])

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

  useEffect(() => {
    if (!selectedCourseId) return
    api
      .get(`/api/attendance/lessons?course_id=${selectedCourseId}`)
      .then((list) => {
        if (!Array.isArray(list)) return
        setLessons(list)
      })
      .catch(() => {})
  }, [selectedCourseId])

  const buildCommentsUrl = () => {
    const params = new URLSearchParams()
    if (selectedCourseId) params.set('course_id', String(selectedCourseId))
    params.set('scope', scope)
    params.set('view_role', isLecturer ? 'lecturer' : 'student')
    if (scope === 'lesson' && filterLessonId) {
      params.set('lesson_id', String(filterLessonId))
    }
    if (studentIdentity?.studentId) {
      params.set('student_id', String(studentIdentity.studentId))
    }
    return `/api/comments?${params.toString()}`
  }

  const { data: comments, loading, refresh } = usePolling(() => api.get(buildCommentsUrl()), 4000)

  useEffect(() => {
    refresh()
  }, [selectedCourseId, scope, filterLessonId, isLecturer, studentIdentity?.studentId, refresh])

  const handleSubmit = async (event) => {
    event.preventDefault()
    if (!content.trim()) return
    setSubmitting(true)
    setBanner(null)
    try {
      await api.post('/api/comments', {
        course_id: selectedCourseId,
        lesson_id: contextLessonId ? Number(contextLessonId) : null,
        student_id: studentIdentity?.studentId || null,
        author_name: isLecturer ? 'Lecturer' : authorName.trim() || studentIdentity?.studentName || 'Student',
        is_anonymous: isAnonymous,
        visibility,
        content: content.trim(),
      })
      setContent('')
      await refresh()
      setBanner({
        tone: 'success',
        text:
          visibility === 'private'
            ? '🔒 Your private message was sent directly to the lecturer.'
            : '💬 Your comment has been posted to the course feed.',
      })
    } catch (err) {
      setBanner({ tone: 'danger', text: err.message })
    } finally {
      setSubmitting(false)
    }
  }

  const handleReplySave = async (commentId) => {
    try {
      await api.patch(`/api/comments/${commentId}`, {
        lecturer_reply: replyDraft.trim(),
      })
      setReplyingId(null)
      setReplyDraft('')
      await refresh()
    } catch (err) {
      setBanner({ tone: 'danger', text: err.message })
    }
  }

  const handleToggleResolved = async (comment) => {
    try {
      await api.patch(`/api/comments/${comment.id}`, {
        resolved: !comment.resolved,
      })
      await refresh()
    } catch (err) {
      setBanner({ tone: 'danger', text: err.message })
    }
  }

  const handleDelete = async (commentId) => {
    try {
      await api.del(`/api/comments/${commentId}`)
      await refresh()
    } catch (err) {
      setBanner({ tone: 'danger', text: err.message })
    }
  }

  const commentList = Array.isArray(comments) ? comments : []

  return (
    <div className="dashboard">
      {/* Top Course & Context Filter Bar */}
      <section className="card context-bar">
        <div className="context-bar__row">
          <div className="context-group">
            <label className="context-group__label" htmlFor="comments-course-select">
              📚 Course
            </label>
            <select
              id="comments-course-select"
              className="select-input select-input--strong"
              value={selectedCourseId || ''}
              onChange={(e) => {
                const cid = Number(e.target.value)
                const found = courses.find((c) => c.id === cid)
                setSelectedCourseId(cid)
                setFilterLessonId('')
                setContextLessonId('')
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

          <div className="context-group">
            <span className="context-group__label">🔍 Filter Comments</span>
            <div className="filter-pills">
              {[
                { key: 'all', label: 'All Comments' },
                { key: 'general', label: 'General Course' },
                { key: 'lesson', label: 'By Lesson' },
                ...(isLecturer ? [{ key: 'private', label: '🔒 Private to Lecturer' }] : []),
              ].map((tab) => (
                <button
                  key={tab.key}
                  type="button"
                  className={`filter-pill ${scope === tab.key ? 'is-active' : ''}`}
                  onClick={() => setScope(tab.key)}
                >
                  {tab.label}
                </button>
              ))}
            </div>
          </div>

          {scope === 'lesson' && (
            <div className="context-group">
              <label className="context-group__label" htmlFor="filter-lesson">
                Lesson
              </label>
              <select
                id="filter-lesson"
                className="select-input"
                value={filterLessonId}
                onChange={(e) => setFilterLessonId(e.target.value)}
              >
                <option value="">All Lessons</option>
                {lessons.map((l) => (
                  <option key={l.id} value={l.id}>
                    Lesson {l.lesson_number}: {l.title}
                  </option>
                ))}
              </select>
            </div>
          )}
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

      <div className="dashboard__grid">
        {/* Left: Post a New Comment / Suggestion */}
        <section className="card">
          <h2>💬 Raise a Comment or Suggestion</h2>
          <p className="muted">
            Ask a question during class or leave an offline suggestion tagged to a specific lesson or the general course.
          </p>

          <form className="quick-form" onSubmit={handleSubmit}>
            <label className="field">
              <span>Context (Specific Lesson or General Course)</span>
              <select
                className="select-input"
                value={contextLessonId}
                onChange={(e) => setContextLessonId(e.target.value)}
                dir="auto"
              >
                <option value="">📌 General Course Comment (Not tied to a specific lesson)</option>
                {lessons.map((l) => (
                  <option key={l.id} value={l.id}>
                    🗓️ Lesson {l.lesson_number}: {l.title}
                  </option>
                ))}
              </select>
            </label>

            {!isLecturer && (
              <div className="quick-form__grid" style={{ gridTemplateColumns: '1fr 1fr' }}>
                <label className="field">
                  <span>Your Name</span>
                  <input
                    type="text"
                    placeholder="Dana Cohen / דנה כהן"
                    value={isAnonymous ? 'Anonymous Student' : authorName}
                    onChange={(e) => setAuthorName(e.target.value)}
                    disabled={isAnonymous}
                    dir="auto"
                  />
                </label>

                <label className="field">
                  <span>Visibility</span>
                  <select
                    className="select-input"
                    value={visibility}
                    onChange={(e) => setVisibility(e.target.value)}
                  >
                    <option value="public">🌐 Public (Everyone in course)</option>
                    <option value="private">🔒 Private (Lecturer ONLY)</option>
                  </select>
                </label>
              </div>
            )}

            {!isLecturer && (
              <label className="checkbox-label">
                <input
                  type="checkbox"
                  checked={isAnonymous}
                  onChange={(e) => setIsAnonymous(e.target.checked)}
                />
                <span>🕶️ Post Anonymously (hide my name from the feed)</span>
              </label>
            )}

            <label className="field">
              <span>
                Your Comment / Question <span className="required">*</span>
              </span>
              <textarea
                className="textarea-input"
                rows={4}
                placeholder="Type your question, comment, or suggestion here…"
                value={content}
                onChange={(e) => setContent(e.target.value)}
                required
                dir="auto"
              />
            </label>

            <button type="submit" className="btn btn--primary btn--block" disabled={submitting}>
              {submitting ? 'Posting…' : visibility === 'private' ? '🔒 Send Private Note to Lecturer' : '💬 Post Comment'}
            </button>
          </form>
        </section>

        {/* Right: Live Comments Feed */}
        <section className="card">
          <div className="card__header">
            <div>
              <h2>Live & Offline Feed ({commentList.length})</h2>
              <p className="muted">
                {isLecturer
                  ? 'Lecturer View: Showing all public & private student comments.'
                  : 'Showing public class comments and your own private notes.'}
              </p>
            </div>
          </div>

          {loading && !comments ? (
            <p className="empty-state">Loading comments…</p>
          ) : commentList.length === 0 ? (
            <div className="empty-state">
              <p>No comments yet for this filter.</p>
              <p className="muted">Be the first to post a question or suggestion!</p>
            </div>
          ) : (
            <div className="comments-feed">
              {commentList.map((c) => (
                <article
                  key={c.id}
                  className={`comment-card ${c.visibility === 'private' ? 'comment-card--private' : ''} ${
                    c.resolved ? 'comment-card--resolved' : ''
                  }`}
                >
                  <div className="comment-card__header">
                    <div className="comment-card__badges">
                      <span className="badge badge--info" dir="auto">
                        {c.context_badge}
                      </span>
                      {c.visibility === 'private' ? (
                        <span className="badge badge--warning">🔒 Private to Lecturer</span>
                      ) : (
                        <span className="badge badge--muted">🌐 Public</span>
                      )}
                      {c.is_mine && <span className="badge badge--success">👤 Your Comment</span>}
                      {c.resolved && <span className="badge badge--success">✓ Resolved</span>}
                    </div>
                    <small className="muted">
                      {c.created_at ? new Date(c.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : ''}
                    </small>
                  </div>

                  <p className="comment-card__body" dir="auto">{c.content}</p>

                  <div className="comment-card__footer">
                    <span className="muted">
                      By{' '}
                      <strong>
                        {c.is_anonymous
                          ? c.is_mine
                            ? '🕶️ Anonymous Student (You)'
                            : '🕶️ Anonymous Student'
                          : c.is_mine
                            ? `${c.author_name} (You)`
                            : c.author_name}
                      </strong>
                    </span>

                    {isLecturer && (
                      <div className="row-actions">
                        <button
                          type="button"
                          className="btn btn--ghost btn--sm"
                          onClick={() => {
                            setReplyingId(replyingId === c.id ? null : c.id)
                            setReplyDraft(c.lecturer_reply || '')
                          }}
                        >
                          ↩ Reply
                        </button>
                        <button
                          type="button"
                          className="btn btn--ghost btn--sm"
                          onClick={() => handleToggleResolved(c)}
                        >
                          {c.resolved ? 'Re-open' : '✓ Resolve'}
                        </button>
                        <button
                          type="button"
                          className="btn btn--ghost btn--sm"
                          onClick={() => handleDelete(c.id)}
                          title="Delete comment"
                        >
                          🗑️
                        </button>
                      </div>
                    )}
                  </div>

                  {c.lecturer_reply && replyingId !== c.id && (
                    <div className="comment-card__reply" dir="auto">
                      <strong>👩‍🏫 Lecturer Reply:</strong> {c.lecturer_reply}
                    </div>
                  )}

                  {isLecturer && replyingId === c.id && (
                    <div className="context-subform" style={{ marginTop: '0.6rem' }}>
                      <input
                        type="text"
                        placeholder="Write lecturer reply…"
                        value={replyDraft}
                        onChange={(e) => setReplyDraft(e.target.value)}
                        dir="auto"
                        autoFocus
                      />
                      <button
                        type="button"
                        className="btn btn--primary btn--sm"
                        onClick={() => handleReplySave(c.id)}
                      >
                        Save Reply
                      </button>
                    </div>
                  )}
                </article>
              ))}
            </div>
          )}
        </section>
      </div>
    </div>
  )
}
