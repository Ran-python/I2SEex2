import { useRole } from '../../context/RoleContext.jsx'
import { useLocation } from '../../router.jsx'
import LecturerDashboard from './lecturer/LecturerDashboard.jsx'
import StudentCheckIn from './student/StudentCheckIn.jsx'

export default function AttendancePage() {
  const { isLecturer } = useRole()
  const { query } = useLocation()

  // Students arriving from the QR code (/attendance/checkin?token=...) start in
  // Student View automatically (see RoleContext).
  return isLecturer ? <LecturerDashboard /> : <StudentCheckIn urlToken={query.get('token')} />
}
