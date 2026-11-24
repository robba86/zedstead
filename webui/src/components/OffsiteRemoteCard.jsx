import { Switch } from './FlagSwitch.jsx'
import Select from './Select.jsx'
import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import ErrorBanner from './ErrorBanner.jsx'
import FormField from './FormField.jsx'
import PanelNote from './PanelNote.jsx'
import PasswordField from './PasswordField.jsx'
import { useToast } from './Toast.jsx'
import { useConfirm } from './useConfirm.jsx'

// The offsite backup target (TODO C1). Backends and their fields come from
// GET /api/backups/remote — the server owns which kinds exist and which keys
// each one takes, so adding a backend is a backend change.
//
// The card never shows a stored value, because the API has no route that
// returns one: the password and keys live in rclone's own config file at 0600
// and zsm writes them one way only. GET says which fields hold a value
// (`set_fields`, names only), and a save that leaves one blank keeps it, so
// changing the path or the auto-upload switch never wipes the credentials.
// Clear takes a stored value away on purpose; another type replaces them all.

const KIND_LABELS = {
  sftp: 'SFTP / SSH',
  s3: 'S3-compatible',
  b2: 'Backblaze B2',
}

// rclone's key names are the source of truth; these are just what a human
// reads. A key with no entry falls back to its own name, so a new backend
// field renders (ugly but present) rather than disappearing.
const FIELD_LABELS = {
  host: 'Host',
  user: 'User',
  port: 'Port',
  key_file: 'Private key file',
  pass: 'Password',
  provider: 'Provider',
  access_key_id: 'Access key ID',
  secret_access_key: 'Secret access key',
  endpoint: 'Endpoint URL',
  region: 'Region',
  account: 'Account ID',
  key: 'Application key',
  hard_delete: 'Hard delete (true/false)',
}

const FIELD_HINTS = {
  key_file: 'None set means ssh-agent and ~/.ssh — usually right on a VPS.',
  pass: 'Prefer a key. Stored obscured in rclone.conf, which is not encryption.',
  endpoint: 'Your provider’s S3 endpoint, e.g. s3.eu-central-003.backblazeb2.com',
  provider: 'rclone’s provider name. "Other" works for most S3-compatible hosts.',
}

const PATH_HINTS = {
  sftp: 'Directory on the remote host, e.g. dayz-backups/prod',
  s3: 'Bucket and optional prefix, e.g. my-bucket/dayz',
  b2: 'Bucket and optional prefix, e.g. my-bucket/dayz',
}

export default function OffsiteRemoteCard() {
  const [status, setStatus] = useState(null)
  const [kind, setKind] = useState('sftp')
  const [values, setValues] = useState({})
  const [cleared, setCleared] = useState([])
  const [path, setPath] = useState('')
  const [auto, setAuto] = useState(true)
  const [busy, setBusy] = useState(false)
  const [testResult, setTestResult] = useState(null)
  // Field name (or "path") to what is wrong with it. Filled by a save that
  // misses a required field, here or from the server's 422; a field's own
  // message goes as soon as that field changes.
  const [errors, setErrors] = useState({})
  const confirm = useConfirm()
  const pushToast = useToast()

  const load = useCallback(async () => {
    try {
      const data = await api.get('/api/backups/remote')
      setStatus(data)
      setPath(data.path || '')
      setAuto(!!data.auto_upload)
      if (data.kind) setKind(data.kind)
    } catch (err) {
      pushToast(err.message || 'Failed to load the offsite target', 'error')
    }
  }, [pushToast])

  useEffect(() => {
    load()
  }, [load])

  // The required checks the server makes too (offsite_ops.form_problems), so
  // the form marks an empty field before any request. The server's lists say
  // what is required; a stored value of the same kind that is not cleared
  // counts as filled, since a blank field means "keep". Format checks (port,
  // hard_delete) are left to the server's answer.
  function missingFields() {
    const sameKindStored =
      status.configured && kind === status.kind ? new Set(status.set_fields || []) : new Set()
    const problems = {}
    for (const name of status.required_fields?.[kind] || []) {
      const typed = (values[name] || '').trim()
      if (!typed && (!sameKindStored.has(name) || cleared.includes(name))) problems[name] = 'Required.'
    }
    if ((status.path_required_kinds || []).includes(kind) && !path.trim()) {
      problems.path = 'Required: the bucket name, then an optional folder.'
    }
    return problems
  }

  function dropError(name) {
    if (!errors[name]) return
    const { [name]: _gone, ...rest } = errors
    setErrors(rest)
  }

  async function save() {
    const missing = missingFields()
    setErrors(missing)
    if (Object.keys(missing).length) return
    setBusy(true)
    setTestResult(null)
    try {
      const data = await api.post('/api/backups/remote', {
        kind,
        path,
        auto,
        fields: values,
        clear: kind === status.kind ? cleared : [],
      })
      setStatus(data)
      // Drop the secrets from component state the moment they are written.
      setValues({})
      setCleared([])
      pushToast('Offsite target saved. Test it to confirm it works.', 'info')
    } catch (err) {
      const fields = err.body?.detail?.fields
      if (fields) setErrors(fields)
      else pushToast(err.message || 'Failed to save the offsite target', 'error')
    } finally {
      setBusy(false)
    }
  }

  async function test() {
    setBusy(true)
    setTestResult(null)
    try {
      const data = await api.post('/api/backups/remote/test')
      setTestResult(data)
    } catch (err) {
      setTestResult({ ok: false, message: err.message || 'Test failed' })
    } finally {
      setBusy(false)
    }
  }

  // SFTP host keys (plans/68 NET-4): Test shows the fingerprint on first
  // contact, and the key is saved only from here, after the admin has
  // compared it. The server scans the host again and refuses a different key.
  async function trustHostKey(keys) {
    setBusy(true)
    try {
      const data = await api.post('/api/backups/remote/host-key', {
        fingerprints: keys.map((k) => k.fingerprint),
      })
      setTestResult(data)
      await load()
    } catch (err) {
      setTestResult({ ok: false, message: err.message || 'Could not save the host key' })
    } finally {
      setBusy(false)
    }
  }

  async function clear() {
    const ok = await confirm({
      title: 'Remove offsite target?',
      message: 'The stored credentials are deleted with it. Backups already uploaded stay where they are.',
      confirmLabel: 'Remove',
      danger: true,
    })
    if (!ok) return
    setBusy(true)
    try {
      await api.del('/api/backups/remote')
      setValues({})
      setCleared([])
      setTestResult(null)
      await load()
      pushToast('Offsite target removed.', 'info')
    } catch (err) {
      pushToast(err.message || 'Failed to remove the offsite target', 'error')
    } finally {
      setBusy(false)
    }
  }

  if (!status) return null

  const fields = status.fields?.[kind] || []
  const secrets = new Set(status.secret_fields || [])
  // Fields of the configured kind that hold a value; blank keeps them.
  const sameKind = status.configured && kind === status.kind
  const stored = new Set(sameKind ? status.set_fields || [] : [])

  function toggleClear(name) {
    setCleared(cleared.includes(name) ? cleared.filter((n) => n !== name) : [...cleared, name])
    setValues({ ...values, [name]: '' })
  }

  const required = new Set(status.required_fields?.[kind] || [])
  const pathRequired = (status.path_required_kinds || []).includes(kind)
  const hasErrors = Object.keys(errors).length > 0

  return (
    <div className="card" id="offsite-target">
      <div className="card-header">
        <h2>Offsite target</h2>
      </div>
      <PanelNote>
        Transfers run through <code>rclone</code>. Zedstead downloads it on first use, so you install nothing
        yourself.
      </PanelNote>

      {status.configured ? (
        <p className="banner banner-info">
          Sending to <code>{status.kind}</code>
          {status.path ? (
            <>
              {' '}
              at <code>{status.path}</code>
            </>
          ) : null}
          . Automatic upload after each backup is {status.auto_upload ? 'on' : 'off'}.
        </p>
      ) : (
        <p className="banner banner-warning">
          No offsite target. Backups exist only on this machine.
        </p>
      )}

      {/* A plain-http S3 endpoint (plans/68 NET-15): offsite_ops.endpoint_warning. */}
      {status.warning ? <p className="banner banner-warning">{status.warning}</p> : null}

      <FormField label="Type">
        <Select
          className="field"
          value={kind}
          onChange={(k) => {
            setKind(k)
            setErrors({})
          }}
          options={(status.supported_kinds || []).map((k) => ({ value: k, label: KIND_LABELS[k] || k }))}
        />
      </FormField>

      {status.configured && !sameKind ? (
        <p className="banner banner-warning">
          Saving replaces the stored <code>{status.kind}</code> target and every value in it.
        </p>
      ) : null}

      {fields.map((name) => {
        const isCleared = stored.has(name) && cleared.includes(name)
        const input = {
          className: 'field',
          autoComplete: 'off',
          value: values[name] || '',
          disabled: isCleared,
          'aria-invalid': errors[name] ? true : undefined,
          onChange: (e) => {
            setValues({ ...values, [name]: e.target.value })
            dropError(name)
          },
          placeholder: isCleared ? 'Removed on save' : stored.has(name) ? 'Stored. Leave blank to keep it.' : '',
        }
        return (
          <FormField
            key={name}
            label={FIELD_LABELS[name] || name}
            optional={!required.has(name)}
            hint={FIELD_HINTS[name]}
            error={errors[name]}
            action={
              stored.has(name) ? (
                <button
                  type="button"
                  className="btn btn-ghost btn-small"
                  onClick={() => {
                    toggleClear(name)
                    dropError(name)
                  }}
                  disabled={busy}
                >
                  {isCleared ? 'Keep' : 'Clear'}
                </button>
              ) : null
            }
          >
            {secrets.has(name) ? <PasswordField {...input} /> : <input type="text" {...input} />}
          </FormField>
        )
      })}

      <FormField label="Destination path" optional={!pathRequired} hint={PATH_HINTS[kind]} error={errors.path}>
        <input
          className="field"
          value={path}
          aria-invalid={errors.path ? true : undefined}
          onChange={(e) => {
            setPath(e.target.value)
            dropError('path')
          }}
        />
      </FormField>

      <div className="dry-run-toggle">
        <Switch checked={auto} onChange={setAuto}>
          Copy every new backup to the remote automatically
        </Switch>
      </div>

      <PanelNote>
        A blank field keeps its stored value, and Clear removes it. Another type replaces the whole target. Zedstead
        writes the values to <code>{status.config_file}</code> (only the owner can read it) and never shows them
        again.
      </PanelNote>

      {hasErrors ? <PanelNote warn>Fill in the marked fields, then save again.</PanelNote> : null}


      <div className="inline-form">
        <button className="btn btn-primary" onClick={save} disabled={busy}>
          Save target
        </button>
        <button className="btn btn-ghost" onClick={test} disabled={busy || !status.configured}>
          Test connection
        </button>
        <button
          className="btn btn-danger"
          onClick={clear}
          disabled={busy || !status.configured}
        >
          Remove
        </button>
      </div>

      {(status.host_key === 'unconfirmed' || status.host_key === 'unchecked') &&
      !testResult?.host_key ? (
        <p className="banner banner-warning">
          {status.host_key === 'unconfirmed'
            ? 'The SFTP host’s key is not confirmed yet, so no backup is sent there. '
            : 'This SFTP target was set up before Zedstead checked host keys, so nothing checks which server receives the backups. '}
          Press Test connection and compare the fingerprint it shows.
        </p>
      ) : null}

      {testResult && !testResult.ok && testResult.host_key?.state === 'changed' ? (
        <ErrorBanner as="p" dismissKey={`offsite:${testResult.message}`}>
          {testResult.message}
        </ErrorBanner>
      ) : testResult ? (
        <p className={testResult.ok ? 'banner banner-info' : 'banner banner-warning'}>{testResult.message}</p>
      ) : null}

      {testResult?.host_key?.state === 'unknown' ? (
        <div>
          <ul>
            {testResult.host_key.keys.map((k) => (
              <li key={k.fingerprint}>
                <code>{k.type}</code> <code>{k.fingerprint}</code>
              </li>
            ))}
          </ul>
          <button
            className="btn btn-primary"
            onClick={() => trustHostKey(testResult.host_key.keys)}
            disabled={busy}
          >
            {testResult.host_key.keys.length > 1 ? 'Confirm and save these keys' : 'Confirm and save this key'}
          </button>
        </div>
      ) : null}

      {testResult?.host_key?.state === 'changed' ? (
        <div>
          <p className="hint">The host offers now:</p>
          <ul>
            {testResult.host_key.keys.map((k) => (
              <li key={k.fingerprint}>
                <code>{k.type}</code> <code>{k.fingerprint}</code>
              </li>
            ))}
          </ul>
          <p className="hint">You confirmed:</p>
          <ul>
            {testResult.host_key.confirmed.map((k) => (
              <li key={k.fingerprint}>
                <code>{k.type}</code> <code>{k.fingerprint}</code>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

    </div>
  )
}
