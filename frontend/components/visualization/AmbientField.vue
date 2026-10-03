<script setup lang="ts">
import * as THREE from 'three'

const containerRef = ref<HTMLDivElement | null>(null)
let renderer: THREE.WebGLRenderer | null = null
let scene: THREE.Scene | null = null
let camera: THREE.PerspectiveCamera | null = null
let lines: THREE.LineSegments | null = null
let animFrameId: number | null = null
let isVisible = true
let prefersReducedMotion = false

function initScene() {
  if (!containerRef.value || typeof window === 'undefined' || !canRender3d()) return

  prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  if (prefersReducedMotion) return

  const width = containerRef.value.clientWidth || window.innerWidth
  const height = containerRef.value.clientHeight || window.innerHeight

  scene = new THREE.Scene()
  camera = new THREE.PerspectiveCamera(50, width / height, 0.1, 1000)
  camera.position.set(0, 15, 60)
  camera.lookAt(0, 0, 0)

  try {
    renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true, powerPreference: 'low-power' })
    renderer.setSize(width, height)
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.5))
    renderer.domElement.style.position = 'absolute'
    renderer.domElement.style.top = '0'
    renderer.domElement.style.left = '0'
    renderer.domElement.style.width = '100%'
    renderer.domElement.style.height = '100%'
    renderer.domElement.style.pointerEvents = 'none'
    renderer.domElement.setAttribute('aria-hidden', 'true')
    containerRef.value.appendChild(renderer.domElement)
  } catch {
    // If WebGL fails, silently abort without crashing
    return
  }

  // Create subtle flowing topological ribbons (representing query discovery streams)
  const segments = 45
  const curves = 8
  const positions: number[] = []
  const colors: number[] = []

  const colorA = new THREE.Color('#38bdf8') // cyan Muse personal agent intent
  const colorB = new THREE.Color('#818cf8') // violet Profound AI perception
  const colorC = new THREE.Color('#10b981') // emerald verified Product Truth

  for (let c = 0; c < curves; c++) {
    const xOffset = (c - curves / 2) * 14
    const zOffset = (c - curves / 2) * 6
    for (let i = 0; i < segments; i++) {
      const t1 = (i / segments) * Math.PI * 3
      const t2 = ((i + 1) / segments) * Math.PI * 3

      const x1 = xOffset + Math.sin(t1 + c) * 12
      const y1 = Math.sin(t1 * 1.5 + c * 0.8) * 4 - 8
      const z1 = zOffset + (i / segments) * 60 - 30

      const x2 = xOffset + Math.sin(t2 + c) * 12
      const y2 = Math.sin(t2 * 1.5 + c * 0.8) * 4 - 8
      const z2 = zOffset + ((i + 1) / segments) * 60 - 30

      positions.push(x1, y1, z1, x2, y2, z2)

      const mix = i / segments
      const col = mix < 0.5 ? colorA.clone().lerp(colorB, mix * 2) : colorB.clone().lerp(colorC, (mix - 0.5) * 2)
      colors.push(col.r, col.g, col.b, col.r, col.g, col.b)
    }
  }

  const geometry = new THREE.BufferGeometry()
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3))
  geometry.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3))

  const material = new THREE.LineBasicMaterial({
    vertexColors: true,
    transparent: true,
    opacity: 0.12, // extremely subtle
    blending: THREE.AdditiveBlending
  })

  lines = new THREE.LineSegments(geometry, material)
  scene.add(lines)

  let clock = 0
  function animate() {
    if (!isVisible || prefersReducedMotion) return
    animFrameId = requestAnimationFrame(animate)

    clock += 0.003
    if (lines) {
      lines.rotation.y = Math.sin(clock * 0.5) * 0.08
      lines.position.y = Math.cos(clock * 0.7) * 0.8
    }

    if (renderer && scene && camera) {
      renderer.render(scene, camera)
    }
  }

  animate()

  function onResize() {
    if (!containerRef.value || !renderer || !camera) return
    const w = containerRef.value.clientWidth || window.innerWidth
    const h = containerRef.value.clientHeight || window.innerHeight
    camera.aspect = w / h
    camera.updateProjectionMatrix()
    renderer.setSize(w, h)
  }

  function onVisibility() {
    isVisible = !document.hidden
    if (isVisible && !animFrameId) {
      animate()
    }
  }

  window.addEventListener('resize', onResize)
  document.addEventListener('visibilitychange', onVisibility)

  onBeforeUnmount(() => {
    window.removeEventListener('resize', onResize)
    document.removeEventListener('visibilitychange', onVisibility)
    if (animFrameId) cancelAnimationFrame(animFrameId)
    if (lines) {
      lines.geometry.dispose()
      if (Array.isArray(lines.material)) {
        lines.material.forEach(m => m.dispose())
      } else {
        lines.material.dispose()
      }
    }
    if (renderer) {
      renderer.dispose()
      if (renderer.domElement && renderer.domElement.parentNode) {
        renderer.domElement.parentNode.removeChild(renderer.domElement)
      }
    }
    scene = null
    camera = null
    renderer = null
  })
}

onMounted(() => {
  initScene()
})
</script>

<template>
  <div ref="containerRef" class="ambient-field" aria-hidden="true" />
</template>

<style scoped>
.ambient-field {
  position: fixed;
  inset: 0;
  pointer-events: none;
  z-index: 0;
  overflow: hidden;
  opacity: 0.85;
}
</style>
