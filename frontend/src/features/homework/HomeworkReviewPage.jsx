import { useEffect, useRef, useState } from 'react'
import { api } from '../../api/client.js'
import { useRole } from '../../context/RoleContext.jsx'

export default function HomeworkReviewPage() {
  const { isLecturer, studentIdentity, setStudentIdentity, setAiSettingsOpen } = useRole()
  const fileInputRef = useRef(null)

  const [courses, setCourses] = useState([])
  const [selectedCourseId, setSelectedCourseId] = useState(studentIdentity?.courseId || null)
  const [assignmentName, setAssignmentName] = useState('Ex2')
  const [studentName, setStudentName] = useState(studentIdentity?.studentName || '')
  const [githubUrl, setGithubUrl] = useState('')
  const [codeText, setCodeText] = useState('')
  const [selectedFile, setSelectedFile] = useState(null)

  const [data, setData] = useState(null)
  const [activeSubmission, setActiveSubmission] = useState(null)
  const [comparisonReport, setComparisonReport] = useState(null)
  const [busyAction, setBusyAction] = useState(null)
  const [banner, setBanner] = useState(null)

  useEffect(() => {
    if (studentIdentity?.studentName && !isLecturer) {
      setStudentName(studentIdentity.studentName)
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

  const loadSubmissions = async () => {
    if (!selectedCourseId) return
    const params = new URLSearchParams()
    params.set('course_id', String(selectedCourseId))
    if (assignmentName.trim()) params.set('assignment_name', assignmentName.trim())
    params.set('view_role', isLecturer ? 'lecturer' : 'student')
    if (studentIdentity?.studentId) params.set('student_id', String(studentIdentity.studentId))
    if (studentName.trim()) params.set('student_name', studentName.trim())

    try {
      const res = await api.get(`/api/homework/submissions?${params.toString()}`)
      setData(res)
      if (res.submissions?.length > 0) {
        setActiveSubmission((prev) => res.submissions.find((s) => s.id === prev?.id) || res.submissions[0])
      } else {
        setActiveSubmission(null)
      }
    } catch {
      /* ignore */
    }
  }

  useEffect(() => {
    loadSubmissions()
  }, [selectedCourseId, assignmentName, isLecturer, studentIdentity?.studentId, studentName])

  const handleSubmit = async (event) => {
    event.preventDefault()
    if (!studentName.trim()) {
      setBanner({ tone: 'danger', text: 'Please enter the student name.' })
      return
    }
    if (!selectedFile && !githubUrl.trim() && !codeText.trim()) {
      setBanner({
        tone: 'danger',
        text: 'Please upload a code/ZIP/PDF file, enter a GitHub repository URL, or paste your code.',
      })
      return
    }

    setBusyAction('submit')
    setBanner(null)
    try {
      const form = new FormData()
      if (selectedCourseId) form.append('course_id', String(selectedCourseId))
      form.append('assignment_name', assignmentName.trim() || 'Ex2')
      form.append('student_name', studentName.trim())
      if (studentIdentity?.studentId) form.append('student_id', String(studentIdentity.studentId))
      if (githubUrl.trim()) form.append('github_url', githubUrl.trim())
      if (codeText.trim()) form.append('code_text', codeText.trim())
      if (selectedFile) form.append('file', selectedFile)

      const created = await api.uploadForm('/api/homework/submit', form)
      setSelectedFile(null)
      setGithubUrl('')
      setCodeText('')
      if (fileInputRef.current) fileInputRef.current.value = ''
      setActiveSubmission(created)
      await loadSubmissions()
      setBanner({
        tone: 'success',
        text: `📝 Deep LLM review completed for ${created.student_name} (${created.assignment_name})!`,
      })
    } catch (err) {
      setBanner({ tone: 'danger', text: err.message })
    } finally {
      setBusyAction(null)
    }
  }

  const handleRunComparisonMatrix = async () => {
    setBusyAction('compare')
    setBanner(null)
    try {
      const res = await api.post('/api/homework/compare', {
        course_id: selectedCourseId,
        assignment_name: assignmentName.trim() || 'Ex2',
      })
      setComparisonReport(res)
      setBanner({
        tone: 'success',
        text: `📊 Generated Cross-Student Comparison Matrix across ${res.submission_count} submissions.`,
      })
    } catch (err) {
      setBanner({ tone: 'danger', text: err.message })
    } finally {
      setBusyAction(null)
    }
  }

  const handleDelete = async (submissionId) => {
    try {
      await api.del(`/api/homework/submissions/${submissionId}`)
      await loadSubmissions()
    } catch (err) {
      setBanner({ tone: 'danger', text: err.message })
    }
  }

  const submissions = data?.submissions || []
  const classStats = data?.class_stats || { total_submissions: 0, average_score: null }

  return (
    <div className="dashboard">
      {/* Top Course & Assignment Bar */}
      <section className="card context-bar">
        <div className="context-bar__row">
          <div className="context-group">
            <label className="context-group__label" htmlFor="hw-course-select">
              📚 Course
            </label>
            <select
              id="hw-course-select"
              className="select-input select-input--strong"
              value={selectedCourseId || ''}
              onChange={(e) => {
                const cid = Number(e.target.value)
                const found = courses.find((c) => c.id === cid)
                setSelectedCourseId(cid)
                setComparisonReport(null)
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
            <label className="context-group__label" htmlFor="hw-assignment-input">
              📝 Assignment Name
            </label>
            <div className="context-group__controls">
              <input
                id="hw-assignment-input"
                type="text"
                className="select-input"
                placeholder="e.g. Ex1, Ex2, Final Project"
                value={assignmentName}
                onChange={(e) => {
                  setAssignmentName(e.target.value)
                  setComparisonReport(null)
                }}
              />
              {data?.assignments?.length > 0 &&
                data.assignments.map((a) => (
                  <button
                    key={a}
                    type="button"
                    className={`filter-pill ${assignmentName === a ? 'is-active' : ''}`}
                    onClick={() => {
                      setAssignmentName(a)
                      setComparisonReport(null)
                    }}
                  >
                    {a}
                  </button>
                ))}
            </div>
          </div>

          <div className="context-group">
            <span className="context-group__label">📊 Class Assignment Stats</span>
            <div className="context-group__controls">
              <span className="badge badge--info">
                {classStats.total_submissions} Submissions in Class
              </span>
              {classStats.average_score !== null && (
                <span className="badge badge--success">
                  Class Avg: {classStats.average_score}/100
                </span>
              )}
              {isLecturer && (
                <button
                  type="button"
                  className="btn btn--primary btn--sm"
                  disabled={busyAction === 'compare' || classStats.total_submissions < 2}
                  onClick={handleRunComparisonMatrix}
                  title="Compare all student submissions for this assignment"
                >
                  {busyAction === 'compare'
                    ? '⏳ Comparing Class…'
                    : '📊 Cross-Student Comparison Matrix'}
                </button>
              )}
            </div>
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

      {/* Lecturer Cross-Student Comparison Matrix Report */}
      {isLecturer && comparisonReport && (
        <section className="card">
          <div className="card__header">
            <div>
              <span className="badge badge--info">{comparisonReport.ai_model}</span>
              <h2>
                📊 Cross-Student Comparison Matrix — {comparisonReport.course_name} ({comparisonReport.assignment_name})
              </h2>
              <p className="muted">
                Comparing {comparisonReport.submission_count} student submissions (Architecture, Standout Implementations, Common Pitfalls & Similarity Check).
              </p>
            </div>
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              onClick={() => setComparisonReport(null)}
            >
              Close Matrix
            </button>
          </div>
          <div className="report-section__content" dir="auto">
            {comparisonReport.comparison_markdown}
          </div>
        </section>
      )}

      <div className="dashboard__grid">
        {/* Left: Submit Homework for Deep LLM Review */}
        <section className="card">
          <div className="card__header">
            <div>
              <h2>📝 Submit Homework for Deep LLM Review</h2>
              <p className="muted">
                Upload code/project files (<code>.py</code>, <code>.java</code>, <code>.c</code>, <code>.cpp</code>,{' '}
                <code>.js</code>, <code>.zip</code>, <code>.pdf</code>) or enter a public GitHub repo URL for deep OOP review & peer comparison.
              </p>
            </div>
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              onClick={() => setAiSettingsOpen(true)}
            >
              ⚙️ AI Key
            </button>
          </div>

          <form className="quick-form" onSubmit={handleSubmit}>
            <div className="quick-form__grid" style={{ gridTemplateColumns: '1fr 1fr' }}>
              <label className="field">
                <span>
                  Student Name <span className="required">*</span>
                </span>
                <input
                  type="text"
                  placeholder="Dana Cohen / דנה כהן"
                  value={studentName}
                  onChange={(e) => setStudentName(e.target.value)}
                  required
                  dir="auto"
                />
              </label>

              <label className="field">
                <span>
                  Assignment <span className="required">*</span>
                </span>
                <input
                  type="text"
                  placeholder="Ex2"
                  value={assignmentName}
                  onChange={(e) => setAssignmentName(e.target.value)}
                  required
                />
              </label>
            </div>

            <label className="field">
              <span>
                GitHub Repository URL <span className="optional">(optional — public repo)</span>
              </span>
              <input
                type="url"
                placeholder="https://github.com/username/oop-ex2"
                value={githubUrl}
                onChange={(e) => setGithubUrl(e.target.value)}
              />
            </label>

            <div className="field">
              <span>Or Upload Code / Archive File (.py, .java, .cpp, .js, .zip, .pdf, .txt)</span>
              <input
                ref={fileInputRef}
                type="file"
                accept=".py,.java,.c,.cpp,.h,.hpp,.cs,.js,.jsx,.ts,.tsx,.zip,.pdf,.txt,.md"
                onChange={(e) => setSelectedFile(e.target.files?.[0] || null)}
                hidden
              />
              <div className="btn-row" style={{ marginTop: '0.2rem' }}>
                <button
                  type="button"
                  className="btn btn--secondary"
                  onClick={() => fileInputRef.current?.click()}
                >
                  {selectedFile ? `📎 ${selectedFile.name}` : '📂 Choose Code or .ZIP File'}
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
              </div>
            </div>

            <label className="field">
              <span>
                Or Paste Source Code / Solution <span className="optional">(optional)</span>
              </span>
              <textarea
                className="textarea-input"
                rows={6}
                placeholder="Paste your source code or design explanation here…"
                value={codeText}
                onChange={(e) => setCodeText(e.target.value)}
              />
            </label>

            <button
              type="submit"
              className="btn btn--primary btn--lg btn--block"
              disabled={busyAction === 'submit'}
            >
              {busyAction === 'submit'
                ? '🤖 Running Deep LLM Review & Peer Comparison…'
                : '🔍 Submit & Run Deep LLM Review'}
            </button>
          </form>
        </section>

        {/* Right: Review Results & Submissions Selector */}
        <section className="card">
          <div className="card__header">
            <div>
              <h2>
                {isLecturer
                  ? `All Class Submissions (${submissions.length})`
                  : `Your Submission Reviews (${submissions.length})`}
              </h2>
              <p className="muted">
                {isLecturer
                  ? 'Lecturer View: Select any student submission to inspect their code and technical audit.'
                  : 'Student View: Deep OOP review and anonymized comparison against class patterns.'}
              </p>
            </div>
          </div>

          {submissions.length > 0 && (
            <div className="lesson-strip" style={{ marginBottom: '0.9rem' }}>
              {submissions.map((sub) => (
                <button
                  key={sub.id}
                  type="button"
                  className={`lesson-pill ${activeSubmission?.id === sub.id ? 'is-selected' : ''}`}
                  onClick={() => setActiveSubmission(sub)}
                >
                  <span dir="auto">{sub.student_name}</span>
                  <span className="lesson-pill__count">{sub.assignment_name}</span>
                  {sub.score_estimate !== null && (
                    <span className="lesson-pill__count">{sub.score_estimate}/100</span>
                  )}
                </button>
              ))}
            </div>
          )}

          {!activeSubmission ? (
            <div className="empty-state">
              <p>No homework review to display yet.</p>
              <p className="muted">
                Submit a file, GitHub repository URL, or code snippet on the left to receive an instant LLM review.
              </p>
            </div>
          ) : (
            <div className="evaluation-report">
              <div className="card__header">
                <div>
                  <div className="comment-card__badges">
                    <span className="badge badge--info">{activeSubmission.assignment_name}</span>
                    {activeSubmission.score_estimate !== null && (
                      <span className="badge badge--success">
                        Estimated Score: {activeSubmission.score_estimate}/100
                      </span>
                    )}
                    {activeSubmission.design_metrics?.architecture_pattern && (
                      <span className="badge badge--muted">
                        🏗️ {activeSubmission.design_metrics.architecture_pattern}
                      </span>
                    )}
                    {activeSubmission.design_metrics?.peers_compared !== undefined && (
                      <span className="badge badge--warning">
                        Compared against {activeSubmission.design_metrics.peers_compared} class peer(s)
                      </span>
                    )}
                  </div>
                  <h3 style={{ marginTop: '0.45rem' }} dir="auto">
                    {activeSubmission.student_name} — <code>{activeSubmission.source_label}</code>
                  </h3>
                </div>
                {isLecturer && (
                  <button
                    type="button"
                    className="btn btn--ghost btn--sm"
                    onClick={() => handleDelete(activeSubmission.id)}
                  >
                    🗑️
                  </button>
                )}
              </div>

              {activeSubmission.design_metrics?.strengths?.length > 0 && (
                <div className="report-section report-section--preserve">
                  <h3>Key Strengths</h3>
                  <ul>
                    {activeSubmission.design_metrics.strengths.map((item, i) => (
                      <li key={i} dir="auto">{item}</li>
                    ))}
                  </ul>
                </div>
              )}

              {activeSubmission.design_metrics?.improvements?.length > 0 && (
                <div className="report-section report-section--improve">
                  <h3>Recommended Improvements</h3>
                  <ul>
                    {activeSubmission.design_metrics.improvements.map((item, i) => (
                      <li key={i} dir="auto">{item}</li>
                    ))}
                  </ul>
                </div>
              )}

              <div className="report-section">
                <h3>Deep Review & Anonymized Class Comparison</h3>
                <div className="report-section__content" dir="auto">
                  {activeSubmission.student_review_md}
                </div>
              </div>

              {isLecturer && activeSubmission.lecturer_audit_md && (
                <div className="report-section">
                  <h3>👩‍🏫 Lecturer-Only Technical Audit & Peer Comparison</h3>
                  <div className="report-section__content" dir="auto">
                    {activeSubmission.lecturer_audit_md}
                  </div>
                </div>
              )}
            </div>
          )}
        </section>
      </div>
    </div>
  )
}
