// The tab bars of the pages that have one, in one small module (plans/75).
//
// Each page imports its own list from here, and so does the Ctrl+K palette
// (searchSources.js), so a tab is searchable the moment it exists and a
// renamed tab is renamed in both. They live here rather than in the page
// modules because importing a page to read its constant would pull the whole
// page into the palette's chunk. tests/test_web_search.py checks that every
// page takes its list from here.
//
// Module-level constants on purpose: useUrlTabs memoises on them.

export const BACKUPS_TABS = ['Quick Backups', 'Full Backups', 'Bundles', 'Offsite']

// The middle names say whose the data is (2026-11-03): the DayZ server's own
// logs, and what Zedstead tracks and keeps of the config files. Change history
// (ChangeTimeline.jsx) replaced Config changes and Saved versions that day.
export const ANALYSIS_TABS = [
  'Economy',
  'Server logs',
  'Change history',
  'Performance',
]
// Collisions is hidden until its scan reports real problems instead of
// thousands of vanilla overlaps (ROADMAP.md, Analysis). CollisionsTab in
// AnalysisPage.jsx and GET /api/analysis/collisions stay. The Map Editor keeps
// its own collision scan. To show the tab again, put 'Collisions' back above.
// Economy's subtabs, shown only while Zedstead holds the stock types.xml: the
// items vanilla has, then the mod and custom items it lacks.
export const ANALYSIS_ECONOMY_SUBTABS = ['Vanilla items', 'Mod and custom items']
// Their old names, so a bookmark or an old ?tab= link still lands.
export const ANALYSIS_TAB_RENAMES = {
  logs: 'Server logs',
  changes: 'Change history',
  versions: 'Change history',
  'config changes': 'Change history',
  'saved versions': 'Change history',
  timeline: 'Change history',
}

// One tab (or Mods subtab) per EditorSpec.group — the backend's own
// categorisation, not a second taxonomy. The mod groups sit one level down,
// under Mods.
// Custom is a tab of its own next to Mods: it is the report over every
// mod's XML, not one mod's editors.
export const CONFIGS_MOD_XML_TAB = 'Custom'
export const CONFIGS_TABS = ['Server', 'Mission', 'Mods', CONFIGS_MOD_XML_TAB]
// Mods' subtabs are EditorSpec.group values, one per mod.
export const CONFIGS_MODS_SUBTABS = ['TraderPlus', 'Expansion', 'Dynamic AI', 'RadZone', 'ToxicZone']

export const PLAYERS_TABS = ['Online', 'Sessions', 'Stats', 'Bans', 'Priority', 'Whitelist', 'Console', 'Chat']
// The console's history is what moderators have run (GET /api/rcon/history is
// moderator-only), so a viewer does not get the tab at all.
export const PLAYERS_MODERATOR_TABS = ['Console']

export const PRESETS_TABS = ['Presets', 'Quick Tweaks', 'Weather']

export const INSTANCES_TABS = ['On this machine', 'Add an instance']

export const SCHEDULER_TABS = ['Tasks', 'New task']

export const MODS_TABS = ['Installed', 'Install', 'Conflicts', 'Updates', 'Workshop Cache']
// The different ways to install a mod — sub-tabs under the Install tab.
// Supported comes first: the mods zsm has editors for are what most people
// came to install. app/web/routers/search.py names the same list.
export const MODS_INSTALL_SUBTABS = ['Supported', 'By Name', 'By ID', 'Workshop', 'Search', 'Expansion']

// The design reference (/design, plan 89): the shared parts, then every
// generic component.
export const DESIGN_TABS = ['Parts', 'Components']
