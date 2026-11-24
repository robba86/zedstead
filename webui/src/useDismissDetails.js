import { useEffect, useRef } from 'react'

// A <details> used as a dropdown menu (the Dashboard's Add tile / Add field)
// closes on its own summary only. This closes it on a press outside it and on
// Escape, like any other menu. Returns the ref to put on the <details>.
export default function useDismissDetails() {
  const ref = useRef(null)

  useEffect(() => {
    const onPointerDown = (e) => {
      const node = ref.current
      if (node?.open && !node.contains(e.target)) node.open = false
    }
    const onKeyDown = (e) => {
      const node = ref.current
      if (e.key === 'Escape' && node?.open) {
        node.open = false
        node.querySelector('summary')?.focus()
      }
    }
    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [])

  return ref
}
