// The bound on every bulk multiplier box (Types, TraderPlus prices). The
// backend refuses the same (app/web/multipliers.py MAX_MULTIPLIER); checking
// here names the box before the confirm. Number('') is 0 in the browser, so an
// emptied box would have been sent as 0.
export const MAX_MULTIPLIER = 10

// Why `raw` cannot be sent as the `name` multiplier, or null when it can.
export function multiplierProblem(name, raw) {
  if (String(raw).trim() === '') return `Fill in the ${name} multiplier (1 leaves it unchanged).`
  const value = Number(raw)
  if (!Number.isFinite(value) || value <= 0 || value > MAX_MULTIPLIER) {
    return `The ${name} multiplier must be above 0 and at most ${MAX_MULTIPLIER}.`
  }
  return null
}
