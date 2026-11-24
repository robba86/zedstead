// Display (lat/lng) <-> game (x/z) conversion, derived from the server's own
// bounds so the two can never disagree.
//
// This exists ONLY for read-only display (the cursor readout). Every write
// endpoint accepts lat/lng directly — never convert client-side and POST game
// coords. The vanilla editor did exactly that for adding player spawns, took
// offset_z as `north * scale` instead of `-south * scale`, and mirrored Z
// about the map centre; clicks landed on the wrong side of the map.
//
// Server (app/spawn_editor/converters.py):
//     lng = (x - offset_x) / scale        lat = (z - offset_z) / scale
// so the south-west corner is (-offset_z/scale, -offset_x/scale), giving:
//     offset_x = -sw_lng * scale          offset_z = -sw_lat * scale

export function latLngToGame(latlng, bounds, scale) {
  if (!bounds || !scale) return null
  const [swLat, swLng] = bounds.south_west
  return {
    x: (latlng.lng - swLng) * scale,
    z: (latlng.lat - swLat) * scale,
  }
}

// A point as the spawn test's X and Z take it: one decimal, inside 0..size.
// Used by the mod page's picker and the editor's spawn-test mode, which post
// metres because the spawn-test endpoint takes them (the server checks the
// edges again).
export function latLngToMetres(latlng, bounds, scale, size) {
  const game = latLngToGame(latlng, bounds, scale)
  if (!game) return null
  const limit = size || Infinity
  const clamp = (v) => Math.round(Math.min(Math.max(v, 0), limit) * 10) / 10
  return { x: clamp(game.x), z: clamp(game.z) }
}

export function gameToLatLng(x, z, bounds, scale) {
  if (!bounds || !scale) return null
  const [swLat, swLng] = bounds.south_west
  return { lat: z / scale + swLat, lng: x / scale + swLng }
}

// A popup shows a coordinate rounded to `digits`. While its text still reads
// exactly that, nobody edited it, so a Save sends the value as it was loaded
// rather than the rounded text: an untouched Z moved by up to 5 cm on every
// Save when the popup wrote back what it displayed (test pass 1, F10).
export function keepUntouched(text, original, digits) {
  return text === original.toFixed(digits) ? original : parseFloat(text)
}
