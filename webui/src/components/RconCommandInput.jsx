// The RCon console's command box (Players page, Console tab): suggestions
// while the first word is typed, and a foldable list of the BattlEye commands
// a DayZ server understands. A pick only fills the box and never sends, since
// several of these kick, ban or shut the server down. The box still sends any
// text, because a mod can add commands this list does not know.

import { useRef, useState } from 'react'
import Suggestions from './Suggestions.jsx'
import ListSection from './ListSection.jsx'
import ListRow from './ListRow.jsx'
import Tooltip from './Tooltip.jsx'

// BattlEye's RCon commands on a DayZ server. `args` is the part after the
// name, `help` one line on what it does.
export const RCON_COMMANDS = [
  { name: 'players', args: '', help: 'List the players online, with number, GUID, IP and ping.' },
  { name: 'say', args: '-1 <message>', help: 'Send a message to everyone (-1) or to one player number.' },
  { name: 'kick', args: '<player#> [reason]', help: 'Kick a player by number.' },
  { name: 'ban', args: '<player#> [minutes] [reason]', help: 'Ban and kick an online player. 0 minutes = permanent.' },
  { name: 'addBan', args: '<GUID|IP> [minutes] [reason]', help: 'Ban a GUID or IP, online or not.' },
  { name: 'removeBan', args: '<ban#>', help: 'Remove a ban by its number in the bans list.' },
  { name: 'bans', args: '', help: 'Show the ban list with numbers.' },
  { name: 'loadBans', args: '', help: 'Read bans.txt again after an edit on disk.' },
  { name: 'writeBans', args: '', help: 'Write the ban list to bans.txt and drop expired bans.' },
  { name: 'admins', args: '', help: 'List the RCon clients connected now.' },
  { name: 'maxPing', args: '<ms>', help: 'Kick players whose ping is above this value.' },
  { name: '#lock', args: '', help: 'Lock the server: nobody new can join.' },
  { name: '#unlock', args: '', help: 'Unlock the server again.' },
  { name: '#shutdown', args: '', help: 'Shut the server down now. No warning to players.' },
]

function usage(cmd) {
  return cmd.args ? `${cmd.name} ${cmd.args}` : cmd.name
}

/** Suggestions are shown only while the first word is typed: after a space
 *  the person is typing arguments, and a list over the box gets in the way. */
function matches(text) {
  const word = text.trimStart()
  if (!word || /\s/.test(word)) return []
  const lower = word.toLowerCase()
  return RCON_COMMANDS.filter((c) => c.name.toLowerCase().startsWith(lower) && c.name.toLowerCase() !== lower)
}

export default function RconCommandInput({ value, onChange, onSubmit, busy, disabled, disabledTip }) {
  const [open, setOpen] = useState(false)
  const [activeIndex, setActiveIndex] = useState(-1)
  const inputRef = useRef(null)
  const blurTimer = useRef(null)
  const items = matches(value)
  const showSuggestions = open && items.length > 0

  function fill(cmd) {
    onChange(cmd.args ? `${cmd.name} ` : cmd.name)
    setOpen(false)
    setActiveIndex(-1)
    inputRef.current?.focus()
  }

  function onKeyDown(e) {
    if (!showSuggestions) return
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setActiveIndex((i) => Math.min(i + 1, items.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActiveIndex((i) => Math.max(i - 1, -1))
    } else if ((e.key === 'Enter' || e.key === 'Tab') && activeIndex >= 0) {
      e.preventDefault()
      fill(items[activeIndex])
    } else if (e.key === 'Escape') {
      e.preventDefault()
      setOpen(false)
      setActiveIndex(-1)
    }
  }

  return (
    <>
      <form className="inline-form rcon-command-form" onSubmit={onSubmit}>
        <div className="rcon-command-box">
          <input
            ref={inputRef}
            className="text-input"
            value={value}
            onChange={(e) => {
              onChange(e.target.value)
              setOpen(true)
              setActiveIndex(-1)
            }}
            onFocus={() => setOpen(true)}
            onBlur={() => {
              // Delayed so a mousedown on a suggestion still lands.
              clearTimeout(blurTimer.current)
              blurTimer.current = setTimeout(() => setOpen(false), 150)
            }}
            onKeyDown={onKeyDown}
            placeholder="players"
            autoComplete="off"
            spellCheck={false}
            aria-autocomplete="list"
          />
          {showSuggestions && (
            <Suggestions
              items={items.map((c) => ({ key: c.name, name: usage(c), meta: c.help }))}
              activeIndex={activeIndex}
              onChoose={(i) => fill(items[i])}
            />
          )}
        </div>
        <Tooltip text={disabledTip} className="bare">
          <button className="btn btn-primary" type="submit" disabled={busy || disabled}>
            {busy ? 'Sending…' : 'Send'}
          </button>
        </Tooltip>
      </form>
      <ListSection title="Commands" count={RCON_COMMANDS.length} foldable defaultOpen={false}>
        {RCON_COMMANDS.map((c) => (
          <ListRow
            key={c.name}
            compact
            wrap
            mono
            name={usage(c)}
            sub={c.help}
            onClick={() => fill(c)}
          />
        ))}
      </ListSection>
    </>
  )
}
