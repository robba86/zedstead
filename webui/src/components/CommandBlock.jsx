import { useState } from 'react'
import { HiOutlineCheck, HiOutlineClipboardDocument } from 'react-icons/hi2'
import { useToast } from './Toast.jsx'

// A shell command to run by hand, wrapped to the card's width and with a
// Copy button: on one unwrapped line it ran off a phone screen, and a long
// command was hard to select by thumb.

// navigator.clipboard exists only in a secure context. A panel opened over
// plain http on the LAN has none, so a hidden textarea and execCommand copy
// there.
async function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) {
    await navigator.clipboard.writeText(text)
    return
  }
  const area = document.createElement('textarea')
  area.value = text
  area.setAttribute('readonly', '')
  area.style.position = 'fixed'
  area.style.opacity = '0'
  document.body.appendChild(area)
  area.select()
  try {
    if (!document.execCommand('copy')) throw new Error('Copy is blocked in this browser')
  } finally {
    area.remove()
  }
}

// Copies `text` and flips `copied` on for a moment; a failure becomes a
// toast. Shared by the block and the inline form below.
function useCopy(text) {
  const [copied, setCopied] = useState(false)
  const pushToast = useToast()

  async function copy() {
    try {
      await copyText(text)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch (err) {
      pushToast(err.message || 'Copy failed', 'error')
    }
  }

  return [copied, copy]
}

function CopyIcon({ copied }) {
  return copied ? <HiOutlineCheck aria-hidden="true" /> : <HiOutlineClipboardDocument aria-hidden="true" />
}

export default function CommandBlock({ text }) {
  const [copied, copy] = useCopy(text)

  return (
    <div className="command-block">
      <pre>
        <code>{text}</code>
      </pre>
      <button type="button" className="btn btn-ghost btn-small command-block-copy" onClick={copy}>
        <CopyIcon copied={copied} />
        {copied ? 'Copied' : 'Copy'}
      </button>
    </div>
  )
}

// A command inside a sentence (a Doctor detail): code-styled, with an icon
// Copy button after it, so the sentence still reads as one line of text.
export function InlineCommand({ text }) {
  const [copied, copy] = useCopy(text)

  return (
    <span className="inline-command">
      <code>{text}</code>
      <button
        type="button"
        className="btn btn-ghost btn-small inline-command-copy"
        onClick={copy}
        aria-label={copied ? 'Copied' : `Copy ${text}`}
        title={copied ? 'Copied' : 'Copy command'}
      >
        <CopyIcon copied={copied} />
      </button>
    </span>
  )
}
