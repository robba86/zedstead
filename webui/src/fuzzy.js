// Tiny dependency-free fuzzy matcher for the config/settings filter boxes.
//
// Subsequence matching: every character of the needle must appear in the
// haystack in order, but not necessarily adjacently — so "srvpw" finds
// "serverPassword" and "mxpl" finds "maxPlayers". Plain substring filters
// (what the rest of the UI uses) can't do that, and config keys are exactly
// the camelCase/snake_case shapes where it pays off.

const BOUNDARY = /[\s._\-/[\]:]/

// Scoring weights — relative magnitudes are what matter, not the absolute
// numbers. Consecutive runs and word starts are what make a match feel right.
const SCORE_MATCH = 8
const SCORE_CONSECUTIVE = 10
const SCORE_BOUNDARY = 12
const SCORE_CAMEL = 10
const PENALTY_GAP = 1
const BONUS_PREFIX = 20
const BONUS_FULL_SUBSTRING = 25

/**
 * Score `needle` against `haystack`. Returns null when there is no match —
 * higher is better. An empty needle matches everything with score 0.
 */
export function fuzzyScore(needle, haystack) {
  if (!needle) return 0
  if (!haystack) return null

  const hay = String(haystack)
  const lowNeedle = needle.toLowerCase()
  const lowHay = hay.toLowerCase()

  if (lowNeedle.length > lowHay.length) return null

  let score = 0
  let hayIndex = 0
  let lastMatch = -1

  for (const char of lowNeedle) {
    const found = lowHay.indexOf(char, hayIndex)
    if (found === -1) return null

    score += SCORE_MATCH
    if (found === lastMatch + 1) {
      score += SCORE_CONSECUTIVE
    } else {
      // Gaps cost, but never enough to push a real match below zero.
      score -= Math.min(found - hayIndex, 10) * PENALTY_GAP
    }

    const prev = found > 0 ? hay[found - 1] : ''
    if (found === 0 || BOUNDARY.test(prev)) score += SCORE_BOUNDARY
    else if (prev && prev === prev.toLowerCase() && hay[found] !== hay[found].toLowerCase()) {
      score += SCORE_CAMEL // camelCase hump
    }

    lastMatch = found
    hayIndex = found + 1
  }

  if (lowHay.startsWith(lowNeedle)) score += BONUS_PREFIX
  else if (lowHay.includes(lowNeedle)) score += BONUS_FULL_SUBSTRING

  return score
}

/**
 * Best score of `needle` across several haystacks (label, key, help text…).
 * Returns null when none of them match.
 */
export function fuzzyScoreAny(needle, ...haystacks) {
  if (!needle) return 0
  let best = null
  for (const hay of haystacks) {
    const score = fuzzyScore(needle, hay)
    if (score !== null && (best === null || score > best)) best = score
  }
  return best
}

/**
 * Filter `items` by `needle`, ranked best-first. `keyFn` returns the string (or
 * array of strings) to match against. An empty needle returns the input list
 * untouched, in its original order.
 */
export function fuzzyFilter(items, needle, keyFn) {
  const trimmed = (needle || '').trim()
  if (!trimmed) return items

  const scored = []
  for (const item of items) {
    const keys = keyFn(item)
    const score = Array.isArray(keys)
      ? fuzzyScoreAny(trimmed, ...keys)
      : fuzzyScore(trimmed, keys)
    if (score !== null) scored.push({ item, score })
  }
  // Stable within equal scores: Array.prototype.sort is stable in ES2019+.
  scored.sort((a, b) => b.score - a.score)
  return scored.map((s) => s.item)
}

/**
 * Fuzzy on the short names (`keyFn`: key, label), then the rest of `items`
 * whose long text (`textFn`: help) holds every typed word as a substring.
 * Fuzzy over a whole help paragraph found the letters one by one anywhere:
 * "fps" matched 21 of 69 serverDZ.cfg fields, Password among them (night
 * test 2026-11-22), and "zzzz-nothing" matched a setting.
 */
export function fuzzyKeysThenText(items, needle, keyFn, textFn) {
  const trimmed = (needle || '').trim()
  if (!trimmed) return items
  const words = trimmed.toLowerCase().split(/\s+/)
  const byKey = fuzzyFilter(items, trimmed, keyFn)
  const textHas = (item) => {
    const text = (textFn(item) || '').toLowerCase()
    return words.every((w) => text.includes(w))
  }
  return [...byKey, ...items.filter((item) => !byKey.includes(item) && textHas(item))]
}
