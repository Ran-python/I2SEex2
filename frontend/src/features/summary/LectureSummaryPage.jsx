import { useEffect, useRef, useState } from 'react'
import { api, getStoredLecturerPin } from '../../api/client.js'
import { ROLES, useRole } from '../../context/RoleContext.jsx'

export default function LectureSummaryPage() {
  const { isLecturer, setRole, setAiSettingsOpen } = useRole()
  const fileInputRef = useRef(null)

  const [courses, setCourses] = useState([])
  const [selectedCourseId, setSelectedCourseId] = useState(null)
  const [lessons, setLessons] = useState([])
  const [selectedLessonId, setSelectedLessonId] = useState('')
  const [transcript, setTranscript] = useState('')
  const [selectedFile, setSelectedFile] = useState(null)

  const [summaries, setSummaries] = useState([])
  const [activeSummary, setActiveSummary] = useState(null)
  const [generating, setGenerating] = useState(false)
  const [banner, setBanner] = useState(null)

  useEffect(() => {
    if (!isLecturer) return
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
  }, [isLecturer, selectedCourseId])

  useEffect(() => {
    if (!isLecturer || !selectedCourseId) return
    api
      .get(`/api/attendance/lessons?course_id=${selectedCourseId}`)
      .then((list) => {
        if (Array.isArray(list)) setLessons(list)
      })
      .catch(() => {})

    api
      .get(`/api/summaries?course_id=${selectedCourseId}`)
      .then((list) => {
        if (Array.isArray(list)) {
          setSummaries(list)
          setActiveSummary(list[0] || null)
        }
      })
      .catch(() => {})
  }, [isLecturer, selectedCourseId])

  // Strict Role Restriction: Hidden & blocked from Student View
  if (!isLecturer) {
    return (
      <section className="coming-soon card">
        <div className="coming-soon__icon">🔒</div>
        <h1>Lecturer Only — ML Lecture Summary & Pedagogical Evaluation</h1>
        <p className="muted">
          This module evaluates teaching delivery and lecture recordings and is strictly restricted to Lecturer Mode.
        </p>
        <button
          type="button"
          className="btn btn--primary"
          onClick={() => setRole(ROLES.LECTURER)}
        >
          🔒 Unlock Lecturer View (PIN Required)
        </button>
      </section>
    )
  }

  const handleGenerate = async (event) => {
    event.preventDefault()
    if (!selectedFile && !transcript.trim()) {
      setBanner({
        tone: 'danger',
        text: 'Please upload a lecture video/audio/transcript file or paste a Zoom/Meet transcript.',
      })
      return
    }
    setGenerating(true)
    setBanner(null)
    try {
      const form = new FormData()
      if (selectedCourseId) form.append('course_id', String(selectedCourseId))
      if (selectedLessonId) form.append('lesson_id', String(selectedLessonId))
      if (transcript.trim()) form.append('transcript', transcript.trim())
      if (selectedFile) form.append('file', selectedFile)

      const created = await api.uploadForm('/api/summaries/generate', form)
      setSummaries((prev) => [created, ...prev])
      setActiveSummary(created)
      setSelectedFile(null)
      if (fileInputRef.current) fileInputRef.current.value = ''
      setBanner({
        tone: 'success',
        text: `🧠 Generated pedagogical evaluation report using ${created.ai_provider}.`,
      })
    } catch (err) {
      setBanner({ tone: 'danger', text: err.message })
    } finally {
      setGenerating(false)
    }
  }

  const handleDownload = async (summary, fmt) => {
    try {
      const pin = getStoredLecturerPin()
      const resp = await fetch(`/api/summaries/${summary.id}/export?fmt=${fmt}&pin=${encodeURIComponent(pin)}`)
      if (!resp.ok) throw new Error('Download failed')
      const blob = await resp.blob()
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = `lecture_evaluation_${summary.id}.${fmt}`
      document.body.appendChild(link)
      link.click()
      link.remove()
      window.setTimeout(() => URL.revokeObjectURL(url), 5000)
    } catch (err) {
      setBanner({ tone: 'danger', text: err.message })
    }
  }

  const handleDelete = async (summaryId) => {
    try {
      await api.del(`/api/summaries/${summaryId}`)
      const remaining = summaries.filter((s) => s.id !== summaryId)
      setSummaries(remaining)
      if (activeSummary?.id === summaryId) {
        setActiveSummary(remaining[0] || null)
      }
    } catch (err) {
      setBanner({ tone: 'danger', text: err.message })
    }
  }

  return (
    <div className="dashboard">
      <section className="card context-bar">
        <div className="context-bar__row">
          <div className="context-group">
            <label className="context-group__label" htmlFor="summary-course-select">
              📚 Course
            </label>
            <select
              id="summary-course-select"
              className="select-input select-input--strong"
              value={selectedCourseId || ''}
              onChange={(e) => {
                setSelectedCourseId(Number(e.target.value))
                setSelectedLessonId('')
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
            <label className="context-group__label" htmlFor="summary-history-select">
              🗂️ Saved Reports ({summaries.length})
            </label>
            <select
              id="summary-history-select"
              className="select-input"
              value={activeSummary?.id || ''}
              onChange={(e) => {
                const found = summaries.find((s) => s.id === Number(e.target.value))
                setActiveSummary(found || null)
              }}
              disabled={summaries.length === 0}
              dir="auto"
            >
              {summaries.length === 0 ? (
                <option value="">No reports yet</option>
              ) : (
                summaries.map((s) => (
                  <option key={s.id} value={s.id}>
                    #{s.id} · {s.lesson_label} ({s.source_filename}) · {s.ai_provider}
                  </option>
                ))
              )}
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

      <div className="dashboard__grid">
        {/* Left: Upload Lecture Video / Transcript */}
        <section className="card">
          <div className="card__header">
            <div>
              <h2>🧠 Generate ML Lecture Summary & Teaching Evaluation</h2>
              <p className="muted">
                Upload a recorded lecture video (<code>.mp4</code>, <code>.mov</code>, <code>.webm</code>) or Zoom/Meet transcript to evaluate pedagogical quality.
              </p>
            </div>
            <span className="badge badge--info">Lecturer Only</span>
          </div>

          <form className="quick-form" onSubmit={handleGenerate}>
            <label className="field">
              <span>Associate with Lesson</span>
              <select
                className="select-input"
                value={selectedLessonId}
                onChange={(e) => setSelectedLessonId(e.target.value)}
                dir="auto"
              >
                <option value="">General Course Lecture</option>
                {lessons.map((l) => (
                  <option key={l.id} value={l.id}>
                    Lesson {l.lesson_number}: {l.title}
                  </option>
                ))}
              </select>
            </label>

            <div className="field">
              <span>Upload Lecture Video, Audio, or Transcript File (.mp4, .mov, .webm, .txt, .vtt)</span>
              <input
                ref={fileInputRef}
                type="file"
                accept="video/*,audio/*,.mp4,.mov,.webm,.mp3,.wav,.txt,.vtt,.srt,.md"
                onChange={(e) => setSelectedFile(e.target.files?.[0] || null)}
                hidden
              />
              <div className="btn-row" style={{ marginTop: '0.2rem' }}>
                <button
                  type="button"
                  className="btn btn--secondary"
                  onClick={() => fileInputRef.current?.click()}
                >
                  {selectedFile ? `🎬 ${selectedFile.name}` : '📂 Choose Video or Transcript File'}
                </button>
                {selectedFile && (
                  <button
                    type="button"
                    className="btn btn--ghost btn--sm"
                    onClick={() => {
                      setSelectedFile(null)
                      if (fileInputRef.current) fileInputRef.current.value = ''
                    }}
                  >
                    Remove
                  </button>
                )}
                <button
                  type="button"
                  className="btn btn--ghost btn--sm"
                  onClick={() => setAiSettingsOpen(true)}
                >
                  ⚙️ AI Settings
                </button>
              </div>
            </div>

            <label className="field">
              <span>
                Zoom / Meet Transcript or Lecture Notes <span className="optional">(or paste here)</span>
              </span>
              <textarea
                className="textarea-input"
                rows={7}
                placeholder="Paste the lecture transcript, auto-captions from Zoom/Google Meet, or lecture outline here…"
                value={transcript}
                onChange={(e) => setTranscript(e.target.value)}
                dir="auto"
              />
            </label>

            <button type="submit" className="btn btn--primary btn--lg btn--block" disabled={generating}>
              {generating ? '🤖 Analyzing Lecture & Evaluating Pedagogy…' : '🧠 Generate Pedagogical Evaluation Report'}
            </button>
          </form>
        </section>

        {/* Right: Structured 5-Section Pedagogical Report */}
        <section className="card">
          {!activeSummary ? (
            <div className="empty-state">
              <p>No lecture evaluation report selected.</p>
              <p className="muted">
                Upload a lecture video or paste a Zoom transcript on the left to generate a structured pedagogical report.
              </p>
            </div>
          ) : (
            <div className="evaluation-report">
              <div className="card__header">
                <div>
                  <span className="badge badge--info">{activeSummary.ai_provider}</span>
                  <h2 style={{ marginTop: '0.35rem' }} dir="auto">
                    {activeSummary.course_name} — {activeSummary.lesson_label}
                  </h2>
                  <p className="muted">Source: {activeSummary.source_filename}</p>
                </div>
                <div className="row-actions">
                  <button
                    type="button"
                    className="btn btn--secondary btn--sm"
                    onClick={() => handleDownload(activeSummary, 'docx')}
                  >
                    ⬇ Download .docx
                  </button>
                  <button
                    type="button"
                    className="btn btn--secondary btn--sm"
                    onClick={() => handleDownload(activeSummary, 'md')}
                  >
                    ⬇ Download .md
                  </button>
                  <button
                    type="button"
                    className="btn btn--ghost btn--sm"
                    onClick={() => handleDelete(activeSummary.id)}
                  >
                    🗑️
                  </button>
                </div>
              </div>

              <div className="report-section">
                <h3>1. Executive Lecture Summary & Key Topics Covered</h3>
                <div className="report-section__content" dir="auto">
                  {activeSummary.executive_summary}
                </div>
              </div>

              <div className="report-section">
                <h3>2. Pedagogical & Delivery Evaluation Parameters</h3>
                <div className="report-section__content" dir="auto">
                  {activeSummary.pedagogical_evaluation}
                </div>
              </div>

              <div className="report-section report-section--preserve">
                <h3>3. Points to Preserve (נקודות לשימור)</h3>
                <div className="report-section__content" dir="auto">
                  {activeSummary.points_to_preserve}
                </div>
              </div>

              <div className="report-section report-section--improve">
                <h3>4. Points for Improvement (נקודות לשיפור)</h3>
                <div className="report-section__content" dir="auto">
                  {activeSummary.points_for_improvement}
                </div>
              </div>

              <div className="report-section">
                <h3>5. Action Items / Topics to Revisit Next Class</h3>
                <div className="report-section__content" dir="auto">
                  {activeSummary.action_items}
                </div>
              </div>
            </div>
          )}
        </section>
      </div>
    </div>
  )
}
