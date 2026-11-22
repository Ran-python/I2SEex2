// Central list of platform features. The Navbar and the router are both driven
// by this registry, so enabling an upcoming feature = flipping `ready` and
// pointing `component` at its page.
import AttendancePage from './attendance/AttendancePage.jsx'
import ComingSoon from '../components/ComingSoon.jsx'

export const FEATURES = [
  {
    key: 'attendance',
    path: '/attendance',
    label: 'Attendance',
    icon: '✅',
    ready: true,
    component: AttendancePage,
    description: 'QR-based attendance tracking restricted to the classroom Wi-Fi.',
  },
  {
    key: 'comments',
    path: '/comments',
    label: 'Live Comments',
    icon: '💬',
    ready: false,
    component: ComingSoon,
    description: 'Students send live comments & suggestions during the lecture, anonymously or by name.',
  },
  {
    key: 'polls',
    path: '/polls',
    label: 'Polls',
    icon: '📊',
    ready: false,
    component: ComingSoon,
    description: 'On-the-spot polls created by the lecturer, with live results.',
  },
  {
    key: 'summary',
    path: '/summary',
    label: 'Lecture Summary',
    icon: '🧠',
    ready: false,
    component: ComingSoon,
    description: 'Machine-learning generated summary of the lecture.',
  },
  {
    key: 'homework',
    path: '/homework',
    label: 'Homework Review',
    icon: '📝',
    ready: false,
    component: ComingSoon,
    description: 'LLM-assisted review and feedback on homework submissions.',
  },
]

export const DEFAULT_FEATURE = FEATURES[0]

export function findFeature(path) {
  return FEATURES.find((f) => path === f.path || path.startsWith(`${f.path}/`)) || null
}
