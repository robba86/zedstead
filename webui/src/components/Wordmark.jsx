import STACKED from './wordmark.svg?raw'
import LONG from './wordmark-long.svg?raw'

/*
 * The Zedstead wordmark (ZED over STEAD), drawn instead of typed. The artwork is wordmark.svg
 * beside this file, an ordinary SVG file that an editor such as Inkscape
 * opens. `?raw` is Vite's own import of a file as text, so no plugin is
 * involved and no extra file is emitted: the shapes are inlined into the
 * bundle, as the path in this file was before.
 *
 * Inlined rather than an <img>, because an inline SVG can paint with
 * currentColor like Logo.jsx, and so take the brand row's colour in every
 * theme (--tac-brand-color in Tactical). Keep `fill="currentColor"` on the
 * shapes when you edit the file.
 *
 * wordmark-long.svg is the one-row logo with "server manager" under it, for
 * the login card (`long`). The sidebar and the top bar keep the stacked one.
 *
 * zedstead-site's src/wordmark.svg is the same file as wordmark.svg: change both
 * or neither.
 */
// Splits a logo file into its viewBox and its shapes, once at import.
function parse(svg, name) {
  const viewBox = svg.match(/viewBox="([^"]+)"/)?.[1]
  const inner = svg.replace(/^[\s\S]*?<svg[^>]*>/, '').replace(/<\/svg>\s*$/, '').replace(/>\s+</g, '><').trim()
  if (!viewBox || !inner) throw new Error(`${name}: no viewBox or no shapes`)
  return { viewBox, inner }
}
const ART = { stacked: parse(STACKED, 'wordmark.svg'), long: parse(LONG, 'wordmark-long.svg') }

export default function Wordmark({ title = 'Zedstead', long = false }) {
  const { viewBox, inner } = long ? ART.long : ART.stacked
  return (
    <svg
      className="brand-wordmark"
      viewBox={viewBox}
      role="img"
      aria-label={title}
      focusable="false"
      // Our own file from this repo, read at build time: no user input.
      dangerouslySetInnerHTML={{ __html: inner }}
    />
  )
}
