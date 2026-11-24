// The small "Default" pill on a field that is on the server's default: in a
// UnitInput's box, in a Select's closed field, after a Switch's label and in
// a RangeField's scale. The look is `.default-pill` (index.css), and
// `className` places it in its host. Other text in `children` ("Before 1.30"
// on a key a newer DayZ replaced) gives the same pill at its own width.
export default function DefaultPill({ className = '', children, ...rest }) {
  const cls = ['status-pill status-pill-neutral default-pill', children && 'default-pill-text', className]
  return (
    <span className={cls.filter(Boolean).join(' ')} {...rest}>
      {children ?? 'Default'}
    </span>
  )
}

// Whether a field that shows `value` is on the server's default `def`. An
// empty value is: the file leaves the key out, so the server uses the default.
// A number is compared as a number, so "60.0" is on a default of 60. False
// when the field has no known default.
export function atDefault(value, def) {
  if (def == null || def === '') return false
  if (value == null || String(value).trim() === '') return true
  if (typeof def === 'boolean') return !!value === def
  const a = Number(value)
  const b = Number(def)
  if (typeof value !== 'boolean' && Number.isFinite(a) && Number.isFinite(b)) return a === b
  return String(value) === String(def)
}

// What a field the file leaves out does, as one sentence for its tooltip or
// note. `shown` is the default as the field writes it ("5 s", "0.8"). A field
// whose default is only the value in Bohemia's example file
// (`default_is_example`) says so, since Bohemia does not name its default.
export function notInFileText(field, shown) {
  return field.default_is_example
    ? `Not in the file. Bohemia's example file sets ${shown}, but Bohemia does not say what the server uses without it.`
    : `Not in the file: the server uses its default, ${shown}.`
}

// The default a "Default" pill can stand on: none when the value is only
// Bohemia's example (`default_is_example`), so the pill is never a guess.
export function pillDefault(field) {
  return field.default_is_example ? null : field.default
}
