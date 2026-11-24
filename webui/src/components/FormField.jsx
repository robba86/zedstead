// One labelled control in a form (webui/DESIGN.md "Form field"): the label
// above, the control, then one muted hint or, when the value is wrong, the
// error in its place. `optional` adds a muted "optional" after the label, so a
// form with few optional fields marks those instead of every required one.
// `error` turns the control's border red through the --field-border token and
// is read out by a screen reader. `action` sits at the right of the control,
// for a small button such as Clear. A group of buttons (Segmented) passes
// as="div", since a label forwards its click to the first button inside.

export default function FormField({ as: Tag = 'label', label, optional = false, hint, error, action, children }) {
  return (
    <Tag className={['modal-field', 'form-field', error && 'form-field-invalid'].filter(Boolean).join(' ')}>
      <span className="form-field-label">
        {label}
        {optional && <span className="form-field-optional">optional</span>}
      </span>
      {action ? (
        <span className="form-field-control">
          {children}
          {action}
        </span>
      ) : (
        children
      )}
      {error ? (
        <span className="form-field-error" role="alert">
          {error}
        </span>
      ) : hint ? (
        <span className="hint">{hint}</span>
      ) : null}
    </Tag>
  )
}
