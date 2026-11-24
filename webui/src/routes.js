// webui/src/routes.js
//
// Central route metadata: single source of truth for breadcrumb labels and
// the sidebar nav. Add a page here (once) and both the sidebar and the
// breadcrumb trail pick it up.
//
// - path:   the React Router path pattern (may contain :params).
// - label:  string, OR (params) => string for dynamic segments.
// - parent: path of the parent crumb (omit for top-level pages under Home).
// - nav:    true to appear in the sidebar (top-level pages only).
// - icon:   the sidebar glyph — a react-icons component from the Heroicons 2
//           set (`react-icons/hi2`, the HiOutline* / Hi* names). NOT the
//           Heroicons 1 set (`react-icons/hi`), whose names collide.
//           Exception: Cleanup uses PiBroom, since Heroicons has no broom.
// - fullBleed: true to drop the content padding (pages that fill the viewport,
//              e.g. the map editor).
// - action: {label, to} rendered as a link at the right of the breadcrumb row.
// - breadcrumbs: false to render no breadcrumb row (the map editor, where
//                the map needs the height).
// - rookie: true to stay in the sidebar in Rookie mode (rookie.jsx).
// - minRole: the least privileged role this page is of any use to — one of
//            role.jsx's ROLES. REQUIRED on every entry, and
//            tests/test_routes_roles.py fails the build if one is missing:
//            a default would decide a security-shaped question by omission,
//            in whichever direction the default happened to go. It hides a
//            nav link and answers the route with a refusal page; it is NOT
//            the permission. The API refuses regardless — see role.jsx.
//
// ORDER MATTERS: list static paths BEFORE the dynamic catch-all that could
// also match them (e.g. /configs/types before /configs/:id), because the
// trail resolver takes the FIRST matchPath() hit.

import { matchPath } from "react-router-dom";

import { roleAtLeast } from "./role.jsx";

import {
  HiOutlineArchiveBox,
  HiOutlineArrowPath,
  HiOutlineChartBar,
  HiOutlineClock,
  HiOutlineCog6Tooth,
  HiOutlineDocumentText,
  HiOutlineHeart,
  HiOutlineHome,
  HiOutlineMap,
  HiOutlineMapPin,
  HiOutlinePuzzlePiece,
  HiOutlineQuestionMarkCircle,
  HiOutlineSquare3Stack3D,
  HiOutlineSwatch,
  HiOutlineUsers,
  HiOutlineRocketLaunch,
  HiOutlineServer,
} from "react-icons/hi2";
// Heroicons has no broom, so Cleanup takes the Phosphor outline one.
import { PiBroom } from "react-icons/pi";

export const HOME = { path: "/", label: "Dashboard" };

// The documentation lives on the site, not in the panel (2026 Q4).
export const DOCS_URL = "https://zedstead.com/docs/";
export const PAGES_URL = `${DOCS_URL}web-ui/#the-pages`;
// The site's six-step guide: ?quick-guide opens it on the home page
// (zedstead-site src/theme.js openGuide).
export const QUICK_GUIDE_URL = "https://zedstead.com/?quick-guide";

export const ROUTES = [
  // First in the menu: a new server starts here (Layout.jsx sends the admin here once).
  { path: "/quick-setup", label: "Quick setup", nav: true, rookie: true, icon: HiOutlineRocketLaunch, minRole: "admin" },
  { path: "/", label: "Dashboard", nav: true, rookie: true, icon: HiOutlineHome, minRole: "viewer" },
  { path: "/my-server", label: "My server", nav: true, rookie: true, icon: HiOutlineServer, minRole: "admin" },
  // The guide is a drawer (components/GuideDrawer.jsx), not a page: the sidebar
  // has a Guide button instead of a nav link. The path stays listed because
  // `zsm:wizard` links (helpLinks.js resolves slugs against these paths) and
  // old bookmarks point at it — the route opens the drawer and bounces to the
  // Dashboard.
  { path: "/wizard", label: "Guide", minRole: "viewer" },
  { path: "/maps", label: "Maps", nav: true, rookie: true, icon: HiOutlineMap, minRole: "admin" },
  {
    path: "/map-editor",
    minRole: "admin",
    label: "Map Editor",
    nav: true,
    icon: HiOutlineMapPin,
    fullBleed: true,
    breadcrumbs: false,
  },
  { path: "/mods", label: "Mods", nav: true, rookie: true, icon: HiOutlinePuzzlePiece, minRole: "admin" },
  // The ref is a workshop id (canonical) or a mod name without its `@` — see
  // crossLinks.js::modLink. The label below is only the fallback: ModPage
  // publishes the mod's real name through crumbTitle.js once it has loaded.
  { path: "/mods/:id", label: (params) => params.id || "Mod", parent: "/mods", minRole: "admin" },
  { path: "/presets", label: "Presets", nav: true, rookie: true, icon: HiOutlineSwatch, minRole: "admin" },
  // Reached from the Guide's mods step and init; not a sidebar page (plans/78).
  { path: "/adopt", label: "Adopt a server", minRole: "admin" },
  { path: "/configs", label: "Configs", nav: true, icon: HiOutlineDocumentText, minRole: "viewer" },
  { path: "/configs/mod-xml", label: "Custom", parent: "/configs", minRole: "viewer" },
  // Types.xml and TraderPlus Prices open in ConfigsPage's drawer like any
  // /configs/:id editor (plans/91). Their entries stay for the breadcrumb's
  // name, which /configs/:id would spell as the bare id.
  { path: "/configs/types", label: "Types.xml", parent: "/configs", minRole: "viewer" },
  { path: "/configs/tp-price", label: "TraderPlus Prices", parent: "/configs", minRole: "viewer" },
  { path: "/configs/:id", label: (params) => params.id, parent: "/configs", minRole: "viewer" },
  { path: "/update", label: "Update", nav: true, icon: HiOutlineArrowPath, minRole: "admin" },
  { path: "/scheduler", label: "Scheduler", nav: true, icon: HiOutlineClock, minRole: "admin" },
  { path: "/players", label: "Players", nav: true, icon: HiOutlineUsers, minRole: "viewer" },
  { path: "/doctor", label: "Doctor", nav: true, rookie: true, icon: HiOutlineHeart, minRole: "viewer" },
  { path: "/analysis", label: "Analysis", nav: true, icon: HiOutlineChartBar, minRole: "viewer" },
  { path: "/cleanup", label: "Cleanup", nav: true, icon: PiBroom, minRole: "admin" },
  { path: "/backups", label: "Backups", nav: true, rookie: true, icon: HiOutlineArchiveBox, minRole: "admin" },
  // Several DayZ servers on one machine (TODO E1). Admin: add and clone pick a
  // server directory and ports.
  { path: "/instances", label: "Instances", nav: true, icon: HiOutlineSquare3Stack3D, minRole: "admin" },
  { path: "/settings", label: "Settings", nav: true, icon: HiOutlineCog6Tooth, minRole: "admin" },
  // Not in the nav: it is reached from the sidebar's account button, beside
  // Log out, where "who am I signed in as" already belongs. `viewer` because
  // everything on it changes the caller's own account — their password and
  // their second factor — which no role should have to ask permission for
  // (TODO B5; /settings being admin-only is what left a moderator unable to
  // rotate their own password at all).
  { path: "/account", label: "Your account", minRole: "viewer" },
  // The help is the docs on zedstead.com since 2026 Q4 (it was app/data/HELP.md,
  // rendered here). `href` makes the sidebar entry a link out, in a new tab;
  // the /help route stays for bookmarks and says where the help went. Since
  // 2026 Q4 the entry is "Docs" and opens the docs index: the help page's
  // "Getting started" was the Guide's steps again (role 1 user test).
  { path: "/help", label: "Docs", nav: true, rookie: true, href: DOCS_URL, icon: HiOutlineQuestionMarkCircle, minRole: "viewer" },
  // Plan 89 D2: every shared part in every state (webui/DESIGN.md). By URL
  // only, so no nav entry; it calls no API, so any signed-in role can see it.
  { path: "/design", label: "Design reference", minRole: "viewer" },
];

// Sidebar nav items derived from ROUTES (labels defined once). An item with
// `href` leaves the panel; `to` is still its route, for the role check and as
// the React key.
export const NAV_ITEMS = ROUTES.filter((r) => r.nav).map((r) => ({
  to: r.path,
  href: r.href,
  label: r.label,
  Icon: r.icon,
  minRole: r.minRole,
  rookie: r.rookie,
}))

// The sidebar for *role*. A link a role cannot use is not disabled, it is
// gone: a greyed-out Mods entry on a viewer's panel invites a support question
// about a decision their admin already made.
//
// Rookie mode shows only the `rookie` entries. It filters the sidebar only:
// every route stays open.
// `quickSetupDone` hides Quick setup once the server is set up (quickSetupState.js).
// `menu`, the viewer's own list of paths (rookie.jsx), replaces Rookie mode.
export function navItemsFor(role, rookie = false, quickSetupDone = false, menu = null) {
  const own = menu && new Set(menu);
  return NAV_ITEMS.filter(
    (item) =>
      roleAtLeast(role, item.minRole) &&
      (own ? own.has(item.to) : !rookie || item.rookie) &&
      !(quickSetupDone && item.to === "/quick-setup")
  );
}

// The role a path needs. `matchPath` and FIRST hit, the same resolution the
// breadcrumb trail uses — which is why the ORDER MATTERS note at the top of
// this file governs this too. A path matching nothing gets `admin`: an
// unrouted URL is either a typo or a page somebody forgot to register, and
// neither should render for a viewer.
export function minRoleFor(pathname) {
  const hit = ROUTES.find((r) => matchPath(r.path, pathname));
  return hit?.minRole || "admin";
}

export const FULL_BLEED_PATHS = ROUTES.filter((r) => r.fullBleed).map((r) => r.path);
