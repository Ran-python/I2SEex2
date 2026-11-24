import { useEffect, useState } from 'react'
import { api } from '../api/client.js'
import { useRole } from '../context/RoleContext.jsx'

export default function StudentIdentityBar() {
  const { isStudent, studentIdentity, setStudentIdentity } = useRole()
  const [courses, setCourses] = useState([])
  const [students, setStudents] = useState([])
  const [customMode, setCustomMode] = useState(false)

  useEffect(() => {
    if (!isStudent) return
    let cancelled = false
    api
      .get('/api/courses')
      .then((list) => {
        if (cancelled || !Array.isArray(list) || list.length === 0) return
        setCourses(list)
        if (!studentIdentity.courseId || !list.some((c) => c.id === studentIdentity.courseId)) {
          setStudentIdentity({
            courseId: list[0].id,
            courseName: list[0].name,
          })
        }
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [isStudent, studentIdentity.courseId, setStudentIdentity])

  useEffect(() => {
    if (!isStudent || !studentIdentity.courseId) return
    let cancelled = false
    api
      .get(`/api/students?course_id=${studentIdentity.courseId}`)
      .then((list) => {
        if (cancelled || !Array.isArray(list)) return
        setStudents(list)
        if (list.length > 0 && !customMode) {
          const exists = studentIdentity.studentId && list.some((s) => s.id === studentIdentity.studentId)
          if (!exists) {
            setStudentIdentity({
              studentId: list[0].id,
              studentName: list[0].full_name,
            })
          }
        }
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [isStudent, studentIdentity.courseId, studentIdentity.studentId, customMode, setStudentIdentity])

  if (!isStudent) return null

  const handleCourseSelect = (event) => {
    const cid = Number(event.target.value)
    const found = courses.find((c) => c.id === cid)
    setCustomMode(false)
    setStudentIdentity({
      courseId: cid,
      courseName: found?.name || '',
      studentId: null,
      studentName: '',
    })
  }

  const handleStudentSelect = (event) => {
    const val = event.target.value
    if (val === '__custom__') {
      setCustomMode(true)
      return
    }
    setCustomMode(false)
    if (!val) {
      setStudentIdentity({ studentId: null, studentName: '' })
      return
    }
    const sid = Number(val)
    const found = students.find((s) => s.id === sid)
    if (found) {
      setStudentIdentity({
        studentId: found.id,
        studentName: found.full_name,
      })
    }
  }

  return (
    <div className="student-identity-bar" aria-label="Student Demo Login Bar">
      <div className="student-identity-bar__title">
        <span>🎓 Student Demo Login:</span>
        {studentIdentity.studentName ? (
          <strong className="badge badge--success" dir="auto">
            Logged in as {studentIdentity.studentName}
          </strong>
        ) : (
          <span className="badge badge--warning">Select your name below</span>
        )}
      </div>

      <div className="student-identity-bar__controls">
        <label className="student-identity-bar__field">
          <span>1. Select Course:</span>
          <select
            className="select-input"
            value={studentIdentity.courseId || ''}
            onChange={handleCourseSelect}
            dir="auto"
          >
            {courses.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </label>

        <label className="student-identity-bar__field">
          <span>2. Select Your Name:</span>
          {!customMode && students.length > 0 ? (
            <select
              className="select-input"
              value={studentIdentity.studentId || ''}
              onChange={handleStudentSelect}
              dir="auto"
            >
              <option value="">— Choose student from roster ({students.length}) —</option>
              {students.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.full_name} {s.id_number ? `(${s.id_number})` : ''}
                </option>
              ))}
              <option value="__custom__">+ Type another name…</option>
            </select>
          ) : (
            <div className="student-identity-bar__custom">
              <input
                type="text"
                className="select-input"
                placeholder="Type your full name…"
                value={studentIdentity.studentName}
                onChange={(e) =>
                  setStudentIdentity({
                    studentId: null,
                    studentName: e.target.value,
                  })
                }
                dir="auto"
              />
              {students.length > 0 && (
                <button
                  type="button"
                  className="btn btn--ghost btn--sm"
                  onClick={() => setCustomMode(false)}
                >
                  Roster List
                </button>
              )}
            </div>
          )}
        </label>
      </div>
    </div>
  )
}
