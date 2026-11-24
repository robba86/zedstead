// The Visual Map Editor page. Until phase 6 of the React port this file held
// an <iframe src="/map-editor/"> around the vanilla-JS editor; the editor is
// now React, native to this app, under pages/mapeditor/.
//
// Lazy-loaded: leaflet (~145KB) plus the marker SVG bundle (~124KB) belong in
// their own chunk, not in the bundle every other page downloads.

import { Suspense, lazy } from 'react'
import LoadingState from '../components/LoadingState.jsx'

const MapEditor = lazy(() => import('./mapeditor/MapEditor.jsx'))

export default function MapEditorPage() {
  return (
    <Suspense fallback={<LoadingState label="Loading map editor…" />}>
      <MapEditor />
    </Suspense>
  )
}
