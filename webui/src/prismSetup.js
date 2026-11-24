// Prism grammars + editor language behaviour for prism-react-editor.
//
// Import this module once (CodeArea.jsx does). Everything here is a side
// effect: the `prism/languages/*` modules register a grammar on the shared
// `languages` record, the `languages/*` modules register editing behaviour
// (auto-indent, tag auto-close, comment toggling) on `languageMap`.
//
// The set of languages is fixed by the backend, not guessed client-side:
// `RawTarget.language` in app/web/editors/_raw.py and
// `mod_ops.mod_file_language` between them emit exactly
// "xml" | "json" | "cfg" | "txt". "txt" is deliberately unregistered —
// an unknown language renders as plain text, which is what it is.
import { languageMap } from 'prism-react-editor'
import { languages } from 'prism-react-editor/prism'

import 'prism-react-editor/prism/languages/xml'
import 'prism-react-editor/prism/languages/json'
import 'prism-react-editor/languages/xml'
import 'prism-react-editor/languages/json'

// --- cfg -------------------------------------------------------------------
//
// serverDZ.cfg (`key = value;` + `class X { … };` blocks, `//` comments) and
// beserver_x64.cfg (`Directive value`, `//` comments). Prism's `ini` grammar
// fits neither: it has no `//` comments, no nested blocks and no `;`
// terminators. This is the same shape the old hand-rolled tokenizer matched.
//
// Order matters, and only for the two *greedy* patterns (a `g` flag is what
// makes prism-code-editor match against the whole document rather than one
// node): between two greedy patterns the earlier match position wins, so
// `string` first keeps `"http://x"` from reading as a comment, while a `//`
// comment containing a quote still swallows the string it starts before.
languages.cfg = {
  string: /"(?:\\[^]|[^\\\n"])*"/g,
  comment: /\/\/.*|\/\*[^]*?(?:\*\/|$)|^[ \t]*[#;].*/gm,
  'class-name': { pattern: /(\bclass[ \t]+)\w+/, lookbehind: true },
  keyword: /\bclass\b/,
  // Lookbehind on the indent so the token starts at the name — a token that
  // swallows the leading tabs colours whitespace and widens every selection
  // highlight to the left margin.
  key: [
    // `maxPlayers =`, `template=`, `steamQueryPort =`
    { pattern: /(^[ \t]*)[A-Za-z_]\w*(?:\[\])?(?=[ \t]*=)/m, lookbehind: true },
    // BattlEye's space-separated directives: `RConPassword secret`.
    { pattern: /(^[ \t]*)[A-Z]\w*(?=[ \t]+\S)/m, lookbehind: true },
  ],
  boolean: /\b(?:false|true)\b/,
  number: /\b-?\d+(?:\.\d+)?\b/,
  punctuation: /[{}[\];,=]/,
}

languageMap.cfg = {
  comments: { line: '//', block: ['/*', '*/'] },
}

// --- launch ----------------------------------------------------------------
//
// The generated `DayZServer` launch command (RunCommandCard), which is a
// single line of `./binary -flag=value …` — not a shell script: there is no
// quoting, no pipes and no variables, so Prism's `bash` grammar would paint
// half of it wrong (`./DayZServer` as a function, `@Mod` as nothing). Read-only
// display: `highlightText(cmd, 'launch')`, no editor, so no `languageMap` half.
//
// `-mod=` is the reason a value has an `inside`: a mod list is the one part of
// the line an admin scans for a specific entry, so `@Name` gets its own colour
// and the `;` separators drop to punctuation.
languages.launch = {
  string: /"(?:\\[^]|[^\\\n"])*"/g,
  // `build_launch_argv` always emits the binary as `./DayZServer`, so the
  // leading `./` is what identifies it — no need to match on the name.
  'class-name': { pattern: /(^|\s)\.[\\/]\S+/, lookbehind: true },
  key: { pattern: /(^|\s)-{1,2}[A-Za-z][\w-]*/, lookbehind: true },
  value: {
    pattern: /(=)\S+/,
    lookbehind: true,
    alias: 'attr-value',
    inside: {
      'attr-name': /@[^\s;]+/,
      number: /^-?\d+(?:\.\d+)?$/,
      punctuation: /;/,
    },
  },
  // A mod name containing a space breaks out of the value above (the command
  // is unquoted, so the line is genuinely ambiguous there) — colour the rest of
  // the list anyway rather than dropping to plain text mid-mod-list.
  'attr-name': /@[^\s;]+/,
  punctuation: /[;=]/,
}
