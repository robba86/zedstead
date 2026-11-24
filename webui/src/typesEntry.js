// One types.xml entry's save, shared by the Types page and the map editor's
// Mod items tab: both send PUT /api/types/entry with the body built here.

// The number cells an entry form edits, and their labels.
export const NUMBER_CELLS = ['nominal', 'min', 'lifetime', 'restock']
export const NUMBER_LABELS = { nominal: 'Nominal', min: 'Min', lifetime: 'Lifetime', restock: 'Restock' }

// A types file's path inside its mission ("db/types.xml"), or its name when
// it lies outside one. The full path stays in a tooltip.
export function shortPath(path) {
  const m = String(path).match(/mpmissions[\\/][^\\/]+[\\/](.+)$/)
  return m ? m[1] : String(path).split(/[\\/]/).pop()
}

/**
 * The PUT /api/types/entry body for *entry* with the cell edits *override*
 * and the nested-tree edits *tree*, as `{ body }`, or `{ error }` when a
 * number cell is not a whole number.
 *
 * Only the cells edited go: a value the row shows for a child the type leaves
 * out (a mod's override in custom/) must not be written back. An emptied cell
 * is refused here, where Number('') would have sent 0. Only the edited keys of
 * the tree go, each with its value as loaded: the server refuses a key the
 * file has changed since.
 */
export function typesEntryBody(entry, override = {}, tree = null) {
  const numbers = {}
  for (const f of NUMBER_CELLS) {
    if (!(f in override)) continue
    const raw = String(override[f]).trim()
    if (!/^-?\d+$/.test(raw)) {
      return { error: `${f} must be a whole number${raw === '' ? '; it cannot be left empty' : ''}.` }
    }
    numbers[f] = Number(raw)
  }
  return {
    body: {
      source_file: entry.source_file,
      name: entry.name,
      ...numbers,
      ...('category' in override && { category: override.category }),
      ...(tree && {
        nested: tree,
        nested_base: Object.fromEntries(Object.keys(tree).map((k) => [k, entry.nested[k]])),
      }),
    },
  }
}
