<script setup lang="ts">
import * as THREE from 'three'
import type { GraphNodeSemantic, GraphEdgeSemantic } from '~/types/agentmatch'

const props = defineProps<{
  nodes: GraphNodeSemantic[]
  edges: GraphEdgeSemantic[]
  selectedNodeId?: string | null
}>()

const emit = defineEmits<{
  selectNode: [id: string | null]
}>()

const canvasContainer = ref<HTMLDivElement | null>(null)
const hoveredNode = ref<GraphNodeSemantic | null>(null)
const tooltipPos = ref({ x: 0, y: 0 })

let scene: THREE.Scene | null = null
let camera: THREE.PerspectiveCamera | null = null
let renderer: THREE.WebGLRenderer | null = null
let animId: number | null = null
let raycaster: THREE.Raycaster | null = null
let mouse: THREE.Vector2 | null = null

// Interaction state
let isDragging = false
let previousMousePosition = { x: 0, y: 0 }
const cameraTarget = new THREE.Vector3(0, 0, 1)
const initialCameraPos = new THREE.Vector3(0, 10, 26)
let currentCameraPos = new THREE.Vector3(0, 10, 26)

// Object pools for cleanup
const nodeMeshes: Map<string, THREE.Group> = new Map()
const edgeLines: THREE.Line[] = []
const texturesToDispose: THREE.Texture[] = []
const materialsToDispose: THREE.Material[] = []
const geometriesToDispose: THREE.BufferGeometry[] = []

// Spatial positioning helper
function computeNodePositions(nodes: GraphNodeSemantic[]): Map<string, THREE.Vector3> {
  const positions = new Map<string, THREE.Vector3>()
  // Group by z layer
  const byZ = new Map<number, GraphNodeSemantic[]>()
  nodes.forEach(n => {
    const list = byZ.get(n.z) ?? []
    list.push(n)
    byZ.set(n.z, list)
  })

  byZ.forEach((list, z) => {
    const count = list.length
    list.forEach((node, idx) => {
      const angle = count > 1 ? ((idx - (count - 1) / 2) / (count || 1)) * 1.8 : 0
      const radius = count > 1 ? 5 + (idx % 2) * 1.5 : 0
      const x = Math.sin(angle) * (count > 2 ? 6.5 : 4)
      const y = (idx - (count - 1) / 2) * 2.8
      positions.set(node.id, new THREE.Vector3(x, y, z))
    })
  })
  return positions
}

function createTextTexture(title: string, subtitle: string, colorHex: string): THREE.Texture {
  const canvas = document.createElement('canvas')
  canvas.width = 440
  canvas.height = 140
  const ctx = canvas.getContext('2d')
  if (ctx) {
    ctx.fillStyle = 'rgba(10, 16, 32, 0.92)'
    ctx.strokeStyle = colorHex
    ctx.lineWidth = 5
    ctx.beginPath()
    ctx.roundRect(6, 6, 428, 128, 18)
    ctx.fill()
    ctx.stroke()

    ctx.font = 'bold 28px sans-serif'
    ctx.fillStyle = '#ffffff'
    const displayTitle = title.length > 24 ? title.slice(0, 23) + '…' : title
    ctx.fillText(displayTitle, 24, 56)

    ctx.font = '20px monospace'
    ctx.fillStyle = colorHex
    ctx.fillText(subtitle.toUpperCase(), 24, 98)
  }

  const texture = new THREE.CanvasTexture(canvas)
  texturesToDispose.push(texture)
  return texture
}

function initScene() {
  if (!canvasContainer.value) return

  const width = canvasContainer.value.clientWidth || 800
  const height = canvasContainer.value.clientHeight || 450

  scene = new THREE.Scene()
  scene.fog = new THREE.FogExp2(0x060913, 0.015)

  camera = new THREE.PerspectiveCamera(45, width / height, 0.1, 1000)
  camera.position.copy(initialCameraPos)
  camera.lookAt(cameraTarget)

  renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true })
  renderer.setSize(width, height)
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
  canvasContainer.value.appendChild(renderer.domElement)

  raycaster = new THREE.Raycaster()
  mouse = new THREE.Vector2()

  // Ambient & directional light
  const ambient = new THREE.AmbientLight(0xffffff, 0.8)
  scene.add(ambient)

  const dirLight = new THREE.DirectionalLight(0x38bdf8, 1.2)
  dirLight.position.set(10, 20, 20)
  scene.add(dirLight)

  // Add subtle reference grid plane at z=0 (Products layer)
  const gridGeo = new THREE.GridHelper(40, 20, 0x1e293b, 0x0f172a)
  gridGeo.rotation.x = Math.PI / 2
  gridGeo.position.z = 0
  scene.add(gridGeo)

  buildGraph()

  // Attach event listeners
  const el = renderer.domElement
  el.addEventListener('mousedown', onMouseDown)
  el.addEventListener('mousemove', onMouseMove)
  el.addEventListener('mouseup', onMouseUp)
  el.addEventListener('wheel', onWheel, { passive: false })
  el.addEventListener('click', onClick)

  animate()
}

function buildGraph() {
  if (!scene) return

  // Clean previous
  nodeMeshes.forEach(grp => scene?.remove(grp))
  nodeMeshes.clear()
  edgeLines.forEach(line => scene?.remove(line))
  edgeLines.length = 0

  const positions = computeNodePositions(props.nodes)

  // Build nodes
  props.nodes.forEach(node => {
    const pos = positions.get(node.id) || new THREE.Vector3(0, 0, node.z)
    const group = new THREE.Group()
    group.position.copy(pos)

    const color = new THREE.Color(node.color || '#38bdf8')

    // Central Sphere
    const sphereGeo = new THREE.SphereGeometry(0.7, 24, 24)
    geometriesToDispose.push(sphereGeo)
    const sphereMat = new THREE.MeshStandardMaterial({
      color,
      roughness: 0.2,
      metalness: 0.8,
      emissive: color,
      emissiveIntensity: 0.4
    })
    materialsToDispose.push(sphereMat)
    const sphere = new THREE.Mesh(sphereGeo, sphereMat)
    sphere.userData = { nodeId: node.id, node }
    group.add(sphere)

    // Outer halo ring
    const ringGeo = new THREE.RingGeometry(0.9, 1.05, 32)
    geometriesToDispose.push(ringGeo)
    const ringMat = new THREE.MeshBasicMaterial({
      color,
      side: THREE.DoubleSide,
      transparent: true,
      opacity: 0.7
    })
    materialsToDispose.push(ringMat)
    const ring = new THREE.Mesh(ringGeo, ringMat)
    group.add(ring)

    // Text Label Sprite
    const texture = createTextTexture(node.label, `${node.type} (z=${node.z > 0 ? `+${node.z}` : node.z})`, node.color)
    const spriteMat = new THREE.SpriteMaterial({ map: texture, transparent: true })
    materialsToDispose.push(spriteMat)
    const sprite = new THREE.Sprite(spriteMat)
    sprite.scale.set(4.2, 1.35, 1)
    sprite.position.set(0, 1.4, 0)
    group.add(sprite)

    scene?.add(group)
    nodeMeshes.set(node.id, group)
  })

  // Build edges
  props.edges.forEach(edge => {
    const srcPos = positions.get(edge.source)
    const dstPos = positions.get(edge.target)
    if (!srcPos || !dstPos) return

    const points = []
    points.push(srcPos)

    // Midpoint curved slightly
    const mid = new THREE.Vector3()
      .addVectors(srcPos, dstPos)
      .multiplyScalar(0.5)
    mid.y += 0.4
    points.push(mid)
    points.push(dstPos)

    const curve = new THREE.QuadraticBezierCurve3(srcPos, mid, dstPos)
    const curvePoints = curve.getPoints(24)

    const lineGeo = new THREE.BufferGeometry().setFromPoints(curvePoints)
    geometriesToDispose.push(lineGeo)

    const lineColor = new THREE.Color(edge.color || '#64748b')
    const lineMat = new THREE.LineBasicMaterial({
      color: lineColor,
      transparent: true,
      opacity: edge.dashed ? 0.8 : 0.45,
      linewidth: edge.dashed ? 2 : 1
    })
    materialsToDispose.push(lineMat)

    const line = new THREE.Line(lineGeo, lineMat)
    scene?.add(line)
    edgeLines.push(line)
  })
}

// Interactivity
function onMouseDown(e: MouseEvent) {
  isDragging = true
  previousMousePosition = { x: e.clientX, y: e.clientY }
}

function onMouseMove(e: MouseEvent) {
  if (!canvasContainer.value || !renderer || !camera || !raycaster || !mouse) return

  const rect = canvasContainer.value.getBoundingClientRect()
  mouse.x = ((e.clientX - rect.left) / rect.width) * 2 - 1
  mouse.y = -((e.clientY - rect.top) / rect.height) * 2 + 1

  if (isDragging) {
    const deltaX = e.clientX - previousMousePosition.x
    const deltaY = e.clientY - previousMousePosition.y

    // Orbit around target
    const angleX = deltaX * 0.005
    const angleY = deltaY * 0.005

    camera.position.sub(cameraTarget)
    camera.position.applyAxisAngle(new THREE.Vector3(0, 1, 0), -angleX)
    camera.position.applyAxisAngle(new THREE.Vector3(1, 0, 0), -angleY)
    camera.position.add(cameraTarget)
    camera.lookAt(cameraTarget)

    previousMousePosition = { x: e.clientX, y: e.clientY }
  } else {
    // Raycast hover
    raycaster.setFromCamera(mouse, camera)
    const objectsToIntersect: THREE.Object3D[] = []
    nodeMeshes.forEach(grp => {
      const sphere = grp.children[0]
      if (sphere) objectsToIntersect.push(sphere)
    })

    const intersects = raycaster.intersectObjects(objectsToIntersect, false)
    const firstHit = intersects[0]
    if (firstHit) {
      const hit = firstHit.object
      hoveredNode.value = hit.userData.node
      tooltipPos.value = { x: e.clientX - rect.left + 15, y: e.clientY - rect.top + 15 }
    } else {
      hoveredNode.value = null
    }
  }
}

function onMouseUp() {
  isDragging = false
}

function onWheel(e: WheelEvent) {
  e.preventDefault()
  if (!camera) return
  const zoomFactor = e.deltaY > 0 ? 1.08 : 0.92
  camera.position.sub(cameraTarget).multiplyScalar(zoomFactor).add(cameraTarget)
}

function onClick(e: MouseEvent) {
  if (!canvasContainer.value || !camera || !raycaster || !mouse) return
  const rect = canvasContainer.value.getBoundingClientRect()
  mouse.x = ((e.clientX - rect.left) / rect.width) * 2 - 1
  mouse.y = -((e.clientY - rect.top) / rect.height) * 2 + 1

  raycaster.setFromCamera(mouse, camera)
  const objectsToIntersect: THREE.Object3D[] = []
  nodeMeshes.forEach(grp => {
    const sphere = grp.children[0]
    if (sphere) objectsToIntersect.push(sphere)
  })

  const intersects = raycaster.intersectObjects(objectsToIntersect, false)
  const firstHit = intersects[0]
  if (firstHit) {
    const hitNodeId = firstHit.object.userData.nodeId
    emit('selectNode', hitNodeId)
  } else {
    emit('selectNode', null)
  }
}

function resetCamera() {
  if (!camera) return
  camera.position.copy(initialCameraPos)
  camera.lookAt(cameraTarget)
}

let startTime = performance.now()
function animate() {
  animId = requestAnimationFrame(animate)

  const elapsed = (performance.now() - startTime) / 1000

  // Gentle settling pulse on halo rings
  nodeMeshes.forEach((grp, id) => {
    const ring = grp.children[1] as THREE.Mesh
    if (ring) {
      const isSelected = id === props.selectedNodeId
      const scale = isSelected ? 1.25 + Math.sin(elapsed * 4) * 0.1 : 1.0 + Math.sin(elapsed * 1.5) * 0.04
      ring.scale.set(scale, scale, 1)
      ring.lookAt(camera!.position)
    }
    const sprite = grp.children[2] as THREE.Sprite
    if (sprite) {
      // Billboarding is automatic for sprites
    }
  })

  if (renderer && scene && camera) {
    renderer.render(scene, camera)
  }
}

// Watch props to rebuild
watch(() => [props.nodes, props.edges], () => {
  buildGraph()
}, { deep: true })

onMounted(() => {
  initScene()
  window.addEventListener('resize', handleResize)
})

function handleResize() {
  if (!canvasContainer.value || !renderer || !camera) return
  const width = canvasContainer.value.clientWidth
  const height = canvasContainer.value.clientHeight
  camera.aspect = width / height
  camera.updateProjectionMatrix()
  renderer.setSize(width, height)
}

onBeforeUnmount(() => {
  if (animId !== null) cancelAnimationFrame(animId)
  window.removeEventListener('resize', handleResize)

  geometriesToDispose.forEach(g => g.dispose())
  materialsToDispose.forEach(m => m.dispose())
  texturesToDispose.forEach(t => t.dispose())

  if (renderer && renderer.domElement) {
    renderer.domElement.remove()
    renderer.dispose()
  }
})
</script>

<template>
  <div class="agentmatch-scene-wrap">
    <div ref="canvasContainer" class="canvas-container" role="img" aria-label="Profound Lift 3D semantic graph">
      <!-- Overlay controls -->
      <div class="scene-overlay-controls">
        <button class="scene-btn" type="button" title="Reset Camera View" @click="resetCamera">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
            <path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" />
            <path d="M3 3v5h5" />
          </svg>
          Reset View
        </button>
        <span class="depth-hint">Layers: z=-8 (Intent) → z=0 (Product) → z=+10 (Gap)</span>
      </div>

      <!-- Hover Tooltip -->
      <div
        v-if="hoveredNode"
        class="scene-tooltip"
        :style="{ left: `${tooltipPos.x}px`, top: `${tooltipPos.y}px` }"
      >
        <div class="tooltip-header">
          <span class="tooltip-dot" :style="{ backgroundColor: hoveredNode.color }" />
          <span class="tooltip-title">{{ hoveredNode.label }}</span>
        </div>
        <div class="tooltip-meta">
          <span class="tooltip-type">{{ hoveredNode.type }}</span>
          <span class="tooltip-z">z = {{ hoveredNode.z > 0 ? `+${hoveredNode.z}` : hoveredNode.z }}</span>
        </div>
        <div v-if="hoveredNode.status" class="tooltip-status">
          Status: <strong>{{ hoveredNode.status }}</strong>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.agentmatch-scene-wrap {
  position: relative;
  width: 100%;
  height: 440px;
  background: radial-gradient(circle at 50% 50%, rgba(15, 23, 42, 0.8) 0%, rgba(6, 9, 19, 0.98) 100%);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: var(--radius-md, 8px);
  overflow: hidden;
}
.canvas-container {
  width: 100%;
  height: 100%;
  position: relative;
  cursor: grab;
}
.canvas-container:active {
  cursor: grabbing;
}
.scene-overlay-controls {
  position: absolute;
  top: 12px;
  left: 12px;
  display: flex;
  align-items: center;
  gap: 12px;
  pointer-events: auto;
  z-index: 10;
}
.scene-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: rgba(15, 23, 42, 0.85);
  border: 1px solid rgba(255, 255, 255, 0.15);
  color: #e2e8f0;
  font-size: 11px;
  font-weight: 600;
  padding: 4px 10px;
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.15s ease;
}
.scene-btn:hover {
  background: rgba(30, 41, 69, 0.95);
  border-color: #38bdf8;
  color: #38bdf8;
}
.depth-hint {
  font-size: 11px;
  color: #64748b;
  font-family: var(--font-mono, monospace);
  background: rgba(10, 15, 30, 0.6);
  padding: 3px 8px;
  border-radius: 4px;
}
.scene-tooltip {
  position: absolute;
  pointer-events: none;
  background: rgba(10, 16, 32, 0.96);
  border: 1px solid rgba(56, 189, 248, 0.4);
  box-shadow: 0 4px 20px rgba(0, 0, 0, 0.5);
  border-radius: 6px;
  padding: 8px 12px;
  z-index: 20;
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-width: 260px;
}
.tooltip-header {
  display: flex;
  align-items: center;
  gap: 6px;
}
.tooltip-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
}
.tooltip-title {
  font-size: 12px;
  font-weight: 600;
  color: #ffffff;
}
.tooltip-meta {
  display: flex;
  justify-content: space-between;
  font-size: 11px;
  color: #94a3b8;
  font-family: var(--font-mono, monospace);
}
.tooltip-type {
  color: #38bdf8;
  text-transform: uppercase;
}
.tooltip-status {
  font-size: 11px;
  color: #cbd5e1;
}
</style>
