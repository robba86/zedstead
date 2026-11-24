// "Your own image" beside the Image checkbox (plans/69 Step 4): an admin can put a map
// image of their own on a map; zsm ships none. It is
// stored on the server (POST /map-image, the file as the body), becomes the
// map's default ground, and is laid edge to edge over the world.
//
// Admin-only like the route (no MODERATOR_WRITES entry), and hidden rather than
// disabled for anyone else (role.jsx). The upload is a rendered ConfirmModal,
// portalled like DynamicAiMoveDialog, because it carries a file input.

import { useState } from 'react'
import { createPortal } from 'react-dom'
import ConfirmModal from '../../../components/ConfirmModal.jsx'
import { useToast } from '../../../components/Toast.jsx'
import { useConfirm } from '../../../components/useConfirm.jsx'
import { formatBytes } from '../../../format.js'
import { useCan } from '../../../role.jsx'
import { useEditor } from '../state/MapEditorProvider'
import { meApi } from '../api'

// The preview the Maps page and the Dashboard show (GET /api/maps/thumbnail).
// A browser draws an 8192 px image in a 130 px box by downsampling it again on
// every repaint, which makes the page stutter while it scrolls. So the upload
// sends a 1024 px JPEG made from the same file, and the server keeps it beside
// the image (POST /map-image/thumbnail).
const THUMB_WIDTH = 1024

async function makeThumbnail(file) {
  const bitmap = await createImageBitmap(file, { resizeWidth: THUMB_WIDTH, resizeQuality: 'high' })
  const canvas = document.createElement('canvas')
  canvas.width = bitmap.width
  canvas.height = bitmap.height
  canvas.getContext('2d').drawImage(bitmap, 0, 0)
  bitmap.close()
  return new Promise((resolve, reject) =>
    canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error('No preview'))), 'image/jpeg', 0.85)
  )
}

export default function OwnMapImage() {
  const { terrain, mapInfo, maps, url, reload } = useEditor()
  const isAdmin = useCan('admin')
  const pushToast = useToast()
  const confirm = useConfirm()
  const [open, setOpen] = useState(false)
  const [file, setFile] = useState(null)
  const [busy, setBusy] = useState(false)

  if (!isAdmin || !terrain || !mapInfo) return null
  const own = mapInfo.image_own
  const name = maps.find((m) => m.terrain === terrain)?.name || terrain
  const query = `/map-image?terrain=${encodeURIComponent(terrain)}`

  // The ground follows the map's default again, which an own image makes the
  // image, and which removing it gives back to whatever the map had.
  const done = (message) => {
    pushToast(message, 'info')
    url.setMapLayers({ base: '' })
    reload.mapInfo()
  }

  const close = () => {
    if (busy) return
    setOpen(false)
    setFile(null)
  }

  async function upload() {
    if (!file) return
    setBusy(true)
    try {
      await meApi.postFile(query, file)
      // The image is saved either way; without a preview the Maps page shows
      // the full image, as before.
      const thumbQuery = `/map-image/thumbnail?terrain=${encodeURIComponent(terrain)}`
      const thumbed = await makeThumbnail(file)
        .then((blob) => meApi.postFile(thumbQuery, blob))
        .then(() => true, () => false)
      setOpen(false)
      setFile(null)
      done(`${name} now uses your own image.`)
      if (!thumbed) pushToast('The small preview for the Maps page could not be made.', 'error')
    } catch (err) {
      pushToast(`${err.message || 'Upload failed'} Nothing was changed.`, 'error')
    } finally {
      setBusy(false)
    }
  }

  async function remove() {
    const yes = await confirm({
      title: `Remove your own image for ${name}?`,
      message: 'The map goes back to the terrain where this map has it, else to the buildings read from your mission.',
      confirmLabel: 'Remove',
      danger: true,
    })
    if (!yes) return
    try {
      await meApi.del(query)
      done(`Removed your own image for ${name}.`)
    } catch (err) {
      pushToast(err.message || 'Could not remove the image', 'error')
    }
  }

  return (
    <span className="me-own-image">
      {own ? (
        <>
          <span>Your own image ({formatBytes(own.size)})</span>
          <button className="btn-link" onClick={() => setOpen(true)}>
            Replace…
          </button>
          <button className="btn-link" onClick={remove}>
            Remove
          </button>
        </>
      ) : (
        <button className="btn-link" onClick={() => setOpen(true)}>
          Use your own image…
        </button>
      )}
      {open &&
        createPortal(
          <ConfirmModal
            open
            title={`Your own image for ${name}`}
            message="It is laid edge to edge over the whole map, north up, so it has to show exactly the map's square."
            confirmLabel={busy ? 'Uploading…' : 'Use this image'}
            confirmDisabled={!file || busy}
            onConfirm={upload}
            onCancel={close}
          >
            <label className="modal-field">
              A JPEG, PNG or WebP image
              <input
                type="file"
                accept="image/jpeg,image/png,image/webp"
                disabled={busy}
                onChange={(e) => setFile(e.target.files?.[0] || null)}
              />
            </label>
            <p className="modal-message">
              You are responsible for having the right to use this image. It is stored on this
              server and shown only in this app.
            </p>
          </ConfirmModal>,
          document.body
        )}
    </span>
  )
}
