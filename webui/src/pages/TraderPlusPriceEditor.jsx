import Select from '../components/Select.jsx'
import SearchInput from '../components/SearchInput.jsx'
import FilterSelect, { Filters } from '../components/FilterSelect.jsx'
import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  HiOutlineAdjustmentsHorizontal,
  HiOutlineMapPin,
} from 'react-icons/hi2'
import { api } from '../api'
import EditorDrawer from '../components/EditorDrawer.jsx'
import { PAGE_HELP } from '../pageHelp.js'
import { useToast } from '../components/Toast.jsx'
import ScrollTable from '../components/ScrollTable.jsx'
import Pager from '../components/Pager.jsx'
import ZoneOfferCard from '../components/ZoneOfferCard.jsx'
import { ItemName } from '../components/ItemIcon.jsx'
import { useCan } from '../role.jsx'
import { inputAttrsForType } from '../format.js'
import { LINKS, MAP_EDITOR_LINKS } from '../crossLinks.js'
import { hiddenEditsNote, useRowEdits } from '../useRowEdits.js'
import { MAX_MULTIPLIER, multiplierProblem } from '../multiplier.js'
import PanelNote from '../components/PanelNote.jsx'
import { useConfirm } from '../components/useConfirm.jsx'

const PAGE_SIZE = 50
// The editable cells of a price row, in column order.
const PRICE_FIELDS = ['coefficient', 'max_stock', 'trade_quantity', 'buy_price', 'sell_price']
// Each number's column name: the header row, and a phone card's field label.
const PRICE_LABELS = { coefficient: 'Coefficient', max_stock: 'Max Stock', trade_quantity: 'Trade Qty', buy_price: 'Buy', sell_price: 'Sell' }
// The Trade filter, as routers/configs.py `_TP_TRADE_FILTERS` takes it.
// A price of -1 turns that direction off.
const TRADE_FILTERS = [
  { value: 'buy', label: 'Can buy', description: 'Players can buy it: the buy price is not -1.' },
  { value: 'sell', label: 'Can sell', description: 'Players can sell it: the sell price is not -1.' },
  { value: 'buy_only', label: 'Buy only', description: 'Players can buy it but not sell it back.' },
  { value: 'sell_only', label: 'Sell only', description: 'Players can sell it but not buy it.' },
  { value: 'off', label: 'Not traded', description: 'Both prices are -1: no trader buys or sells it.' },
]

// The TraderPlus Prices editor, in the Configs page's drawer on
// /configs/tp-price (plans/91; it was a page of its own until 2026 Q4).
// `drawer` is the frame's title, width key and close handler, from
// BespokeEditorDrawer.
export default function TraderPlusPriceEditor({ drawer }) {
  // Saves, the bulk multiplier and restore are admin writes (plans/43 §4).
  const isAdmin = useCan('admin')
  const [query, setQuery] = useState('')
  // '' is every category / every direction.
  const [category, setCategory] = useState('')
  const [trade, setTrade] = useState('')
  const [offset, setOffset] = useState(0)
  const [rows, setRows] = useState([])
  const [total, setTotal] = useState(0)
  const [unreadable, setUnreadable] = useState(0)
  const [categories, setCategories] = useState([])
  // Per row id, kept across paging, the search and another row's save.
  const { edits, setEdit, dropRow } = useRowEdits()

  const [multCategory, setMultCategory] = useState('')
  const [multiplier, setMultiplier] = useState(1)
  const confirm = useConfirm()

  const pushToast = useToast()

  const load = useCallback(async () => {
    try {
      const params = new URLSearchParams({ offset: String(offset), limit: String(PAGE_SIZE) })
      if (query) params.set('q', query)
      if (category) params.set('category', category)
      if (trade) params.set('trade', trade)
      const data = await api.get(`/api/tp-price?${params.toString()}`)
      setRows(data.rows)
      setTotal(data.total)
      setUnreadable(data.unreadable || 0)
      setCategories(data.categories)
      if (!multCategory && data.categories.length) setMultCategory(data.categories[0])
    } catch (err) {
      pushToast(err.message || 'Failed to load prices', 'error')
    }
  }, [query, category, trade, offset, multCategory, pushToast])

  useEffect(() => {
    load()
  }, [load])

  async function saveRow(row) {
    const override = edits[row.id] || {}
    const [catIdx, prodIdx] = row.id.split(':')
    // Only the edited cells go, as on Types. An emptied cell is refused here:
    // Number('') is 0, and a buy price of 0 makes the item free.
    const body = {}
    for (const f of PRICE_FIELDS) {
      if (!(f in override)) continue
      const raw = String(override[f]).trim()
      const value = Number(raw)
      if (raw === '' || !Number.isFinite(value)) {
        pushToast(`${f} must be a number${raw === '' ? '; it cannot be left empty' : ''}.`, 'error')
        return
      }
      body[f] = value
    }
    try {
      await api.put(`/api/tp-price/${catIdx}/${prodIdx}`, body)
      pushToast(`${row.classname} saved.`, 'info')
      dropRow(row.id)
      load()
    } catch (err) {
      pushToast(err.message || 'Save failed', 'error')
    }
  }

  // Number('') is 0, and a 0 set every price in the category to 1:
  // the box is checked before the confirm, as on Types.
  function askMultiplier() {
    const problem = multiplierProblem('price', multiplier)
    if (problem) {
      pushToast(problem, 'error')
      return
    }
    applyMultiplier()
  }

  async function applyMultiplier() {
    const ok = await confirm({
      title: 'Apply price multiplier?',
      message: `Multiply the buy and sell prices of every product in ${multCategory} by ${Number(multiplier)}, whatever the search shows.`,
      confirmLabel: 'Apply',
    })
    if (!ok) return
    const catIdx = categories.indexOf(multCategory)
    if (catIdx < 0) return
    try {
      const data = await api.post(`/api/tp-price/${catIdx}/multiplier`, {
        multiplier: Number(multiplier),
        columns: ['buy_price', 'sell_price'],
      })
      pushToast(`Updated ${data.changes} price(s) in ${multCategory}.`, 'info')
      load()
    } catch (err) {
      pushToast(err.message || 'Multiplier failed', 'error')
    }
  }

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const page = Math.floor(offset / PAGE_SIZE) + 1
  const hiddenNote = hiddenEditsNote([edits], rows.map((row) => row.id))

  return (
    <EditorDrawer {...drawer} help={PAGE_HELP['/configs/tp-price']} helpPage="/configs/tp-price">
      {/* The bar a registry editor has for its related editors and its map
          link (ConfigEditorDrawer). */}
      <div className="action-bar editor-drawer-actions">
        <Link className="btn btn-ghost" to={LINKS['tp-general'].to}>
          <HiOutlineAdjustmentsHorizontal aria-hidden="true" />
          TraderPlus General
        </Link>
        <Link className="btn btn-ghost" to={MAP_EDITOR_LINKS['tp-general']}>
          <HiOutlineMapPin aria-hidden="true" />
          View traders on map
        </Link>
      </div>

      <div className="inline-form">
        <SearchInput
          placeholder="Search classname…"
          value={query}
          onChange={(e) => { setQuery(e.target.value); setOffset(0) }}
        />
        {/* A Select, not chips: a price file often holds dozens of categories.
            Picking one also points the multiplier at it. */}
        <Select
          aria-label="Category"
          className="text-input"
          value={category}
          onChange={(v) => { setCategory(v); setOffset(0); if (v) setMultCategory(v) }}
          options={[{ value: '', label: 'All categories' }, ...categories]}
        />
      </div>

      <Filters>
        <FilterSelect label="Trade" value={trade} options={TRADE_FILTERS} onChange={(v) => { setTrade(v); setOffset(0) }} />
      </Filters>

      {isAdmin && (
      <div className="card">
        <h2>Category multiplier (buy + sell price)</h2>
        <div className="inline-form">
          <label className="modal-field" htmlFor="tp-mult-category">
            Category
            <Select id="tp-mult-category" value={multCategory} onChange={setMultCategory} options={categories} />
          </label>
          <label className="modal-field">
            Price ×
            <input
              className="text-input text-input-narrow"
              type="number"
              step="0.1"
              min="0.1"
              max={MAX_MULTIPLIER}
              value={multiplier}
              onChange={(e) => setMultiplier(e.target.value)}
            />
          </label>
          <button className="btn btn-primary" onClick={askMultiplier}>
            Apply
          </button>
        </div>
      </div>
      )}

      <ZoneOfferCard onAdded={load} />

      {unreadable > 0 && (
        <div className="banner banner-warning">
          {unreadable === 1 ? '1 line' : `${unreadable} lines`} in the price file{' '}
          {unreadable === 1 ? 'is' : 'are'} not in the form Zedstead reads (class, coefficient, max stock, trade
          quantity, buy price, sell price). {unreadable === 1 ? 'It is' : 'They are'} shown as written, kept
          unchanged on every save, and can only be corrected in the file itself.
        </div>
      )}

      {hiddenNote && <PanelNote>{hiddenNote}</PanelNote>}

      {/* table-cards: a phone shows each row as a card (index.css), as
          Types does, so the five number boxes fit without a sideways scroll. */}
      <ScrollTable
        className="table-cards"
        footer={
          <Pager
            page={page}
            totalPages={totalPages}
            onPrev={() => setOffset(offset - PAGE_SIZE)}
            onNext={() => setOffset(offset + PAGE_SIZE)}
            extra={`${total} products`}
          />
        }
      >
        <table className="data-table">
          <thead>
            <tr>
              <th>Classname</th>
              <th>Category</th>
              {PRICE_FIELDS.map((f) => <th key={f}>{PRICE_LABELS[f]}</th>)}
              <th />
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const override = edits[row.id] || {}
              // A line the backend could not read has no numbers of its own:
              // inputs here would show defaults and a save would be refused.
              if (row.unreadable) {
                return (
                  <tr key={row.id}>
                    <td className="cell-name">
                      <ItemName name={row.classname}>
                        <code>{row.classname}</code>
                      </ItemName>
                    </td>
                    <td data-label="Category">{row.category}</td>
                    <td colSpan={6} data-type="list" className="text-muted">
                      Not a product line Zedstead can read. Kept as it is.
                    </td>
                  </tr>
                )
              }
              return (
                <tr key={row.id}>
                  <td className="cell-name">
                    <ItemName name={row.classname} />
                  </td>
                  <td data-label="Category">{row.category}</td>
                  {/* data-type: the table editors' minimum width (index.css),
                      or a phone squeezes the inputs until a number is cut. */}
                  {PRICE_FIELDS.map((f) => (
                    <td key={f} data-type="float" data-label={PRICE_LABELS[f]}>
                      <input
                        className="text-input text-input-narrow"
                        {...inputAttrsForType('float')}
                        value={override[f] ?? row[f]}
                        onChange={(e) => setEdit(row.id, f, e.target.value)}
                      />
                    </td>
                  ))}
                  <td className="cell-actions">
                    {isAdmin && (
                    <button className="btn btn-ghost btn-small" onClick={() => saveRow(row)}>
                      Save
                    </button>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </ScrollTable>
    </EditorDrawer>
  )
}
