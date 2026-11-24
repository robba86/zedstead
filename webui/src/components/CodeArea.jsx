import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
} from 'react'
import { Editor } from 'prism-react-editor'
import { BasicSetup, ReadOnlySetup } from 'prism-react-editor/setups'

import { tooltipAttrs } from './Tooltip.jsx'
import '../prismSetup.js'
import 'prism-react-editor/layout.css'
import 'prism-react-editor/search.css'
import 'prism-react-editor/invisibles.css'
import 'prism-react-editor/copy-button.css'
import 'prism-react-editor/code-folding.css'
import '../prism-editor.css'

// Above this the per-line DOM the editor builds stops being worth it (a modded
// types.xml runs to megabytes); render a plain textarea instead so opening the
// file doesn't lock the tab.
const MAX_EDITOR_CHARS = 200000

/**
 * Lets a wrapper reach the editor instance of the CodeArea it renders as a
 * child. Provide a `register(editor | null)` callback; CodeArea calls it on
 * mount and with null on unmount. XmlTools uses it to put the caret on a
 * reported line — see its jumpTo.
 */
export const CodeAreaRegistry = createContext(null)

// Word wrap is one per-browser choice for every code area, not per editor: the
// button in one pane flips the Mod XML page's other pane too. On by default,
// which is what the editor always did before the button existed.
const WRAP_KEY = 'zsm.codearea.wrap'
const wrapListeners = new Set()

function readWrap() {
  try {
    return localStorage.getItem(WRAP_KEY) !== '0'
  } catch {
    return true
  }
}

function setWrap(on) {
  try {
    localStorage.setItem(WRAP_KEY, on ? '1' : '0')
  } catch {
    // Private mode: the choice lasts until reload.
  }
  wrapListeners.forEach((fn) => fn())
}

function subscribeWrap(fn) {
  wrapListeners.add(fn)
  return () => wrapListeners.delete(fn)
}

// An overlay child of the editor, so it scrolls with nothing and sits in the
// top-right corner like the package's copy button (left of it when readOnly).
function WrapButton({ wrap, readOnly }) {
  return (
    <div className="code-area-wrap-box">
      <button
        type="button"
        className={`code-area-wrap${wrap ? ' is-on' : ''}${readOnly ? ' beside-copy' : ''}`}
        aria-pressed={wrap}
        aria-label={wrap ? 'Word wrap on' : 'Word wrap off'}
        {...tooltipAttrs(
          wrap ? 'Word wrap is on — click to turn it off' : 'Word wrap is off — click to turn it on'
        )}
        // Keep the editor's focus and caret where they were.
        onMouseDown={(e) => e.preventDefault()}
        onClick={() => setWrap(!wrap)}
      >
        <svg
          width="1.2em"
          aria-hidden="true"
          viewBox="0 0 16 16"
          fill="none"
          stroke="currentColor"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M2 3h12M2 8h10a2 2 0 010 4H8m1.5-1.5L8 12l1.5 1.5M2 13h3" />
        </svg>
      </button>
    </div>
  )
}

// Editable code area, backed by prism-react-editor.
//
// The wrapper exists for one reason: prism-react-editor is deliberately NOT a
// controlled component. Its `value` prop is an *initial* value, and feeding a
// new one back in from the change handler resets the caret to 0 and wipes the
// undo history on every keystroke (the package README calls this out as its
// one pitfall). So the prop we hand it is a `seed` that only moves when the
// value changes from OUTSIDE — a Format XML rewrite, a Reset, a reload — which
// is exactly when losing the caret is correct anyway. `emitted` is the last
// value the editor itself produced; a parent value equal to it is our own
// keystroke coming back, and must not re-seed.
//
// Everything else is the package's: BasicSetup adds undo/redo, auto-indent,
// bracket + tag matching, indent guides and Ctrl+F search; ReadOnlySetup swaps
// those for a copy button and code folding. Colours come from prism-editor.css.
export default function CodeArea({
  value = '',
  onChange,
  language,
  className = '',
  readOnly = false,
}) {
  const emitted = useRef(value)
  // `n` counts the outside changes. A Reset back to the text the editor was
  // seeded with left `seed` equal, so React kept the edited text on screen
  // (user test in 2026 Q4); the count, as the editor's key, remounts it.
  const [seed, setSeed] = useState({ text: value, n: 0 })
  const register = useContext(CodeAreaRegistry)
  const wrap = useSyncExternalStore(subscribeWrap, readWrap)

  useEffect(() => {
    if (value !== emitted.current) {
      emitted.current = value
      setSeed((prev) => ({ text: value, n: prev.n + 1 }))
    }
  }, [value])

  // Stable so React doesn't detach/reattach the editor on every render.
  const editorRef = useCallback((editor) => register?.(editor), [register])

  if (value.length > MAX_EDITOR_CHARS) {
    return (
      <textarea
        className={`text-editor-area ${className}`}
        spellCheck={false}
        autoComplete="off"
        autoCapitalize="off"
        autoCorrect="off"
        readOnly={readOnly}
        wrap={wrap ? 'soft' : 'off'}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
    )
  }

  return (
    <Editor
      key={seed.n}
      ref={editorRef}
      className={`code-area ${className}`}
      language={language || 'text'}
      value={seed.text}
      readOnly={readOnly}
      wordWrap={wrap}
      tabSize={4}
      // DayZ mission XML is tab-indented, and so is what the server's
      // Format XML returns — indenting with spaces here would fight it.
      insertSpaces={false}
      onUpdate={(next) => {
        if (next === emitted.current) return
        emitted.current = next
        onChange(next)
      }}
    >
      {readOnly ? <ReadOnlySetup /> : <BasicSetup />}
      <WrapButton wrap={wrap} readOnly={readOnly} />
    </Editor>
  )
}
