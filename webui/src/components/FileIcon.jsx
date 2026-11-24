import { HiOutlineEye, HiOutlinePencilSquare } from 'react-icons/hi2'

// Leading glyph on a clickable file name, saying what the click does before
// it happens: an eye opens the file read-only, a pen opens it somewhere it can
// be edited. A file name with neither is plain text, not a link.
export default function FileIcon({ action = 'view' }) {
  const Icon = action === 'edit' ? HiOutlinePencilSquare : HiOutlineEye
  return <Icon className="file-icon" aria-hidden="true" />
}
