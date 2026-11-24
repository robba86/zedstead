// Fetch wrapper for the zsm web API.
//
// - Sends the session cookie (credentials: "same-origin").
// - A 401 means the session is gone: redirect to /login instead of letting
//   every caller special-case it. **Except on /api/auth/login**, where a 401
//   is the expected answer to a wrong password and there is nowhere to
//   redirect to — the user is already on /login. That path falls through to
//   the normal error handling below, so the form can show what the backend
//   actually said ("Invalid password") instead of "Not authenticated", and so
//   it can read `totp_required` off the body (TODO B5).
// - Non-2xx responses throw an Error whose message is the backend's `detail`
//   (FastAPI's HTTPException shape) so callers can show it directly (e.g. in
//   a toast) without knowing the response shape. The Error also carries
//   `status` and `body` for the rare caller that needs more than a sentence.
// - A 401 on a write leaves a notice for LoginPage in sessionStorage. The
//   redirect unloads the page before the caller's error toast can be read, so
//   without it a Save refused for want of a session (after a panel restart,
//   which ends every session) looked like it had worked. Logout is not a
//   change and leaves none.
// - A 401 that says why (any detail but the generic "Not authenticated", e.g.
//   accounts that cannot be read, so only the single password signs in) leaves
//   that sentence as the notice on any method, and is what the Error says.
//   Dropping it sent the user to a login form that would refuse their
//   account's password without a word (plans/68 review B4).
// - Every path goes to this page's instance (`withBase`, plans/62): `/api/…`
//   is `/i/<id>/api/…` on another instance's page. /login is the panel's and
//   stays unprefixed; `?i=` tells the form which instance to offer first.

import { INSTANCE_ID, withBase } from './instance'

export const LOGIN_NOTICE_KEY = 'zsm.login.notice'
const LOGIN_NOTICE =
  'Your session had ended, so your last change was not saved. Sign in and make it again.'
const NOT_SAVED = 'Your last change was not saved.'
const NOT_AUTHENTICATED = 'Not authenticated'

// The sentence a 401 gives for refusing the session, or '' for the generic
// one (no session, or an expired one), which the login page needs no help with.
async function refusalReason(resp) {
  try {
    const detail = (await resp.json())?.detail
    return typeof detail === 'string' && detail !== NOT_AUTHENTICATED ? detail : ''
  } catch {
    return ''
  }
}

async function request(path, options = {}) {
  const resp = await fetch(withBase(path), {
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  })

  if (resp.status === 401 && path !== '/api/auth/login') {
    const reason = await refusalReason(resp)
    if (window.location.pathname !== '/login') {
      const method = (options.method || 'GET').toUpperCase()
      const write = method !== 'GET' && path !== '/api/auth/logout'
      const notice = reason ? (write ? `${reason} ${NOT_SAVED}` : reason) : write ? LOGIN_NOTICE : ''
      if (notice) {
        try {
          sessionStorage.setItem(LOGIN_NOTICE_KEY, notice)
        } catch {
          // best-effort: storage can be unavailable; the redirect still happens
        }
      }
      window.location.href = loginHref()
    }
    throw new Error(reason || NOT_AUTHENTICATED)
  }

  const text = await resp.text()
  let body = null
  if (text) {
    try {
      body = JSON.parse(text)
    } catch {
      body = text
    }
  }

  if (!resp.ok) {
    const detail = (body && body.detail) || resp.statusText || 'Request failed'
    const err = new Error(typeof detail === 'string' ? detail : JSON.stringify(detail))
    err.status = resp.status
    err.body = body
    throw err
  }

  return body
}

// The sign-in page, remembering which instance the visitor was on.
export function loginHref() {
  return INSTANCE_ID ? `/login?i=${encodeURIComponent(INSTANCE_ID)}` : '/login'
}

export const api = {
  get: (path) => request(path),
  post: (path, data) =>
    request(path, {
      method: 'POST',
      body: data !== undefined ? JSON.stringify(data) : undefined,
    }),
  put: (path, data) => request(path, { method: 'PUT', body: JSON.stringify(data) }),
  // A file as the whole body, not a multipart form: one file going to an
  // already-named destination needs no form fields, and multipart would mean
  // adding python-multipart to every install for one button. The explicit
  // Content-Type overrides the JSON default above.
  postFile: (path, file) =>
    request(path, {
      method: 'POST',
      body: file,
      headers: { 'Content-Type': 'application/octet-stream' },
    }),
  del: (path, data) =>
    request(path, {
      method: 'DELETE',
      body: data !== undefined ? JSON.stringify(data) : undefined,
    }),
}
