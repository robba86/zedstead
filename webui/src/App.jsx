import { createContext, lazy, useCallback, useContext, useEffect, useState } from 'react'
import {
  createBrowserRouter,
  createRoutesFromElements,
  Navigate,
  Outlet,
  Route,
  RouterProvider,
  useLocation,
} from 'react-router-dom'
import { api } from './api'
import { BASE } from './instance'
import Layout from './components/Layout.jsx'
import RoleGate, { NotFound } from './components/RoleGate.jsx'
import { RoleProvider } from './role.jsx'
import LoginPage from './pages/LoginPage.jsx'
import { GuideRoute } from './guide.jsx'
import { UnsavedChangesGuard } from './useUnsavedChanges.js'
import { LINKS } from './crossLinks.js'
import LoadingState from './components/LoadingState.jsx'

// Every page below the shell is lazy. They all used to be static imports, which
// put each page *and its heaviest leaf dependency* in the entry chunk — marked
// + dompurify (66KB) for Help alone, prism-react-editor for the editors,
// dnd-kit for the dashboard grid — so the login screen downloaded the markdown
// renderer. Layout wraps <Outlet /> in a <Suspense>, so a navigation swaps the
// content area while the sidebar and breadcrumbs stay put.
//
// LoginPage is the exception: it is the first paint for an anonymous visitor
// and it renders outside Layout, so it stays eager rather than flashing a
// fallback with no shell around it. GuideRoute likewise — Layout's GuideDrawer
// already pulls guide.jsx into the entry chunk, so deferring it buys nothing.
const DashboardPage = lazy(() => import('./pages/DashboardPage.jsx'))
const DoctorPage = lazy(() => import('./pages/DoctorPage.jsx'))
const UpdatePage = lazy(() => import('./pages/UpdatePage.jsx'))
const ModsPage = lazy(() => import('./pages/ModsPage.jsx'))
const ModPage = lazy(() => import('./pages/ModPage.jsx'))
const PlayersPage = lazy(() => import('./pages/PlayersPage.jsx'))
const BackupsPage = lazy(() => import('./pages/BackupsPage.jsx'))
const SchedulerPage = lazy(() => import('./pages/SchedulerPage.jsx'))
const SettingsPage = lazy(() => import('./pages/SettingsPage.jsx'))
const InstancesPage = lazy(() => import('./pages/InstancesPage.jsx'))
const AccountPage = lazy(() => import('./pages/AccountPage.jsx'))
const CleanupPage = lazy(() => import('./pages/CleanupPage.jsx'))
const ConfigsPage = lazy(() => import('./pages/ConfigsPage.jsx'))
const PresetsPage = lazy(() => import('./pages/PresetsPage.jsx'))
const QuickSetupPage = lazy(() => import('./pages/QuickSetupPage.jsx'))
const MyServerPage = lazy(() => import('./pages/MyServerPage.jsx'))
const AdoptPage = lazy(() => import('./pages/AdoptPage.jsx'))
const MapsPage = lazy(() => import('./pages/MapsPage.jsx'))
const MapEditorPage = lazy(() => import('./pages/MapEditorPage.jsx'))
const AnalysisPage = lazy(() => import('./pages/AnalysisPage.jsx'))
const HelpPage = lazy(() => import('./pages/HelpPage.jsx'))
const DesignPage = lazy(() => import('./pages/DesignPage.jsx'))

// Who is signed in and how to change that, for the route elements below. The
// router is created once at module level (a data router must not be rebuilt
// on render; it is what useBlocker needs, plans/55 C), so its elements cannot
// close over App's state and read it from here instead.
const AuthContext = createContext(null)

function useAuth() {
  return useContext(AuthContext)
}

function LoginRoute() {
  const { authState, loadMe } = useAuth()
  return authState === 'authed' ? <Navigate to="/" replace /> : <LoginPage onLogin={loadMe} />
}

function RequireAuth() {
  const { authState } = useAuth()
  if (authState === 'anon') return <Navigate to="/login" replace />
  return <Outlet />
}

// The shell, plus the one unsaved-changes guard: it must sit inside the router
// and above every page, and appear once (useUnsavedChanges.js says why).
// Mod XML moved into Configs as its own Custom tab. The old address
// stays a redirect, hash included, so /configs/mod-xml#manual-files still scrolls.
function ModXmlRedirect() {
  const { hash } = useLocation()
  return <Navigate to={`${LINKS['mod-xml'].to}${hash}`} replace />
}

function ShellRoute() {
  const { logout } = useAuth()
  return (
    <>
      <UnsavedChangesGuard />
      <Layout onLogout={logout} />
    </>
  )
}

const router = createBrowserRouter(
  createRoutesFromElements(
    <>
      <Route path="/login" element={<LoginRoute />} />
      <Route element={<RequireAuth />}>
        <Route element={<ShellRoute />}>
          {/* One gate for every page, resolved from routes.js's minRole.
              Inside Layout on purpose: a refusal keeps the sidebar, so the
              reader can go somewhere they can actually use. */}
          <Route element={<RoleGate />}>
            <Route path="/" element={<DashboardPage />} />
            {/* The guide is a drawer now; the old path opens it. */}
            <Route path="/wizard" element={<GuideRoute />} />
            <Route path="/doctor" element={<DoctorPage />} />
            <Route path="/update" element={<UpdatePage />} />
            <Route path="/mods" element={<ModsPage />} />
            <Route path="/mods/:id" element={<ModPage />} />
            <Route path="/players" element={<PlayersPage />} />
            <Route path="/backups" element={<BackupsPage />} />
            <Route path="/scheduler" element={<SchedulerPage />} />
            <Route path="/instances" element={<InstancesPage />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="/account" element={<AccountPage />} />
            <Route path="/cleanup" element={<CleanupPage />} />
            <Route path="/configs" element={<ConfigsPage />} />
            <Route path="/configs/mod-xml" element={<ModXmlRedirect />} />
            <Route path="/configs/:id" element={<ConfigsPage />} />
            <Route path="/presets" element={<PresetsPage />} />
            <Route path="/quick-setup" element={<QuickSetupPage />} />
            <Route path="/my-server" element={<MyServerPage />} />
            <Route path="/adopt" element={<AdoptPage />} />
            <Route path="/maps" element={<MapsPage />} />
            <Route path="/map-editor" element={<MapEditorPage />} />
            <Route path="/analysis" element={<AnalysisPage />} />
            <Route path="/help" element={<HelpPage />} />
            <Route path="/design" element={<DesignPage />} />
          </Route>
          {/* Outside RoleGate: an unknown path has no role to check. */}
          <Route path="*" element={<NotFound />} />
        </Route>
      </Route>
    </>
  ),
  // `/i/<id>` on another instance's page (plans/62): every route below is
  // that instance's, and a <Link to="/mods"> stays on it.
  { basename: BASE || undefined }
)

const NO_INSTANCE = { id: '', label: '', count: 0 }
const ANON = { username: '', role: '', totp: false, instance: NO_INSTANCE, instances: [], startup: '', install: '' }

function meFrom(data) {
  return {
    username: data.username || '',
    role: data.role || '',
    totp: !!data.totp,
    instance: data.instance || NO_INSTANCE,
    instances: data.instances || [],
    startup: data.startup || '',
    install: data.install || '',
  }
}

export default function App() {
  const [authState, setAuthState] = useState('checking') // checking | authed | anon
  // Who is signed in, for RoleProvider. The same request that decides
  // authState answers it — the body was simply thrown away before
  // (plans/43 step 5c), so nothing extra is fetched.
  //
  // It is read once per page load and **goes stale** if an admin re-roles you
  // mid-session: the nav keeps what it was told until the next reload. That is
  // deliberate rather than polled, because the role here decides only what is
  // *shown*. `require_session` re-reads users.json on every request, so the API
  // has already stopped answering under the old role.
  const [me, setMe] = useState(ANON)

  const loadMe = useCallback(
    () =>
      api
        .get('/api/auth/me')
        .then((data) => {
          setMe(meFrom(data))
          setAuthState('authed')
        })
        .catch(() => {
          setMe(ANON)
          setAuthState('anon')
        }),
    [],
  )

  // The Account page flips this when you enrol or un-enrol, so the card it
  // lives on shows the new state without a round trip. Nothing reads it but
  // that card — the API is what enforces the second factor, at login.
  const setTotp = useCallback((value) => setMe((prev) => ({ ...prev, totp: !!value })), [])
  // The Instances page passes what it just listed, so a rename of this
  // panel's instance reaches the sidebar and the tab title without a reload.
  const setInstance = useCallback(
    (value) => setMe((prev) => ({ ...prev, instance: { ...prev.instance, ...value } })),
    [],
  )

  // The instance list changed (one added, removed or renamed anywhere). Only
  // a success is applied: a blip must not sign the page out, and a real 401
  // already goes to /login through api.js.
  const refreshMe = useCallback(
    () =>
      api
        .get('/api/auth/me')
        .then((data) => setMe(meFrom(data)))
        .catch(() => {}),
    [],
  )

  const logout = useCallback(() => {
    setMe(ANON)
    setAuthState('anon')
  }, [])

  useEffect(() => {
    loadMe()
  }, [loadMe])

  if (authState === 'checking') {
    return <LoadingState screen />
  }

  return (
    <AuthContext.Provider value={{ authState, loadMe, logout }}>
      <RoleProvider value={{ ...me, setTotp, setInstance, refreshMe }}>
        <RouterProvider router={router} />
      </RoleProvider>
    </AuthContext.Provider>
  )
}
