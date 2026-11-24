// The features no person has run on a real server yet. Each one carries a
// Beta badge (components/BetaBadge.jsx), which shows only for a key listed
// here: when a person tests a feature, deleting its line removes every badge
// it has. Read-only pages, Discord webhooks and CLI-only steps get no badge.
// Zedstead stays 0.x, and so beta, until people have tested all of them.
export const BETA_FEATURES = new Set([
  // Data at risk
  'update-merge', // a DayZ update that keeps mission edits, and the conflict choice
  'bundle-import',
  'backup-restore', // a full backup restored, also on another machine
  'offsite', // offsite backup and restore
  'adopt',
  'cleanup-mods', // removing a mod on /cleanup
  'instances', // a second instance, and two servers at once
  // Players and the service
  'restart-warnings', // restart with warning, and the join lock
  'moderation', // kick, ban, unban and messages
  'rcon-console',
  'self-update', // the Update Zedstead card
  'service-setup', // service install with linger, and start at boot
  // Checked only in Docker, never by a person
  'scheduler', // scheduled restarts and nightly updates
  'mod-rollback',
  'whitelist',
  'two-factor',
  'accounts', // viewer and co-admin accounts
  'mission-check', // a community mission's upstream check, on /maps
])
