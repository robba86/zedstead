import { createContext, useContext } from 'react'

// Who is signed in and what they may do (TODO B4, plans/43 step 5c).
//
// **This is courtesy, not a permission.** Every route the backend refuses is
// refused by `app/web/deps.py::require_session` whatever the browser believes,
// and the tests that matter are `tests/test_permissions.py`. What this buys is
// a panel that does not advertise buttons whose only outcome is a 403.
//
// The order mirrors `app/web/users.py::ROLES` — three strings, ascending.
// Keep them in step; the frontend copy exists because a nav bar cannot wait
// for a round trip per link.
export const ROLES = ['viewer', 'moderator', 'admin']

// An unknown role is never privileged, the same direction `role_at_least`
// takes on the backend: a value nobody recognises must cost access, not grant
// it. That covers the case worth thinking about — a future role the browser's
// cached bundle has never heard of.
export function roleAtLeast(role, minimum) {
  const held = ROLES.indexOf(role)
  const needed = ROLES.indexOf(minimum)
  if (held < 0 || needed < 0) return false
  return held >= needed
}

// `role: ''` until /api/auth/me answers. App renders a loading screen until
// then, so no page sees it — but `roleAtLeast('', anything)` is false, so if
// one ever did it would under-show rather than over-show.
//
// `totp` is whether *this* caller has a second factor (TODO B5) and `setTotp`
// lets the Account page reflect its own change without a round trip. Neither
// is a permission: the second factor is checked at login, by the API.
//
// `instance` is the server this panel serves (plans/62 step 1), named in the
// sidebar and the tab title. It rides along because it comes from the same
// /api/auth/me answer; it is not a permission either. `instances` (with
// `startup`, the instance unprefixed URLs serve) lists what the sidebar can
// switch to, and is empty with a single instance (plans/62 step 7);
// `refreshMe` re-reads it when the list changes.
const RoleContext = createContext({
  username: '',
  role: '',
  totp: false,
  instance: { id: '', label: '', count: 0 },
  instances: [],
  startup: '',
  install: '',
  setTotp: () => {},
  setInstance: () => {},
  refreshMe: () => {},
})

export function RoleProvider({ value, children }) {
  return <RoleContext.Provider value={value}>{children}</RoleContext.Provider>
}

export function useRole() {
  return useContext(RoleContext)
}

// Whether the signed-in role may use a control that needs *minimum*. The
// minimum is the backend's own classification (`app/web/permissions.py`):
// `moderator` for a route in MODERATOR_WRITES, `admin` for every other write.
// A control the role cannot use is hidden rather than disabled — it only ever
// answers 403 — which is plans/43 §4's rule and the sidebar's.
export function useCan(minimum) {
  return roleAtLeast(useRole().role, minimum)
}
