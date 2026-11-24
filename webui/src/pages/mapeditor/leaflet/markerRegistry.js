// Metadata attached to Leaflet layers, without monkey-patching them.
//
// The vanilla editor stashed `marker._spawnMeta` / `marker._selectionMeta`
// (the latter carrying a save(lat,lng) closure) directly on Leaflet objects,
// and the box-selection code walked layer groups sniffing for those
// properties. A WeakMap keeps the same lookup without colliding with
// Leaflet's own underscore-prefixed internals, and entries disappear when a
// marker is garbage collected after a layer rebuild.

const registry = new WeakMap()

export function setMeta(layer, meta) {
  registry.set(layer, meta)
  return layer
}

export function getMeta(layer) {
  return registry.get(layer) || null
}

export function hasMeta(layer) {
  return registry.has(layer)
}

/** Every layer in *group* (recursing into nested groups) that carries meta. */
export function collectWithMeta(group, predicate) {
  const found = []
  if (!group) return found
  group.eachLayer((layer) => {
    if (typeof layer.eachLayer === 'function' && !registry.has(layer)) {
      found.push(...collectWithMeta(layer, predicate))
      return
    }
    const meta = registry.get(layer)
    if (meta && (!predicate || predicate(meta, layer))) found.push({ layer, meta })
  })
  return found
}
