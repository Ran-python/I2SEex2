import { useEffect, useRef, useState } from 'react'
import { api } from '../../../api/client.js'
import { ROLES, useRole } from '../../../context/RoleContext.jsx'
import { formatClock, useCountdown } from '../../../hooks/useCountdown.js'
import { usePolling } from '../../../hooks/usePolling.js'
import { csvTextToXlsxBlob } from '../../../utils/xlsxFallback.js'

const STATUS_META = {
  present: { label: 'QR Check-in', badge: 'badge--success' },
  manual: { label: 'Manual (Lecturer)', badge: 'badge--info' },
  unlisted: { label: 'Joined via QR (Added to Roster)', badge: 'badge--warning' },
  absent: { label: '—', badge: 'badge--muted' },
}

function formatTime(isoString) {
  if (!isoString) return '—'
  try {
    return new Date(isoString).toLocaleTimeString([], {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    })
  } catch {
    return '—'
  }
}

function formatShortDate(isoString) {
  if (!isoString) return ''
  try {
    return new Date(isoString).toLocaleDateString([], {
      month: 'short',
      day: 'numeric',
    })
  } catch {
    return ''
  }
}

function formatLessonHeading(lesson) {
  if (!lesson) return 'No lessons yet'
  const base = `Lesson ${lesson.lesson_number}`
  if (!lesson.title || lesson.title.toLowerCase() === base.toLowerCase()) {
    return base
  }
  return `${base}: ${lesson.title}`
}

export default function LecturerDashboard() {
  const { setRole, setAiSettingsOpen } = useRole()
  const fileInputRef = useRef(null)
  const newCourseFileRef = useRef(null)
  const photoInputRef = useRef(null)

  const [selectedCourseId, setSelectedCourseId] = useState(null)
  const [selectedLessonId, setSelectedLessonId] = useState(null)
  const [manualHeadcount, setManualHeadcount] = useState('')

  // New Course modal/inline drawer state (with optional roster file upload)
  const [showNewCourse, setShowNewCourse] = useState(false)
  const [newCourseName, setNewCourseName] = useState('')
  const [newCourseFile, setNewCourseFile] = useState(null)

  // Optional custom title for the next lesson when opening a session
  const [nextLessonTitle, setNextLessonTitle] = useState('')

  // Inline lesson rename state
  const [editingLessonTitle, setEditingLessonTitle] = useState(false)
  const [lessonTitleDraft, setLessonTitleDraft] = useState('')

  // Inline student edit state
  const [editingStudentId, setEditingStudentId] = useState(null)
  const [studentDraft, setStudentDraft] = useState({ full_name: '', id_number: '', email: '' })

  const [busyAction, setBusyAction] = useState(null)
  const [banner, setBanner] = useState(null) // { tone: 'success'|'danger'|'info', text: string }
  const [copied, setCopied] = useState(false)
  const [search, setSearch] = useState('')
  const [filter, setFilter] = useState('all')
  const [newStudent, setNewStudent] = useState({
    full_name: '',
    id_number: '',
    email: '',
    mark_present: true,
  })

  const buildDashboardUrl = () => {
    const params = new URLSearchParams()
    if (selectedCourseId) params.set('course_id', String(selectedCourseId))
    if (selectedLessonId) params.set('lesson_id', String(selectedLessonId))
    const qs = params.toString()
    return `/api/attendance/dashboard${qs ? `?${qs}` : ''}`
  }

  const {
    data: dashboard,
    error: pollError,
    loading,
    refresh,
  } = usePolling(() => api.get(buildDashboardUrl()), 3000)

  // Refresh immediately when the lecturer switches course or lesson.
  useEffect(() => {
    refresh()
  }, [selectedCourseId, selectedLessonId, refresh])

  const course = dashboard?.course || null
  const courses = dashboard?.courses || []
  const lessons = dashboard?.lessons || []
  const session = dashboard?.session || null
  const activeGlobal = dashboard?.active_session || null
  const nextLessonNum = dashboard?.next_lesson_number || 1
  const nextLessonDefault = dashboard?.next_lesson_default_title || `Lesson ${nextLessonNum}`

  const isActive = Boolean(session?.active)
  const remaining = useCountdown(session?.seconds_remaining || 0, dashboard?.server_time)

  // Automatically refresh once when the countdown hits 0 so the UI flips to closed state.
  useEffect(() => {
    if (isActive && remaining === 0) {
      const t = window.setTimeout(() => refresh(), 600)
      return () => window.clearTimeout(t)
    }
    return undefined
  }, [isActive, remaining, refresh])

  const showBanner = (tone, text) => {
    setBanner({ tone, text })
  }

  const detectFrontendPort = () => {
    const port = Number(window.location.port)
    if (port) return port
    return window.location.protocol === 'https:' ? 443 : 80
  }

  const handleCourseChange = (event) => {
    const nextId = Number(event.target.value)
    setSelectedCourseId(nextId)
    setSelectedLessonId(null)
    setEditingLessonTitle(false)
    setEditingStudentId(null)
    setNextLessonTitle('')
  }

  const selectLesson = (lessonId) => {
    setSelectedLessonId(lessonId ? Number(lessonId) : null)
    setEditingLessonTitle(false)
    setEditingStudentId(null)
  }

  const handleLessonChange = (event) => {
    selectLesson(event.target.value)
  }

  const handleCreateCourse = async (event) => {
    event.preventDefault()
    if (!newCourseName.trim()) return
    setBusyAction('create-course')
    setBanner(null)
    try {
      const created = await api.post('/api/courses', { name: newCourseName.trim() })
      let importSummary = ''
      if (newCourseFile) {
        const res = await api.upload(`/api/students/import?course_id=${created.id}`, newCourseFile)
        importSummary = ` Imported ${res.created} students from "${newCourseFile.name}" into the course roster.`
      }
      setNewCourseName('')
      setNewCourseFile(null)
      if (newCourseFileRef.current) newCourseFileRef.current.value = ''
      setShowNewCourse(false)
      setSelectedCourseId(created.id)
      setSelectedLessonId(null)
      await refresh()
      showBanner('success', `Created course "${created.name}".${importSummary}`)
    } catch (err) {
      showBanner('danger', err.message)
    } finally {
      setBusyAction(null)
    }
  }

  const handleDeleteCourse = async () => {
    if (!course) return
    const confirmed = window.confirm(
      `Are you sure you want to delete the course "${course.name}" and all of its lessons and roster?`,
    )
    if (!confirmed) return
    setBusyAction('delete-course')
    setBanner(null)
    try {
      try {
        await api.del(`/api/courses/${course.id}`)
      } catch (innerErr) {
        if (innerErr.status === 404 || innerErr.status === 405) {
          await api.post('/api/courses', { name: `__DELETE_COURSE__:${course.id}` })
        } else {
          throw innerErr
        }
      }
      setSelectedCourseId(null)
      setSelectedLessonId(null)
      setEditingLessonTitle(false)
      await refresh()
      showBanner('info', `Deleted course "${course.name}".`)
    } catch (err) {
      showBanner('danger', err.message)
    } finally {
      setBusyAction(null)
    }
  }

  const handleDeleteLesson = async (targetLesson = session) => {
    if (!targetLesson) return
    const heading = formatLessonHeading(targetLesson)
    const confirmed = window.confirm(
      `Are you sure you want to delete "${heading}" and its attendance records?`,
    )
    if (!confirmed) return
    setBusyAction(`delete-lesson-${targetLesson.id}`)
    setBanner(null)
    try {
      try {
        await api.del(`/api/attendance/lessons/${targetLesson.id}`)
      } catch (innerErr) {
        if (innerErr.status === 404 || innerErr.status === 405) {
          await api.patch(`/api/attendance/lessons/${targetLesson.id}`, {
            title: '__DELETE_LESSON__',
          })
        } else {
          throw innerErr
        }
      }
      setSelectedLessonId(null)
      setEditingLessonTitle(false)
      await refresh()
      showBanner('info', `Deleted ${heading}.`)
    } catch (err) {
      showBanner('danger', err.message)
    } finally {
      setBusyAction(null)
    }
  }

  const handleOpenSession = async (event) => {
    if (event) event.preventDefault()
    setBusyAction('open')
    setBanner(null)
    try {
      const opened = await api.post('/api/attendance/sessions/open', {
        course_id: course?.id,
        title: nextLessonTitle.trim() || null,
        base_url: window.location.origin,
        frontend_port: detectFrontendPort(),
      })
      setNextLessonTitle('')
      setSelectedCourseId(opened.course_id)
      setSelectedLessonId(opened.id)
      await refresh()
      showBanner(
        'success',
        `Opened ${formatLessonHeading(opened)} for "${course?.name}". Classroom network lock active (${opened.lecturer_subnet || 'same Wi-Fi /24'}).`,
      )
    } catch (err) {
      showBanner('danger', err.message)
    } finally {
      setBusyAction(null)
    }
  }

  const handleReopenLesson = async () => {
    if (!session) return
    setBusyAction('reopen')
    setBanner(null)
    const payload = {
      course_id: course?.id,
      lesson_id: session.id,
      title: session.title,
      base_url: window.location.origin,
      frontend_port: detectFrontendPort(),
    }
    try {
      let reopened
      try {
        reopened = await api.post(`/api/attendance/lessons/${session.id}/reopen`, payload)
      } catch (innerErr) {
        if (innerErr.status === 404 || innerErr.status === 405) {
          // Re-open the existing lesson in-place via PATCH (never creates a new lesson)
          const currentTitle = session.title || `Lesson ${session.lesson_number}`
          reopened = await api.patch(`/api/attendance/lessons/${session.id}`, {
            title: `__REOPEN__:${currentTitle}`,
            reopen: true,
            base_url: window.location.origin,
            frontend_port: detectFrontendPort(),
          })
        } else {
          throw innerErr
        }
      }
      setSelectedCourseId(reopened.course_id || course?.id)
      setSelectedLessonId(reopened.id || session.id)
      await refresh()
      showBanner('success', `Re-opened QR attendance for ${formatLessonHeading(session)} (15 minutes).`)
    } catch (err) {
      showBanner('danger', err.message)
    } finally {
      setBusyAction(null)
    }
  }

  const handleCloseSession = async () => {
    setBusyAction('close')
    setBanner(null)
    try {
      await api.post('/api/attendance/sessions/close', { lesson_id: session?.id })
      await refresh()
      showBanner('info', 'Attendance session closed. You can continue editing this lesson manually at any time.')
    } catch (err) {
      showBanner('danger', err.message)
    } finally {
      setBusyAction(null)
    }
  }

  const startRenameLesson = () => {
    if (!session) return
    setLessonTitleDraft(session.title || `Lesson ${session.lesson_number}`)
    setEditingLessonTitle(true)
  }

  const handleRenameLesson = async (event) => {
    event.preventDefault()
    if (!session || !lessonTitleDraft.trim()) return
    setBusyAction('rename-lesson')
    try {
      await api.patch(`/api/attendance/lessons/${session.id}`, {
        title: lessonTitleDraft.trim(),
      })
      setEditingLessonTitle(false)
      await refresh()
      showBanner('success', `Renamed Lesson ${session.lesson_number} to "${lessonTitleDraft.trim()}".`)
    } catch (err) {
      showBanner('danger', err.message)
    } finally {
      setBusyAction(null)
    }
  }

  const handleCopyUrl = async () => {
    if (!session?.checkin_url) return
    try {
      await navigator.clipboard.writeText(session.checkin_url)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 2000)
    } catch {
      showBanner('info', `Check-in URL: ${session.checkin_url}`)
    }
  }

  const handleFileChange = async (event) => {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return

    setBusyAction('import')
    setBanner(null)
    try {
      const qs = course?.id ? `?course_id=${course.id}` : ''
      const res = await api.upload(`/api/students/import${qs}`, file)
      await refresh()
      const cols = Object.entries(res.detected_columns || {})
        .filter(([, v]) => Boolean(v))
        .map(([k, v]) => `${k}: "${v}"`)
        .join(', ')
      showBanner(
        'success',
        `Linked & saved "${file.name}" to course "${res.course_name || course?.name}": ${res.created} added, ${res.updated} updated` +
          (res.skipped ? `, ${res.skipped} empty rows skipped` : '') +
          (cols ? ` (detected ${cols}).` : '.'),
      )
    } catch (err) {
      showBanner('danger', err.message)
    } finally {
      setBusyAction(null)
    }
  }

  const handleManualToggle = async (student) => {
    setBusyAction(`student-${student.id}`)
    setBanner(null)
    try {
      try {
        await api.patch(`/api/students/${student.id}/presence`, {
          is_present: !student.is_present,
          lesson_id: session?.id || null,
        })
      } catch (innerErr) {
        if ((innerErr.status === 404 || innerErr.status === 405) && session?.id) {
          if (student.is_present) {
            await api.del(`/api/attendance/records/${student.id}?lesson_id=${session.id}`)
          } else {
            await api.post('/api/attendance/manual', {
              student_id: student.id,
              lesson_id: session.id,
            })
          }
        } else {
          throw innerErr
        }
      }
      await refresh()
    } catch (err) {
      showBanner('danger', err.message)
    } finally {
      setBusyAction(null)
    }
  }

  const startEditStudent = (student) => {
    setEditingStudentId(student.id)
    setStudentDraft({
      full_name: student.full_name || '',
      id_number: student.id_number || '',
      email: student.email || '',
    })
  }

  const handleSaveStudentEdit = async (studentId) => {
    if (!studentDraft.full_name.trim()) {
      showBanner('danger', 'Student full name is required.')
      return
    }
    setBusyAction(`edit-student-${studentId}`)
    try {
      await api.patch(`/api/students/${studentId}`, {
        full_name: studentDraft.full_name.trim(),
        id_number: studentDraft.id_number.trim() || null,
        email: studentDraft.email.trim() || null,
      })
      setEditingStudentId(null)
      await refresh()
      showBanner('success', 'Updated student details in the course roster.')
    } catch (err) {
      showBanner('danger', err.message)
    } finally {
      setBusyAction(null)
    }
  }

  const handleDeleteStudent = async (student) => {
    setBusyAction(`delete-student-${student.id}`)
    try {
      await api.del(`/api/students/${student.id}`)
      if (editingStudentId === student.id) setEditingStudentId(null)
      await refresh()
      showBanner('info', `Removed ${student.full_name} from the "${course?.name}" roster.`)
    } catch (err) {
      showBanner('danger', err.message)
    } finally {
      setBusyAction(null)
    }
  }

  const handleAddStudent = async (event) => {
    event.preventDefault()
    if (!newStudent.full_name.trim()) {
      showBanner('danger', 'Student full name is required.')
      return
    }
    setBusyAction('add-student')
    setBanner(null)
    try {
      const res = await api.post('/api/students', {
        course_id: course?.id,
        lesson_id: session?.id || null,
        full_name: newStudent.full_name.trim(),
        id_number: newStudent.id_number.trim() || null,
        email: newStudent.email.trim() || null,
        mark_present: Boolean(newStudent.mark_present),
      })
      setNewStudent({ full_name: '', id_number: '', email: '', mark_present: true })
      await refresh()
      showBanner(
        'success',
        `${res.created ? 'Added' : 'Updated'} ${res.student.full_name} in the "${course?.name}" roster` +
          (res.marked_present ? ` and marked as נוכח (Present).` : '.'),
      )
    } catch (err) {
      showBanner('danger', err.message)
    } finally {
      setBusyAction(null)
    }
  }

  const handleAlertAction = async (alertId, approve) => {
    setBusyAction(`alert-${alertId}`)
    try {
      await api.post(`/api/attendance/alerts/${alertId}/dismiss?approve=${approve}`, {})
      await refresh()
    } catch (err) {
      showBanner('danger', err.message)
    } finally {
      setBusyAction(null)
    }
  }

  const handleResetLocks = async () => {
    setBusyAction('reset-locks')
    try {
      const res = await api.del('/api/attendance/device-locks')
      showBanner('info', `Cleared ${res.removed} active 15-minute device lock(s) for demo testing.`)
    } catch (err) {
      showBanner('danger', err.message)
    } finally {
      setBusyAction(null)
    }
  }

  const handleToggleNetworkOverride = async (nextChecked) => {
    if (!session) return
    setBusyAction('network-override')
    try {
      await api.patch(`/api/attendance/lessons/${session.id}/network-override`, {
        allow_any_network: Boolean(nextChecked),
      })
      await refresh()
      showBanner(
        nextChecked ? 'info' : 'success',
        nextChecked
          ? '⚠️ Demo Override ON: Current session allows check-ins without Wi-Fi IP/ASN restriction.'
          : '🔒 Strict Classroom Wi-Fi Network Lock restored for current session.',
      )
    } catch (err) {
      showBanner('danger', err.message)
    } finally {
      setBusyAction(null)
    }
  }

  const handlePhotoUpload = async (event) => {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file || !session) return
    setBusyAction('photo-count')
    setBanner(null)
    try {
      const res = await api.upload(`/api/attendance/lessons/${session.id}/photo-headcount`, file)
      await refresh()
      if (res.discrepancy_alert) {
        showBanner(
          'danger',
          `🚨 Discrepancy Alert: ${res.registered_count} students marked present, but only ${res.photo_headcount} counted in "${file.name}" (${res.discrepancy} extra check-ins)!`,
        )
      } else {
        showBanner(
          'success',
          `📸 AI Photo Count for ${formatLessonHeading(session)}: ${res.photo_headcount} students counted (registered: ${res.registered_count}).`,
        )
      }
    } catch (err) {
      showBanner('danger', err.message)
    } finally {
      setBusyAction(null)
    }
  }

  const handleManualHeadcountSave = async (event) => {
    event.preventDefault()
    if (!session) return
    const val = manualHeadcount === '' ? null : Math.max(0, Number(manualHeadcount))
    setBusyAction('photo-manual')
    try {
      await api.patch(`/api/attendance/lessons/${session.id}/photo-headcount`, {
        photo_headcount: val,
        photo_analysis: val !== null ? `Manual headcount verification: ${val} students in room.` : null,
      })
      setManualHeadcount('')
      await refresh()
    } catch (err) {
      showBanner('danger', err.message)
    } finally {
      setBusyAction(null)
    }
  }

  const triggerBlobDownload = (blob, filename) => {
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = filename
    document.body.appendChild(link)
    link.click()
    link.remove()
    window.setTimeout(() => URL.revokeObjectURL(url), 5000)
  }

  const handleExportDownload = async (endpoint, fmt, fallbackName) => {
    setBusyAction(`export-${endpoint}-${fmt}`)
    setBanner(null)
    try {
      const resp = await fetch(endpoint, { credentials: 'include' })
      if (resp.ok) {
        const disp = resp.headers.get('content-disposition') || ''
        const match = disp.match(/filename="([^"]+)"/)
        const filename = match ? match[1] : `${fallbackName}.${fmt}`
        const blob = await resp.blob()
        triggerBlobDownload(blob, filename)
        return
      }
      // Transparent fallback: if an un-restarted backend returned 500 on fmt=xlsx,
      // fetch fmt=csv from the backend and build a genuine .xlsx file in the browser.
      if (fmt === 'xlsx') {
        const csvEndpoint = endpoint.replace('fmt=xlsx', 'fmt=csv')
        const csvResp = await fetch(csvEndpoint, { credentials: 'include' })
        if (csvResp.ok) {
          const disp = csvResp.headers.get('content-disposition') || ''
          const match = disp.match(/filename="([^"]+)"/)
          const csvName = match ? match[1] : `${fallbackName}.csv`
          const xlsxName = csvName.replace(/\.csv$/i, '.xlsx')
          const csvText = await csvResp.text()
          const xlsxBlob = csvTextToXlsxBlob(csvText)
          triggerBlobDownload(xlsxBlob, xlsxName)
          return
        }
      }
      throw new Error(`Export failed (${resp.status})`)
    } catch (err) {
      showBanner('danger', err.message || 'Could not export attendance file.')
    } finally {
      setBusyAction(null)
    }
  }

  const students = dashboard?.students || []
  const alerts = dashboard?.alerts || []
  const stats = dashboard?.stats || { roster_size: 0, present: 0, absent: 0, unlisted: 0, manual: 0 }
  const network = dashboard?.network || null

  const filteredStudents = students.filter((s) => {
    const statusKey = s.status || 'absent'
    if (filter === 'present' && !s.is_present) return false
    if (filter === 'absent' && s.is_present) return false
    if (filter === 'unlisted' && statusKey !== 'unlisted') return false
    if (!search.trim()) return true
    const q = search.trim().toLowerCase()
    return (
      s.full_name?.toLowerCase().includes(q) ||
      s.id_number?.toLowerCase().includes(q) ||
      s.email?.toLowerCase().includes(q)
    )
  })

  const attendanceRate =
    students.length > 0 ? Math.round((stats.present / students.length) * 100) : 0

  return (
    <div className="dashboard">
      {/* Top Network & Demo Info Strip */}
      <div className="network-bar">
        <div className="network-bar__left">
          <span className="network-pill">
            <span className="status-dot status-dot--live" aria-hidden="true" />
            Classroom Network Lock: <code>{session?.lecturer_subnet || network?.subnet || '127.0.0.0/24'}</code>
          </span>
          {network && (
            <span className="network-meta">
              Lecturer IP: <code>{session?.lecturer_ip || network.lecturer_ip || network.lan_ip}</code>
            </span>
          )}
        </div>
        <div className="network-bar__right">
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            onClick={handleResetLocks}
            disabled={busyAction === 'reset-locks'}
            title="Clears the 15-minute device lockout table so you can test multiple check-ins from the same phone/laptop during the live demo"
          >
            🔓 Reset 15m Device Locks (Demo)
          </button>
        </div>
      </div>

      {/* Course / Class & Lesson History Selector Bar */}
      <section className="card context-bar" aria-label="Class and lesson selector">
        <div className="context-bar__row">
          {/* Class / Course Selector */}
          <div className="context-group">
            <label className="context-group__label" htmlFor="course-select">
              📚 Course / Lecture (קורס / הרצאה)
            </label>
            <div className="context-group__controls">
              <select
                id="course-select"
                className="select-input select-input--strong"
                value={course?.id || ''}
                onChange={handleCourseChange}
                dir="auto"
              >
                {courses.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name} ({c.student_count} students in roster · {c.lesson_count} lessons)
                  </option>
                ))}
              </select>
              <button
                type="button"
                className="btn btn--secondary btn--sm"
                onClick={() => setShowNewCourse((v) => !v)}
              >
                {showNewCourse ? 'Cancel' : '+ New Course'}
              </button>
              {course && (
                <button
                  type="button"
                  className="btn btn--ghost btn--sm"
                  onClick={handleDeleteCourse}
                  disabled={busyAction === 'delete-course'}
                  title={`Delete course "${course.name}"`}
                >
                  🗑️ Delete Course
                </button>
              )}
            </div>
          </div>

          {/* Lesson Selector / History Dropdown */}
          <div className="context-group">
            <label className="context-group__label" htmlFor="lesson-select">
              🗓️ Select Lesson to View or Edit ({lessons.length})
            </label>
            <div className="context-group__controls">
              <select
                id="lesson-select"
                className="select-input"
                value={session?.id || ''}
                onChange={handleLessonChange}
                disabled={lessons.length === 0}
                dir="auto"
              >
                {lessons.length === 0 ? (
                  <option value="">No lessons yet — open Lesson 1 below</option>
                ) : (
                  lessons.map((l) => {
                    const datePart = formatShortDate(l.opened_at)
                    const stateTag = l.active ? '● LIVE' : 'Saved'
                    return (
                      <option key={l.id} value={l.id}>
                        {formatLessonHeading(l)} ({l.present_count ?? 0}/{students.length} present) · {stateTag}
                        {datePart ? ` · ${datePart}` : ''}
                      </option>
                    )
                  })
                )}
              </select>
              {session && !editingLessonTitle && (
                <>
                  <button
                    type="button"
                    className="btn btn--ghost btn--sm"
                    onClick={startRenameLesson}
                    title="Rename this lesson"
                  >
                    ✏️ Rename
                  </button>
                  <button
                    type="button"
                    className="btn btn--ghost btn--sm"
                    onClick={() => handleDeleteLesson(session)}
                    disabled={busyAction === `delete-lesson-${session.id}`}
                    title={`Delete ${formatLessonHeading(session)}`}
                  >
                    🗑️ Delete Lesson
                  </button>
                </>
              )}
            </div>
          </div>
        </div>

        {/* Clickable Lesson Pills Strip for fast switching between lessons */}
        {lessons.length > 0 && (
          <div className="lesson-strip" role="tablist" aria-label="Lessons of this course">
            <span className="lesson-strip__label">Lessons in {course?.name}:</span>
            {lessons
              .slice()
              .reverse()
              .map((l) => {
                const isSelected = session?.id === l.id
                return (
                  <button
                    key={l.id}
                    type="button"
                    role="tab"
                    aria-selected={isSelected}
                    className={`lesson-pill ${isSelected ? 'is-selected' : ''} ${l.active ? 'is-live' : ''}`}
                    onClick={() => selectLesson(l.id)}
                  >
                    <span>{formatLessonHeading(l)}</span>
                    <span className="lesson-pill__count">
                      {l.present_count ?? 0}/{students.length}
                    </span>
                    {l.active && <span className="lesson-pill__dot" aria-label="Live" />}
                  </button>
                )
              })}
          </div>
        )}

        {/* Inline New Class Form (with optional Roster upload linked to the new course) */}
        {showNewCourse && (
          <form className="context-subform" onSubmit={handleCreateCourse}>
            <input
              type="text"
              placeholder='New course name (e.g. "Object-Oriented Programming" / "מבוא להנדסת תוכנה")'
              value={newCourseName}
              onChange={(e) => setNewCourseName(e.target.value)}
              required
              autoFocus
              dir="auto"
            />
            <label className="btn btn--secondary btn--sm file-picker-label">
              <span>{newCourseFile ? `📎 ${newCourseFile.name}` : '📂 Attach Course Roster (.xlsx/.csv) (optional)'}</span>
              <input
                ref={newCourseFileRef}
                type="file"
                accept=".xlsx,.xls,.csv,.tsv"
                onChange={(e) => setNewCourseFile(e.target.files?.[0] || null)}
                hidden
              />
            </label>
            <button
              type="submit"
              className="btn btn--primary btn--sm"
              disabled={busyAction === 'create-course'}
            >
              {busyAction === 'create-course' ? 'Creating…' : 'Create Course'}
            </button>
          </form>
        )}

        {/* Inline Rename Lesson Form */}
        {editingLessonTitle && session && (
          <form className="context-subform" onSubmit={handleRenameLesson}>
            <span className="context-subform__tag">Lesson {session.lesson_number} Title:</span>
            <input
              type="text"
              value={lessonTitleDraft}
              onChange={(e) => setLessonTitleDraft(e.target.value)}
              required
              autoFocus
              dir="auto"
            />
            <button
              type="submit"
              className="btn btn--primary btn--sm"
              disabled={busyAction === 'rename-lesson'}
            >
              Save Title
            </button>
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              onClick={() => setEditingLessonTitle(false)}
            >
              Cancel
            </button>
          </form>
        )}

        {/* Notice if another lesson is live while viewing a past lesson */}
        {activeGlobal && (!session || activeGlobal.id !== session.id) && (
          <div className="context-live-hint">
            <span>
              ● Live session currently running: <strong>{activeGlobal.course_name}</strong> —{' '}
              <strong>{formatLessonHeading(activeGlobal)}</strong>
            </span>
            <button
              type="button"
              className="btn btn--primary btn--sm"
              onClick={() => {
                setSelectedCourseId(activeGlobal.course_id)
                setSelectedLessonId(activeGlobal.id)
              }}
            >
              Jump to Live QR
            </button>
          </div>
        )}
      </section>

      {/* Feedback Banner */}
      {banner && (
        <div className={`notice notice--${banner.tone} notice--dismissible`} role="status">
          <span>{banner.text}</span>
          <button type="button" className="notice__close" onClick={() => setBanner(null)} aria-label="Dismiss">
            ✕
          </button>
        </div>
      )}

      {pollError && !dashboard && (
        <div className="notice notice--danger" role="alert">
          Could not load dashboard: {pollError.message}
        </div>
      )}

      {/* Automatic Discrepancy System Alert (Registered Present > Photo Headcount) */}
      {session &&
        session.photo_headcount !== null &&
        session.photo_headcount !== undefined &&
        stats.present > session.photo_headcount && (
          <section className="alerts-panel alerts-panel--danger" role="alert" aria-label="Attendance discrepancy alert">
            <div className="alerts-panel__header">
              <span className="alerts-panel__icon" aria-hidden="true">🚨</span>
              <div>
                <h2>
                  Discrepancy Alert: {stats.present} students marked present, but only {session.photo_headcount} students were counted in the classroom photo —{' '}
                  {stats.present - session.photo_headcount} extra check-in(s) detected!
                </h2>
                <p>
                  Lesson: <strong>{formatLessonHeading(session)}</strong>
                  {session.photo_analysis ? ` · ${session.photo_analysis}` : ''}
                </p>
              </div>
            </div>
          </section>
        )}

      {/* System Alerts for Newly Registered Students (Auto-Added to Course Roster) */}
      {alerts.length > 0 && (
        <section className="alerts-panel" aria-label="New student registration alerts">
          <div className="alerts-panel__header">
            <span className="alerts-panel__icon" aria-hidden="true">🔔</span>
            <div>
              <h2>
                New Student Joined via QR ({alerts.length}) — Automatically Added to "{course?.name}" Roster
              </h2>
              <p>
                The following student(s) were not in the original uploaded list when they scanned the QR code.
                They were <strong>automatically added to the course roster</strong> and marked as{' '}
                <strong>נוכח (Present)</strong>.
              </p>
            </div>
          </div>
          <ul className="alerts-list">
            {alerts.map((alert) => (
              <li key={alert.id} className="alert-item">
                <div className="alert-item__details">
                  <strong className="alert-item__name" dir="auto">{alert.full_name}</strong>
                  <span className="alert-item__field">
                    ID (ת״ז): <code>{alert.id_number || 'Not provided'}</code>
                  </span>
                  <span className="alert-item__field">
                    Email: <code>{alert.email || 'Not provided'}</code>
                  </span>
                  <span className="alert-item__field">
                    Time: <code>{formatTime(alert.created_at)}</code>
                  </span>
                </div>
                <div className="alert-item__actions">
                  <button
                    type="button"
                    className="btn btn--secondary btn--sm"
                    disabled={busyAction === `alert-${alert.id}`}
                    onClick={() => handleAlertAction(alert.id, true)}
                  >
                    ✓ Got It
                  </button>
                </div>
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* Main Grid: Left = QR Session Control, Right = Summary & Quick Actions */}
      <div className="dashboard__grid">
        {/* QR Session Card */}
        <section className="card qr-card">
          <div className="card__header">
            <div>
              <h2>
                {session ? formatLessonHeading(session) : 'QR Attendance Session'}
              </h2>
              <p className="muted">
                {course?.name} · Course roster ({students.length} students) loads automatically in every lesson.
              </p>
            </div>
            <span className={`badge ${isActive ? 'badge--live' : 'badge--muted'}`}>
              {isActive ? '● OPEN (LIVE)' : 'CLOSED'}
            </span>
          </div>

          {isActive && session ? (
            <div className="qr-active">
              <div className="qr-frame">
                <img
                  src={`/api/attendance/sessions/${session.id}/qr.png?t=${encodeURIComponent(session.token)}`}
                  alt="Attendance check-in QR code"
                  className="qr-image"
                />
              </div>

              <div className="qr-info">
                <div className="countdown-box" aria-live="polite">
                  <span className="countdown-box__label">Time Remaining</span>
                  <span className={`countdown-box__time ${remaining <= 60 ? 'is-urgent' : ''}`}>
                    {formatClock(remaining)}
                  </span>
                </div>

                <div className="qr-url-box">
                  <span className="qr-url-box__label">LAN Check-in URL (same Wi-Fi required)</span>
                  <code className="qr-url-box__code">{session.checkin_url}</code>
                  <div className="qr-url-box__actions">
                    <button type="button" className="btn btn--secondary btn--sm" onClick={handleCopyUrl}>
                      {copied ? '✓ Copied' : '📋 Copy URL'}
                    </button>
                    <button
                      type="button"
                      className="btn btn--secondary btn--sm"
                      onClick={() => setRole(ROLES.STUDENT)}
                    >
                      🎓 Preview Student Form
                    </button>
                  </div>
                </div>

                <div className="qr-controls">
                  <button
                    type="button"
                    className="btn btn--danger btn--block"
                    onClick={handleCloseSession}
                    disabled={busyAction === 'close'}
                  >
                    {busyAction === 'close' ? 'Closing…' : `⏹ Close ${formatLessonHeading(session)} QR`}
                  </button>
                </div>
              </div>
            </div>
          ) : (
            <div className="qr-closed">
              <div className="qr-closed__placeholder" aria-hidden="true">
                <span>📱</span>
              </div>
              <p className="muted">
                {session
                  ? `Viewing ${formatLessonHeading(session)} (${formatShortDate(session.opened_at)}). You can edit students and mark נוכח / לא נוכח in the table below, re-open the QR for this lesson, or start the next lesson:`
                  : `Course "${course?.name}" has ${students.length} student(s) saved in its roster. Open Lesson 1 below and the entire roster will load automatically:`}
              </p>

              {session && (
                <button
                  type="button"
                  className="btn btn--secondary btn--sm"
                  onClick={handleReopenLesson}
                  disabled={busyAction === 'reopen'}
                >
                  {busyAction === 'reopen'
                    ? 'Re-opening…'
                    : `🔄 Re-open QR for ${formatLessonHeading(session)} (15:00)`}
                </button>
              )}

              <form className="qr-next-form" onSubmit={handleOpenSession}>
                <label className="field qr-closed__title-field">
                  <span>
                    Open New Sequential Lesson <span className="optional">(defaults to "{nextLessonDefault}")</span>
                  </span>
                  <input
                    type="text"
                    placeholder={`${nextLessonDefault} (or custom title, e.g. "Design Patterns")`}
                    value={nextLessonTitle}
                    onChange={(e) => setNextLessonTitle(e.target.value)}
                    maxLength={200}
                    dir="auto"
                  />
                </label>

                <button
                  type="submit"
                  className="btn btn--primary btn--lg btn--block"
                  disabled={busyAction === 'open'}
                >
                  {busyAction === 'open'
                    ? 'Opening…'
                    : `▶ Open ${nextLessonTitle.trim() ? `Lesson ${nextLessonNum}: ${nextLessonTitle.trim()}` : nextLessonDefault} (15:00)`}
                </button>
              </form>
            </div>
          )}

          {/* Live Network Diagnostic & Demo Override Box */}
          <div className="qr-url-box" style={{ marginTop: '1rem' }} aria-label="Network Diagnostic Box">
            <span className="qr-url-box__label">📡 Live Network & Cloudflare Tunnel Diagnostic</span>
            <div style={{ display: 'grid', gap: '0.3rem', fontSize: '0.88rem', marginTop: '0.25rem' }}>
              <div>
                <strong>QR Code URL:</strong>{' '}
                <code>{session?.checkin_url || `${window.location.origin}/attendance/checkin?token=...`}</code>
              </div>
              <div>
                <strong>Lecturer Detected Request IP:</strong>{' '}
                <code>{network?.detected_request_ip || session?.lecturer_ip || network?.lan_ip || '127.0.0.1'}</code>
                {' · '}
                <strong>Local LAN IP:</strong> <code>{network?.lan_ip || '127.0.0.1'}</code>
              </div>
              <div>
                <strong>Lecturer Public IP (Router / Tunnel):</strong>{' '}
                <code>{network?.public_ip || session?.lecturer_ip || 'Detecting…'}</code>
                {' · '}
                <strong>Active Subnet Lock:</strong>{' '}
                <code>{network?.subnet || session?.lecturer_subnet || '127.0.0.0/24'}</code>
              </div>
              <div>
                <strong>ISP / Organization ASN:</strong>{' '}
                <code>
                  {network?.asn ? `${network.asn}${network?.org ? ` (${network.org})` : ''}` : 'Local LAN'}
                </code>
              </div>
            </div>

            {session && (
              <label
                className="checkbox-label"
                style={{
                  marginTop: '0.65rem',
                  paddingTop: '0.55rem',
                  borderTop: '1px solid rgba(148, 163, 184, 0.25)',
                }}
              >
                <input
                  type="checkbox"
                  checked={Boolean(network?.allow_any_network)}
                  disabled={busyAction === 'network-override'}
                  onChange={(e) => handleToggleNetworkOverride(e.target.checked)}
                />
                <span>
                  <strong>Demo Override: Allow Current Session Without IP Check</strong>{' '}
                  <span className="muted">(Safety net for live class presentation; off by default)</span>
                </span>
              </label>
            )}
          </div>
        </section>

        {/* Right Column: Stats, Roster Import/Export & Manual Add Student */}
        <div className="dashboard__side">
          {/* Stats Cards */}
          <section className="stats-grid" aria-label="Attendance statistics">
            <div className="stat-card">
              <span className="stat-card__label">Course Roster</span>
              <strong className="stat-card__value">{students.length}</strong>
            </div>
            <div className="stat-card stat-card--success">
              <span className="stat-card__label">נוכחים (Present)</span>
              <strong className="stat-card__value">
                {stats.present} <small>({attendanceRate}%)</small>
              </strong>
            </div>
            <div className="stat-card stat-card--muted">
              <span className="stat-card__label">לא נוכחים (Absent)</span>
              <strong className="stat-card__value">{stats.absent}</strong>
            </div>
            <div className="stat-card stat-card--warning">
              <span className="stat-card__label">Joined via QR</span>
              <strong className="stat-card__value">{stats.unlisted}</strong>
            </div>
          </section>

          {/* Classroom Photo Headcount & Discrepancy Check Card */}
          <section className="card">
            <div className="card__header">
              <div>
                <h2>📸 Classroom Photo Headcount (AI Vision)</h2>
                <p className="muted">
                  Upload a photo of the lecture hall to automatically count seated students via Vision LLM and detect remote/proxy check-ins.
                </p>
              </div>
              {session && session.photo_headcount !== null && session.photo_headcount !== undefined && (
                <span
                  className={`badge ${
                    stats.present > session.photo_headcount ? 'badge--danger' : 'badge--success'
                  }`}
                >
                  Photo: {session.photo_headcount} vs. Present: {stats.present}
                </span>
              )}
            </div>

            <input
              ref={photoInputRef}
              type="file"
              accept="image/jpeg,image/png,image/webp,.jpg,.jpeg,.png,.webp"
              onChange={handlePhotoUpload}
              hidden
            />

            <div className="btn-row">
              <button
                type="button"
                className="btn btn--primary"
                disabled={!session || busyAction === 'photo-count'}
                onClick={() => photoInputRef.current?.click()}
              >
                {busyAction === 'photo-count'
                  ? '🤖 Counting Students in Photo…'
                  : '📷 Upload Classroom Photo (.jpg / .png)'}
              </button>
              <button
                type="button"
                className="btn btn--ghost btn--sm"
                onClick={() => setAiSettingsOpen(true)}
              >
                ⚙️ Configure Vision API Key
              </button>
            </div>

            {session && (
              <form className="context-subform" style={{ marginTop: '0.75rem' }} onSubmit={handleManualHeadcountSave}>
                <span className="context-subform__tag">Or set headcount manually:</span>
                <input
                  type="number"
                  min={0}
                  max={1000}
                  placeholder={
                    session.photo_headcount !== null && session.photo_headcount !== undefined
                      ? `Current: ${session.photo_headcount}`
                      : 'Enter headcount (e.g. 25)'
                  }
                  value={manualHeadcount}
                  onChange={(e) => setManualHeadcount(e.target.value)}
                />
                <button
                  type="submit"
                  className="btn btn--secondary btn--sm"
                  disabled={busyAction === 'photo-manual'}
                >
                  Apply Count
                </button>
              </form>
            )}

            {session?.photo_analysis && (
              <p className="muted" style={{ marginTop: '0.6rem' }}>
                <strong>Last Count Result:</strong> {session.photo_analysis}
              </p>
            )}
          </section>

          {/* Roster Import & Session Export */}
          <section className="card">
            <h2>Course Roster & Attendance Export — {course?.name}</h2>
            <p className="muted">
              Upload a student list (<code>.xlsx</code>, <code>.csv</code>, or Google Forms CSV) to link and save it
              to <strong>{course?.name}</strong>. The saved list ({students.length} students) automatically appears
              online in every lesson you open for this course.
            </p>
            <input
              ref={fileInputRef}
              type="file"
              accept=".xlsx,.xls,.csv,.tsv"
              onChange={handleFileChange}
              hidden
            />
            <div className="btn-row">
              <button
                type="button"
                className="btn btn--primary"
                onClick={() => fileInputRef.current?.click()}
                disabled={busyAction === 'import'}
              >
                {busyAction === 'import' ? '⏳ Uploading…' : `📂 Upload Roster to "${course?.name}" (.xlsx / .csv)`}
              </button>

              <button
                type="button"
                className="btn btn--secondary"
                disabled={!session}
                onClick={() => {
                  if (!session) {
                    showBanner('info', 'Open a lesson first before exporting lesson attendance.')
                    return
                  }
                  handleExportDownload(
                    `/api/attendance/export?fmt=csv&lesson_id=${session.id}`,
                    'csv',
                    `attendance_lesson_${session.lesson_number}`,
                  )
                }}
              >
                ⬇ Lesson CSV
              </button>
              <button
                type="button"
                className="btn btn--secondary"
                disabled={!session}
                onClick={() => {
                  if (!session) {
                    showBanner('info', 'Open a lesson first before exporting lesson attendance.')
                    return
                  }
                  handleExportDownload(
                    `/api/attendance/export?fmt=xlsx&lesson_id=${session.id}`,
                    'xlsx',
                    `attendance_lesson_${session.lesson_number}`,
                  )
                }}
              >
                ⬇ Lesson Excel (.xlsx)
              </button>
              {course && lessons.length > 0 && (
                <button
                  type="button"
                  className="btn btn--secondary"
                  title="Export full attendance summary matrix across all lessons of this class"
                  onClick={() =>
                    handleExportDownload(
                      `/api/attendance/export-course?fmt=xlsx&course_id=${course.id}`,
                      'xlsx',
                      `course_summary_${course.id}`,
                    )
                  }
                >
                  📊 Full Course Summary (.xlsx)
                </button>
              )}
            </div>
          </section>

          {/* Quick Manual Add Student Form */}
          <section className="card">
            <h2>
              Add Student to "{course?.name}"
              {session ? ` (${formatLessonHeading(session)})` : ''}
            </h2>
            <form className="quick-form" onSubmit={handleAddStudent}>
              <div className="quick-form__grid">
                <label className="field">
                  <span>
                    Full Name <span className="required">*</span>
                  </span>
                  <input
                    type="text"
                    placeholder="Dana Cohen / דנה כהן"
                    value={newStudent.full_name}
                    onChange={(e) => setNewStudent((s) => ({ ...s, full_name: e.target.value }))}
                    required
                    dir="auto"
                  />
                </label>
                <label className="field">
                  <span>ID Number (ת״ז)</span>
                  <input
                    type="text"
                    inputMode="numeric"
                    placeholder="123456789"
                    value={newStudent.id_number}
                    onChange={(e) => setNewStudent((s) => ({ ...s, id_number: e.target.value }))}
                  />
                </label>
                <label className="field">
                  <span>Email</span>
                  <input
                    type="email"
                    placeholder="student@ariel.ac.il"
                    value={newStudent.email}
                    onChange={(e) => setNewStudent((s) => ({ ...s, email: e.target.value }))}
                  />
                </label>
              </div>
              <div className="quick-form__footer">
                <label className="checkbox-label">
                  <input
                    type="checkbox"
                    checked={newStudent.mark_present}
                    onChange={(e) => setNewStudent((s) => ({ ...s, mark_present: e.target.checked }))}
                  />
                  <span>
                    Mark as נוכח (Present){session ? ` in ${formatLessonHeading(session)}` : ''}
                  </span>
                </label>
                <button
                  type="submit"
                  className="btn btn--primary"
                  disabled={busyAction === 'add-student'}
                >
                  {busyAction === 'add-student' ? 'Saving…' : '+ Add to Course Roster'}
                </button>
              </div>
            </form>
          </section>
        </div>
      </div>

      {/* Online Course Roster & Lesson Attendance Table */}
      <section className="card roster-card">
        <div className="roster-toolbar">
          <div>
            <h2>
              Online Course Roster — {course?.name}
              {session ? ` · ${formatLessonHeading(session)}` : ''}
            </h2>
            <p className="muted">
              Saved in database & auto-updated every 3 seconds · Showing {filteredStudents.length} of {students.length} students
            </p>
          </div>
          <div className="roster-toolbar__controls">
            <input
              type="search"
              className="search-input"
              placeholder="Search name, ID, or email…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              dir="auto"
            />
            <div className="filter-pills" role="group" aria-label="Filter by attendance status">
              {[
                { key: 'all', label: `All (${students.length})` },
                { key: 'present', label: `נוכח (${stats.present})` },
                { key: 'absent', label: `לא נוכח (${stats.absent})` },
                { key: 'unlisted', label: `Joined via QR (${stats.unlisted})` },
              ].map((tab) => (
                <button
                  key={tab.key}
                  type="button"
                  className={`filter-pill ${filter === tab.key ? 'is-active' : ''}`}
                  onClick={() => setFilter(tab.key)}
                >
                  {tab.label}
                </button>
              ))}
            </div>
          </div>
        </div>

        {loading && !dashboard ? (
          <p className="empty-state">Loading course roster…</p>
        ) : students.length === 0 ? (
          <div className="empty-state">
            <p>No students in the "{course?.name}" roster yet.</p>
            <p className="muted">
              Upload a roster file (<code>.xlsx</code> / <code>.csv</code>), add a student manually above, or open a
              lesson and let students check in via QR code (any student who registers via QR is automatically added
              to the course roster).
            </p>
          </div>
        ) : filteredStudents.length === 0 ? (
          <p className="empty-state">No students match the current filter.</p>
        ) : (
          <div className="table-wrap">
            <table className="roster-table">
              <thead>
                <tr>
                  <th>Full Name</th>
                  <th>ID Number (ת״ז)</th>
                  <th>Email</th>
                  <th>Presence (נוכח / לא נוכח)</th>
                  <th>Source</th>
                  <th>Check-in Time</th>
                  <th className="text-right">Lecturer Actions (Edit / Toggle)</th>
                </tr>
              </thead>
              <tbody>
                {filteredStudents.map((student) => {
                  const statusKey = student.status || 'absent'
                  const meta = STATUS_META[statusKey] || STATUS_META.absent
                  const isPresent = Boolean(student.is_present)
                  const isBusy = busyAction === `student-${student.id}`
                  const isEditing = editingStudentId === student.id

                  return (
                    <tr key={student.id} className={isPresent ? 'row--present' : ''}>
                      <td>
                        {isEditing ? (
                          <input
                            type="text"
                            className="inline-edit-input"
                            value={studentDraft.full_name}
                            onChange={(e) => setStudentDraft((d) => ({ ...d, full_name: e.target.value }))}
                            dir="auto"
                          />
                        ) : (
                          <>
                            <span className="student-name" dir="auto">{student.full_name}</span>
                            {statusKey === 'unlisted' && (
                              <span
                                className="badge badge--warning badge--tiny"
                                title="Joined via QR and automatically added to the course roster"
                              >
                                New via QR
                              </span>
                            )}
                          </>
                        )}
                      </td>
                      <td>
                        {isEditing ? (
                          <input
                            type="text"
                            className="inline-edit-input"
                            value={studentDraft.id_number}
                            onChange={(e) => setStudentDraft((d) => ({ ...d, id_number: e.target.value }))}
                            placeholder="ID Number"
                          />
                        ) : (
                          <code>{student.id_number || '—'}</code>
                        )}
                      </td>
                      <td className="cell-email">
                        {isEditing ? (
                          <input
                            type="email"
                            className="inline-edit-input"
                            value={studentDraft.email}
                            onChange={(e) => setStudentDraft((d) => ({ ...d, email: e.target.value }))}
                            placeholder="Email"
                          />
                        ) : (
                          student.email || '—'
                        )}
                      </td>
                      <td>
                        <span className={`badge ${isPresent ? 'badge--success' : 'badge--muted'}`}>
                          {isPresent ? '✅ נוכח (Present)' : '❌ לא נוכח (Not Present)'}
                        </span>
                      </td>
                      <td>
                        <span className={`badge ${meta.badge}`}>{meta.label}</span>
                      </td>
                      <td>
                        <code>{formatTime(student.checked_in_at)}</code>
                      </td>
                      <td className="text-right">
                        <div className="row-actions">
                          {isEditing ? (
                            <>
                              <button
                                type="button"
                                className="btn btn--primary btn--sm"
                                disabled={busyAction === `edit-student-${student.id}`}
                                onClick={() => handleSaveStudentEdit(student.id)}
                              >
                                Save
                              </button>
                              <button
                                type="button"
                                className="btn btn--ghost btn--sm"
                                onClick={() => setEditingStudentId(null)}
                              >
                                Cancel
                              </button>
                            </>
                          ) : (
                            <>
                              <button
                                type="button"
                                className={`btn btn--sm ${isPresent ? 'btn--ghost' : 'btn--secondary'}`}
                                disabled={isBusy}
                                onClick={() => handleManualToggle(student)}
                              >
                                {isBusy
                                  ? '…'
                                  : isPresent
                                    ? 'סמן כלא נוכח'
                                    : '✓ סמן כנוכח'}
                              </button>
                              <button
                                type="button"
                                className="btn btn--ghost btn--sm"
                                onClick={() => startEditStudent(student)}
                                title="Edit student details"
                              >
                                ✏️
                              </button>
                              <button
                                type="button"
                                className="btn btn--ghost btn--sm"
                                onClick={() => handleDeleteStudent(student)}
                                disabled={busyAction === `delete-student-${student.id}`}
                                title="Remove student from course roster"
                              >
                                🗑️
                              </button>
                            </>
                          )}
                        </div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  )
}
