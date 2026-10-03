/** True when the decorative/3D WebGL scenes should initialise. Automation, reduced motion or no WebGL -> false (2D/text fallbacks). */
export function canRender3d(): boolean {
  if (typeof window === 'undefined' || typeof navigator === 'undefined') return false
  if (navigator.webdriver) return false
  if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return false
  try {
    const c = document.createElement('canvas')
    return !!(c.getContext('webgl2') || c.getContext('webgl'))
  } catch {
    return false
  }
}
