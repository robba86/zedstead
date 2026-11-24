// The serverDZ.cfg answers that My server and Quick setup's "Your server" card
// both ask: read from GET /api/configs/serverdz, so both show the same values.

// Under "How many players?" on both pages. Every player draws on the same
// loot economy, so a smaller cap leaves more for each one.
export const PLAYER_COUNT_HINT =
  'All players share the same loot. A smaller cap leaves more for each player.'

// The editor's fields as `{ key: field }`.
export function fieldsByKey(config) {
  return Object.fromEntries(config.sections.flatMap((s) => s.fields).map((f) => [f.key, f]))
}

// A field's value, or its default when the file does not set it.
export function fieldValue(fields, key) {
  return fields[key]?.value ?? fields[key]?.default
}

// The name, the two passwords and the player count, as form values.
export function serverAnswers(fields) {
  const password = fields.password?.value ?? ''
  return {
    hostname: fieldValue(fields, 'hostname') ?? '',
    passwordOn: password !== '',
    password,
    passwordAdmin: fields.passwordAdmin?.value ?? '',
    maxPlayers: String(fieldValue(fields, 'maxPlayers') ?? ''),
  }
}
