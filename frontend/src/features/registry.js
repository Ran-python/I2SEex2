// Central list of platform features (Phase 2: All 5 features active).
import AttendancePage from './attendance/AttendancePage.jsx'
import CommentsPage from './comments/CommentsPage.jsx'
import HomeworkReviewPage from './homework/HomeworkReviewPage.jsx'
import PollsPage from './polls/PollsPage.jsx'
import LectureSummaryPage from './summary/LectureSummaryPage.jsx'

export const FEATURES = [
  {
    key: 'attendance',
    path: '/attendance',
    label: 'Attendance',
    icon: '✅',
    ready: true,
    lecturerOnly: false,
    component: AttendancePage,
    description: 'QR-based attendance tracking with Wi-Fi subnet lock & AI classroom photo headcount.',
  },
  {
    key: 'comments',
    path: '/comments',
    label: 'Live Comments',
    icon: '💬',
    ready: true,
    lecturerOnly: false,
    component: CommentsPage,
    description: 'Real-time and offline comments & suggestions with course/lesson tagging, anonymity, and private lecturer notes.',
  },
  {
    key: 'polls',
    path: '/polls',
    label: 'Polls',
    icon: '📊',
    ready: true,
    lecturerOnly: false,
    component: PollsPage,
    description: 'On-the-spot KISS polls with scannable QR code and live bar-chart results.',
  },
  {
    key: 'summary',
    path: '/summary',
    label: 'Lecture Summary',
    icon: '🧠',
    ready: true,
    lecturerOnly: true,
    component: LectureSummaryPage,
    description: 'Lecturer-only ML lecture video summary and pedagogical evaluation report.',
  },
  {
    key: 'homework',
    path: '/homework',
    label: 'Homework Review',
    icon: '📝',
    ready: true,
    lecturerOnly: false,
    component: HomeworkReviewPage,
    description: 'Deep LLM homework review with peer comparison and lecturer cross-student matrix.',
  },
]

export const DEFAULT_FEATURE = FEATURES[0]

export function findFeature(path) {
  return FEATURES.find((f) => path === f.path || path.startsWith(`${f.path}/`)) || null
}
