import { Switch } from './FlagSwitch.jsx'
import Select from './Select.jsx'
import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import { useCan } from '../role.jsx'
import ConfirmModal from './ConfirmModal.jsx'
import { useToast } from './Toast.jsx'
import { ItemName } from './ItemIcon.jsx'
import FoldCaret from './FoldCaret.jsx'

// plans/48 D: RadZone and ToxicZone add items no trader sells until a
// category lists them and a trader lists the category. This offers each
// installed zone mod's classes as one category and the traders to give it to,
// for either trader system:
//
// - `traderplus` (the Prices page): written unpriced, buy and sell -1, and
//   priced afterwards in the Prices table, unless the admin ticks "use the
//   mod's own prices" (ToxicZone only, plans/48 D10), which the server reads
//   from the Trader.txt in the admin's installed copy of the mod.
// - `market` (Configs → Expansion): the Market has no "unpriced" and zsm no
//   Market editor, so the dialog is where they get priced (D13). One default
//   range fills every row, and any row can be changed or left out. For
//   ToxicZone, "Fill from Trader.txt" sets each listed row's min and max
//   price to the file's buy price; the admin still reviews every row.
//
// Everything that decides what is offered is server-side
// (app/ops/zone_trader.py): the classes come from the installed mod's types
// file, and those the last boot refused or the files already list are left
// out with the reason. The POST re-derives the list rather than taking it from
// here. The mods' terms ask for the author's consent before their work is
// reused (plans/48, finding 7 and D10), and nothing from the mods is in
// zsm's source.
const SYSTEMS = {
  traderplus: {
    endpoint: '/api/tp-price/zone-offers',
    target: (data) => data.price_file,
    hint:
      'These installed mods add items that no trader sells yet. Adding one writes its items to the price file as a category, with buy and sell at -1 (TraderPlus neither sells nor buys at -1), and gives the category to the traders you pick.',
    message: (offer, data, usePrices) =>
      `${offer.items.length} classes from ${offer.sources.join(', ')} go into ${data.price_file} ${
        usePrices ? `priced from ${offer.prices_source}` : 'unpriced'
      }.`,
    categoryHint: 'An existing category gets the new items added to it.',
    trader: (t) => `Trader ID ${t.id}`,
    noTraders: 'TraderPlusIDsConfig.json lists no trader IDs.',
    unsold: 'With no trader picked, nobody sells the category until one lists it in the IDs editor.',
    done: 'Set their prices in the table.',
  },
  market: {
    endpoint: '/api/expansion/market/zone-offers',
    target: (data) => data.market_dir,
    hint:
      'These installed mods add items the Expansion Market does not sell yet. Adding one writes a Market category file with the prices you set and lists it at the traders you pick.',
    message: (offer, data) =>
      `Classes from ${offer.sources.join(', ')} go into ${data.market_dir}. Fill in the "Every row" line: each item starts with it, and any row can be changed or left out.`,
    categoryHint: 'Letters, digits and _ only: it becomes the file name. An existing category gets the new items added.',
    trader: (t) => t.id,
    noTraders: 'The Traders folder has no trader files.',
    unsold: 'With no trader picked, nobody sells the category until a trader file lists it.',
    done: 'It takes effect on the next server restart.',
  },
}

const RANGE = [
  ['min_price', 'Min price'],
  ['max_price', 'Max price'],
  ['min_stock', 'Min stock'],
  ['max_stock', 'Max stock'],
]

export default function ZoneOfferCard({ system = 'traderplus', onAdded }) {
  const sys = SYSTEMS[system]
  const priced = system === 'market'
  const isAdmin = useCan('admin')
  const toast = useToast()
  const [data, setData] = useState(null)
  const [offer, setOffer] = useState(null)
  const [category, setCategory] = useState('')
  const [picked, setPicked] = useState([])
  const [mode, setMode] = useState('1')
  const [range, setRange] = useState({})
  const [rows, setRows] = useState({})
  const [left, setLeft] = useState([])
  const [usePrices, setUsePrices] = useState(false)

  const load = useCallback(async () => {
    try {
      setData(await api.get(sys.endpoint))
    } catch {
      setData(null)
    }
  }, [sys.endpoint])

  useEffect(() => {
    if (isAdmin) load()
  }, [isAdmin, load])

  if (!isAdmin || !data || !sys.target(data) || !data.offers.length) return null

  function start(o) {
    setOffer(o)
    setCategory(o.category)
    setPicked([])
    setMode('1')
    setRange({ min_price: '', max_price: '', min_stock: data.stock?.min ?? '', max_stock: data.stock?.max ?? '' })
    setRows({})
    setLeft([])
    setUsePrices(false)
  }

  function toggle(list, setList, id) {
    setList(list.includes(id) ? list.filter((x) => x !== id) : [...list, id])
  }

  // A row shows its own value once edited, and the default range until then.
  const cell = (name, key) => rows[name]?.[key] ?? range[key]

  // A Market price is a range the stock moves the buy price along, and the
  // sell price is the zone's percentage of it; Trader.txt has one buy price.
  // So both ends of the range take it, and stock is left alone.
  function fillFromTraderTxt() {
    const next = { ...rows }
    for (const [name, price] of Object.entries(offer.prices)) {
      next[name] = { ...next[name], min_price: price.buy, max_price: price.buy }
    }
    setRows(next)
  }

  function pricedItems() {
    return offer.items
      .filter((name) => !left.includes(name))
      .map((name) => ({ classname: name, ...Object.fromEntries(RANGE.map(([k]) => [k, cell(name, k)])) }))
  }

  async function add() {
    const o = offer
    const body = { mod: o.key, category: category.trim(), trader_ids: picked }
    if (!priced && usePrices) body.use_prices = true
    if (priced) {
      const items = pricedItems()
      if (items.some((item) => RANGE.some(([k]) => String(item[k]).trim() === ''))) {
        toast('Every row needs a price and stock range; set the defaults or fill the row.', 'error')
        return
      }
      Object.assign(body, {
        mode: Number(mode),
        items: items.map((item) => ({
          ...item,
          ...Object.fromEntries(RANGE.map(([k]) => [k, Number(item[k])])),
        })),
      })
    }
    setOffer(null)
    try {
      const res = await api.post(sys.endpoint, body)
      const sold = res.traders.length ? ` and listed it at ${res.traders.join(', ')}` : ''
      const done = res.priced ? `${res.priced} priced from the mod's Trader.txt; check them in the table.` : sys.done
      toast(`Added ${res.added.length} ${o.label} items to ${res.category}${sold}. ${done}`, 'info')
      onAdded?.()
    } catch (e) {
      toast(e.message || `Could not add the ${o.label} items`, 'error')
    }
    load()
  }

  return (
    <div className="card">
      <h2>Zone mod items</h2>
      <p className="hint">{sys.hint}</p>
      <ul className="tile-list">
        {data.offers.map((o) => (
          <li key={o.key}>
            <span>
              <strong>{o.label}</strong>: {o.items.length} to add
              {o.skipped.length > 0 && `, ${o.skipped.length} left out`}
            </span>
            {o.items.length > 0 ? (
              <button className="btn btn-ghost btn-small" onClick={() => start(o)}>
                Add as a category…
              </button>
            ) : (
              <span className="text-muted">Nothing left to add</span>
            )}
          </li>
        ))}
      </ul>

      <ConfirmModal
        open={offer !== null}
        title={offer ? `Add ${offer.label}'s items to ${priced ? 'the Market' : 'TraderPlus'}?` : ''}
        message={offer ? sys.message(offer, data, usePrices) : ''}
        confirmLabel="Add"
        onConfirm={add}
        onCancel={() => setOffer(null)}
      >
        {offer && (
          <>
            <label className="modal-field">
              Category
              <input
                className="text-input"
                value={category}
                onChange={(e) => setCategory(e.target.value)}
                aria-label="Category name"
              />
              <span className="hint">{sys.categoryHint}</span>
            </label>
            <div className="modal-field">
              Sold by
              {data.traders.length === 0 && <span className="hint">{sys.noTraders}</span>}
              {data.traders.map((t) => (
                <label key={t.id} className="dry-run-toggle">
                  <input
                    type="checkbox"
                    checked={picked.includes(t.id)}
                    onChange={() => toggle(picked, setPicked, t.id)}
                  />
                  {sys.trader(t)}
                  {t.names.length > 0 && ` · ${t.names.join(', ')}`}
                </label>
              ))}
              {picked.length === 0 && <span className="hint">{sys.unsold}</span>}
            </div>
            {priced && (
              <>
                <label className="modal-field">
                  Trader mode
                  <Select
                    value={mode}
                    onChange={setMode}
                    options={Object.entries(data.modes).map(([value, label]) => ({ value, label }))}
                  />
                </label>
                <div className="modal-field zone-market-rows">
                  Prices and stock
                  {offer.prices_source && (
                    <span className="hint">
                      <button type="button" className="btn btn-ghost btn-small" onClick={fillFromTraderTxt}>
                        Fill from Trader.txt
                      </button>{' '}
                      Sets min and max price to the buy price in {offer.prices_source} for the{' '}
                      {Object.keys(offer.prices).length} items it lists. Stock and the other rows stay as they are.
                    </span>
                  )}
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th aria-label="Include" />
                        <th>Item</th>
                        {RANGE.map(([key, label]) => (
                          <th key={key}>{label}</th>
                        ))}
                      </tr>
                      {/* The default range sits under the column headers, so
                          each input is labelled by the column it fills. */}
                      <tr>
                        <td />
                        <td className="hint">Every row</td>
                        {RANGE.map(([key, label]) => (
                          <td key={key}>
                            <input
                              className="text-input"
                              type="number"
                              min="0"
                              value={range[key]}
                              aria-label={`Default ${label.toLowerCase()}`}
                              onChange={(e) => setRange({ ...range, [key]: e.target.value })}
                            />
                          </td>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {offer.items.map((name) => {
                        const out = left.includes(name)
                        return (
                          <tr key={name} className={out ? 'row-readonly' : undefined}>
                            <td>
                              <input
                                type="checkbox"
                                checked={!out}
                                onChange={() => toggle(left, setLeft, name)}
                                aria-label={`Include ${name}`}
                              />
                            </td>
                            <td>{name}</td>
                            {RANGE.map(([key, label]) => (
                              <td key={key}>
                                <input
                                  className="text-input"
                                  type="number"
                                  min="0"
                                  disabled={out}
                                  value={cell(name, key)}
                                  aria-label={`${name} ${label.toLowerCase()}`}
                                  onChange={(e) =>
                                    setRows({ ...rows, [name]: { ...rows[name], [key]: e.target.value } })
                                  }
                                />
                              </td>
                            ))}
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              </>
            )}
            {!priced && offer.prices_source && (
              <div className="modal-field">
                <div className="dry-run-toggle">
                  <Switch checked={usePrices} onChange={setUsePrices}>
                    Use the mod&apos;s own prices
                  </Switch>
                </div>
                <span className="hint">
                  {Object.keys(offer.prices).length} of {offer.items.length} items have a buy and sell price in{' '}
                  {offer.prices_source}, the file in your installed copy of the mod. The rest stay at -1.
                </span>
              </div>
            )}
            {!priced && (
              <div className="modal-field">
                <details>
                  <summary>
                    <FoldCaret /> {offer.items.length} items to add
                  </summary>
                  <ul className="tile-list">
                    {offer.items.map((name) => (
                      <li key={name}>
                        <span>{name}</span>
                        {usePrices && offer.prices[name] && (
                          <span className="hint">
                            buy {offer.prices[name].buy} · sell {offer.prices[name].sell}
                          </span>
                        )}
                      </li>
                    ))}
                  </ul>
                </details>
              </div>
            )}
            {offer.skipped.length > 0 && (
              <div className="modal-field">
                <details>
                  <summary>
                    <FoldCaret /> {offer.skipped.length} left out
                  </summary>
                  <ul className="tile-list">
                    {offer.skipped.map((s) => (
                      <li key={s.classname}>
                        <ItemName name={s.classname} />
                        <span className="hint">{s.reason}</span>
                      </li>
                    ))}
                  </ul>
                </details>
              </div>
            )}
            <p className="hint">{offer.check_note}</p>
          </>
        )}
      </ConfirmModal>
    </div>
  )
}
