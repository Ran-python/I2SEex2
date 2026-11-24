import { useEffect, useRef, useState } from 'react'
import { api, getStoredAiSettings, getStoredLecturerPin } from '../../api/client.js'
import { ROLES, useRole } from '../../context/RoleContext.jsx'

export default function LectureSummaryPage() {
  const { isLecturer, setRole, setAiSettingsOpen } = useRole()
  const fileInputRef = useRef(null)

  const [courses, setCourses] = useState([])
  const [selectedCourseId, setSelectedCourseId] = useState(null)
  const [lessons, setLessons] = useState([])
  const [selectedLessonId, setSelectedLessonId] = useState('')
  const [recordingLanguage, setRecordingLanguage] = useState('auto')
  const [youtubeUrl, setYoutubeUrl] = useState('')
  const [transcript, setTranscript] = useState('')
  const [selectedFile, setSelectedFile] = useState(null)

  const [summaries, setSummaries] = useState([])
  const [activeSummary, setActiveSummary] = useState(null)
  const [generating, setGenerating] = useState(false)
  const [statusStep, setStatusStep] = useState('')
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
    if (!youtubeUrl.trim() && !selectedFile && !transcript.trim()) {
      setBanner({
        tone: 'danger',
        text: 'Please enter a YouTube URL, upload a lecture video/audio file (.mp4, .mov, .webm), or paste a transcript.',
      })
      return
    }
    setGenerating(true)
    setBanner(null)

    if (youtubeUrl.trim()) {
      setStatusStep('📺 Extracting YouTube transcript & generating AI lecture evaluation…')
    } else if (selectedFile) {
      setStatusStep(
        `🎵 Uploading "${selectedFile.name}" for FFmpeg 16 kHz mono audio extraction & speech transcription…`,
      )
    } else {
      setStatusStep('🤖 Analyzing lecture transcript & evaluating pedagogy…')
    }

    try {
      const ai = getStoredAiSettings()
      const isMediaUpload = Boolean(
        selectedFile && !/\.(txt|vtt|srt|md|csv)$/i.test(selectedFile.name || ''),
      )
      const form = new FormData()
      if (selectedCourseId) form.append('course_id', String(selectedCourseId))
      if (selectedLessonId) form.append('lesson_id', String(selectedLessonId))
      if (recordingLanguage && recordingLanguage !== 'auto') {
        form.append('language', recordingLanguage)
      }
      if (isMediaUpload) {
        form.append('async_job', 'true')
      }
      if (youtubeUrl.trim()) form.append('youtube_url', youtubeUrl.trim())
      if (transcript.trim()) form.append('transcript', transcript.trim())
      if (ai.provider) form.append('provider', ai.provider)
      if (ai.apiKey) form.append('api_key', ai.apiKey)
      if (ai.model) form.append('model', ai.model)
      if (selectedFile) form.append('file', selectedFile)

      let created = await api.uploadForm('/api/summaries/generate', form)

      if (created?.job_id && created.status !== 'completed') {
        const jobId = created.job_id
        if (created.stage) setStatusStep(created.stage)
        while (true) {
          await new Promise((resolve) => window.setTimeout(resolve, 2000))
          const job = await api.get(`/api/summaries/jobs/${encodeURIComponent(jobId)}`)
          if (job.stage) setStatusStep(job.stage)
          if (job.status === 'completed' && job.summary) {
            created = job.summary
            break
          }
          if (job.status === 'failed') {
            throw new Error(job.detail || 'Lecture audio processing failed.')
          }
        }
      }

      setSummaries((prev) => [created, ...prev])
      setActiveSummary(created)
      setSelectedFile(null)
      setYoutubeUrl('')
      if (fileInputRef.current) fileInputRef.current.value = ''
      setBanner({
        tone: 'success',
        text: `🧠 Generated pedagogical evaluation report for "${created.source_filename}" using ${created.ai_provider}.`,
      })
    } catch (err) {
      setBanner({ tone: 'danger', text: err.message })
    } finally {
      setGenerating(false)
      setStatusStep('')
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
        <div className={`notice notice--${banner.tone} notice--dismissible`} role="alert">
          <span>{banner.text}</span>
          <button type="button" className="notice__close" onClick={() => setBanner(null)}>
            ✕
          </button>
        </div>
      )}

      <div className="dashboard__grid">
        {/* Left: YouTube URL / Upload Lecture Video / Transcript */}
        <section className="card">
          <div className="card__header">
            <div>
              <h2>🧠 Generate ML Lecture Summary & Teaching Evaluation</h2>
              <p className="muted">
                Summarize any YouTube lecture URL, upload a recorded video file (<code>.mp4</code>, <code>.mov</code>, <code>.webm</code>), or paste a Zoom/Meet transcript.
              </p>
            </div>
            <span className="badge badge--info">Lecturer Only</span>
          </div>

          <form className="quick-form" onSubmit={handleGenerate}>
            <div className="quick-form__grid" style={{ gridTemplateColumns: '1fr 1fr' }}>
              <label className="field">
                <span>Associate with Lesson</span>
                <select
                  className="select-input"
                  value={selectedLessonId}
                  onChange={(e) => setSelectedLessonId(e.target.value)}
                  disabled={generating}
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

              <label className="field">
                <span>Recording Language</span>
                <select
                  className="select-input"
                  value={recordingLanguage}
                  onChange={(e) => setRecordingLanguage(e.target.value)}
                  disabled={generating}
                  dir="auto"
                >
                  <option value="auto">🌐 Auto-detect (עברית / English)</option>
                  <option value="he">🇮🇱 עברית (Hebrew — he)</option>
                  <option value="en">🇺🇸 English (en)</option>
                </select>
              </label>
            </div>

            <label className="field">
              <span>
                📺 Option 1: YouTube Lecture URL <span className="optional">(fast transcript extraction via youtube-transcript-api)</span>
              </span>
              <input
                type="url"
                placeholder="https://www.youtube.com/watch?v=... or https://youtu.be/..."
                value={youtubeUrl}
                onChange={(e) => setYoutubeUrl(e.target.value)}
                disabled={generating}
              />
            </label>

            <div className="field">
              <span>
                🎬 Option 2: Upload Video, Audio, or Transcript File (<code>.mp4</code>, <code>.mov</code>, <code>.webm</code>, <code>.txt</code>, <code>.vtt</code>)
              </span>
              <input
                ref={fileInputRef}
                type="file"
                accept="video/*,audio/*,.mp4,.mov,.webm,.mkv,.mp3,.wav,.txt,.vtt,.srt,.md"
                onChange={(e) => setSelectedFile(e.target.files?.[0] || null)}
                hidden
              />
              <div className="btn-row" style={{ marginTop: '0.2rem' }}>
                <button
                  type="button"
                  className="btn btn--secondary"
                  onClick={() => fileInputRef.current?.click()}
                  disabled={generating}
                >
                  {selectedFile
                    ? `🎬 ${selectedFile.name} (${(selectedFile.size / (1024 * 1024)).toFixed(1)} MB)`
                    : '📂 Choose Video or Transcript File'}
                </button>
                {selectedFile && (
                  <button
                    type="button"
                    className="btn btn--ghost btn--sm"
                    disabled={generating}
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
                📝 Option 3: Zoom / Meet Transcript or Lecture Notes <span className="optional">(or paste here)</span>
              </span>
              <textarea
                className="textarea-input"
                rows={6}
                placeholder="Paste the lecture transcript, auto-captions from Zoom/Google Meet, or additional instructions here…"
                value={transcript}
                onChange={(e) => setTranscript(e.target.value)}
                disabled={generating}
                dir="auto"
              />
            </label>

            {generating && statusStep && (
              <div className="notice notice--info" role="status">
                {statusStep}
              </div>
            )}

            <button type="submit" className="btn btn--primary btn--lg btn--block" disabled={generating}>
              {generating ? '⏳ Processing Lecture & Generating Report…' : '🧠 Generate Pedagogical Evaluation Report'}
            </button>
          </form>
        </section>

        {/* Right: Structured 5-Section Pedagogical Report */}
        <section className="card">
          {!activeSummary ? (
            <div className="empty-state">
              <p>No lecture evaluation report selected.</p>
              <p className="muted">
                Enter a YouTube link, upload a lecture video, or paste a Zoom transcript on the left to generate a structured pedagogical report.
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
                    onClick={() => handleDownload(activeSummary, 'pdf')}
                  >
                    ⬇ Download .pdf
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
                <h3>1. Overview, Executive Summary & Key Definitions</h3>
                <div className="report-section__content" dir="auto">
                  {activeSummary.executive_summary}
                </div>
              </div>

              <div className="report-section">
                <h3>2. Detailed Breakdown & Pedagogical Evaluation Parameters</h3>
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
                <h3>5. Review Questions & Action Items for Next Class</h3>
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
