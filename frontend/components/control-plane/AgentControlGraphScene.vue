<script setup lang="ts">
import * as THREE from 'three'
import type { ControlPlaneGraphEdge, ControlPlaneGraphNode } from '~/types'

const props = withDefaults(
  defineProps<{
    nodes: ControlPlaneGraphNode[]
    edges: ControlPlaneGraphEdge[]
    selectedNodeId?: string | null
  }>(),
  {
    selectedNodeId: null,
  }
)

const emit = defineEmits<{
  (e: 'select', node: ControlPlaneGraphNode): void
  (e: 'clear-selection'): void
}>()

const canvasContainer = ref<HTMLDivElement | null>(null)
const hoveredNode = ref<ControlPlaneGraphNode | null>(null)
const tooltipX = ref(0)
const tooltipY = ref(0)
const showAccessibleTree = ref(false)

// Color Palette for Flight Deck
const STATUS_COLORS: Record<string, number> = {
  positive: 0x10b981, // emerald
  negative: 0xef4444, // rose / red
  uncertain: 0xf59e0b, // amber
  running: 0x38bdf8, // sky blue
  neutral: 0x64748b, // slate
}

const TYPE_COLORS: Record<string, number> = {
  agent: 0x38bdf8,
  campaign: 0x6366f1,
  decision: 0xa855f7,
  experiment: 0x06b6d4,
  outcome: 0x10b981,
  asset: 0x2dd4bf,
  task: 0x818cf8,
  signal: 0xf43f5e,
}

let scene: THREE.Scene | null = null
let camera: THREE.PerspectiveCamera | null = null
let renderer: THREE.WebGLRenderer | null = null
let animFrameId: number | null = null

const nodeMeshes = new Map<string, THREE.Mesh>()
let particlesMesh: THREE.Points | null = null
let raycaster: THREE.Raycaster | null = null
let mouse: THREE.Vector2 | null = null

// Check for reduced motion
const prefersReducedMotion = ref(false)
if (typeof window !== 'undefined' && window.matchMedia) {
  prefersReducedMotion.value = window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

function initThree() {
  if (!canvasContainer.value) return

  const width = canvasContainer.value.clientWidth || 800
  const height = canvasContainer.value.clientHeight || 500

  // 1. Scene
  scene = new THREE.Scene()
  scene.fog = new THREE.FogExp2(0x0a0f1d, 0.02)

  // 2. Camera
  camera = new THREE.PerspectiveCamera(45, width / height, 0.1, 1000)
  camera.position.set(0, 8, 28)
  camera.lookAt(0, 0, 0)

  // 3. Renderer
  try {
    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true })
  } catch {
    showAccessibleTree.value = true
    return
  }

  renderer.setSize(width, height)
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
  canvasContainer.value.appendChild(renderer.domElement)

  // 4. Lights
  const ambientLight = new THREE.AmbientLight(0xffffff, 0.9)
  scene.add(ambientLight)

  const dirLight = new THREE.DirectionalLight(0x38bdf8, 1.2)
  dirLight.position.set(10, 20, 15)
  scene.add(dirLight)

  const purpleLight = new THREE.PointLight(0xa855f7, 1.5, 40)
  purpleLight.position.set(-10, -5, -5)
  scene.add(purpleLight)

  // 5. Ambient background grid / dust
  createAmbientField()

  // 6. Build operational graph
  rebuildGraph()

  // 7. Raycaster & Events
  raycaster = new THREE.Raycaster()
  mouse = new THREE.Vector2()

  canvasContainer.value.addEventListener('mousemove', onMouseMove)
  canvasContainer.value.addEventListener('click', onClick)
  window.addEventListener('resize', onResize)

  // 8. Animation loop
  let lastTime = 0
  const animate = (time: number) => {
    animFrameId = requestAnimationFrame(animate)

    if (!prefersReducedMotion.value && camera) {
      // Gentle subtle camera breathing
      const drift = Math.sin(time * 0.0005) * 0.4
      camera.position.x = drift
      camera.lookAt(0, 0, 0)
    }

    // Animate flow particles
    if (particlesMesh && !prefersReducedMotion.value) {
      const positions = particlesMesh.geometry.attributes.position.array as Float32Array
      for (let i = 1; i < positions.length; i += 3) {
        positions[i] += Math.sin(time * 0.002 + i) * 0.015
      }
      particlesMesh.geometry.attributes.position.needsUpdate = true
    }

    if (renderer && scene && camera) {
      renderer.render(scene, camera)
    }
  }

  animFrameId = requestAnimationFrame(animate)
}

function createAmbientField() {
  if (!scene) return
  const count = 120
  const geom = new THREE.BufferGeometry()
  const pos = new Float32Array(count * 3)

  for (let i = 0; i < count * 3; i += 3) {
    pos[i] = (Math.random() - 0.5) * 45
    pos[i + 1] = (Math.random() - 0.5) * 25
    pos[i + 2] = (Math.random() - 0.5) * 30
  }

  geom.setAttribute('position', new THREE.BufferAttribute(pos, 3))
  const mat = new THREE.PointsMaterial({
    color: 0x38bdf8,
    size: 0.15,
    transparent: true,
    opacity: 0.25,
  })

  particlesMesh = new THREE.Points(geom, mat)
  scene.add(particlesMesh)
}

function rebuildGraph() {
  if (!scene) return

  // Clean old node meshes
  for (const [, mesh] of nodeMeshes) {
    scene.remove(mesh)
    mesh.geometry.dispose()
    if (Array.isArray(mesh.material)) {
      mesh.material.forEach((m) => m.dispose())
    } else {
      mesh.material.dispose()
    }
  }
  nodeMeshes.clear()

  // Build Nodes
  for (const node of props.nodes) {
    const isSelected = props.selectedNodeId === node.id
    const color = STATUS_COLORS[node.status] || TYPE_COLORS[node.type] || 0x64748b

    let geom: THREE.BufferGeometry
    if (node.type === 'campaign') {
      geom = new THREE.CylinderGeometry(1.2, 1.2, 0.4, 24)
    } else if (node.type === 'agent') {
      geom = new THREE.SphereGeometry(0.85, 20, 20)
    } else if (node.type === 'decision') {
      geom = new THREE.OctahedronGeometry(0.75)
    } else if (node.type === 'experiment') {
      geom = new THREE.BoxGeometry(1.1, 1.1, 1.1)
    } else {
      geom = new THREE.DodecahedronGeometry(0.8)
    }

    const mat = new THREE.MeshStandardMaterial({
      color,
      roughness: 0.2,
      metalness: 0.6,
      emissive: isSelected ? color : (node.status === 'running' ? 0x0284c7 : 0x000000),
      emissiveIntensity: isSelected ? 0.6 : (node.status === 'running' ? 0.3 : 0.0),
    })

    const mesh = new THREE.Mesh(geom, mat)
    mesh.position.set(node.x, node.y, node.z)
    mesh.userData = { node }

    scene.add(mesh)
    nodeMeshes.set(node.id, mesh)
  }

  // Build Edges
  for (const edge of props.edges) {
    const src = nodeMeshes.get(edge.source)
    const tgt = nodeMeshes.get(edge.target)
    if (!src || !tgt) continue

    const points = [src.position.clone(), tgt.position.clone()]
    const lineGeom = new THREE.BufferGeometry().setFromPoints(points)

    const edgeColor =
      edge.status === 'positive'
        ? 0x10b981
        : edge.status === 'negative'
        ? 0xef4444
        : edge.status === 'active'
        ? 0x38bdf8
        : 0x475569

    const lineMat = new THREE.LineBasicMaterial({
      color: edgeColor,
      transparent: true,
      opacity: edge.status === 'active' ? 0.8 : 0.35,
      linewidth: 2,
    })

    const line = new THREE.Line(lineGeom, lineMat)
    scene.add(line)
  }
}

watch(
  () => [props.nodes, props.edges, props.selectedNodeId],
  () => {
    rebuildGraph()
  },
  { deep: true }
)

function onMouseMove(e: MouseEvent) {
  if (!canvasContainer.value || !camera || !raycaster || !mouse) return

  const rect = canvasContainer.value.getBoundingClientRect()
  mouse.x = ((e.clientX - rect.left) / rect.width) * 2 - 1
  mouse.y = -((e.clientY - rect.top) / rect.height) * 2 + 1

  raycaster.setFromCamera(mouse, camera)
  const intersects = raycaster.intersectObjects(Array.from(nodeMeshes.values()))

  if (intersects.length > 0) {
    const hitNode = intersects[0].object.userData.node as ControlPlaneGraphNode
    hoveredNode.value = hitNode
    tooltipX.value = e.clientX - rect.left + 16
    tooltipY.value = e.clientY - rect.top + 16
  } else {
    hoveredNode.value = null
  }
}

function onClick(e: MouseEvent) {
  if (!canvasContainer.value || !camera || !raycaster || !mouse) return

  const rect = canvasContainer.value.getBoundingClientRect()
  mouse.x = ((e.clientX - rect.left) / rect.width) * 2 - 1
  mouse.y = -((e.clientY - rect.top) / rect.height) * 2 + 1

  raycaster.setFromCamera(mouse, camera)
  const intersects = raycaster.intersectObjects(Array.from(nodeMeshes.values()))

  if (intersects.length > 0) {
    const hitNode = intersects[0].object.userData.node as ControlPlaneGraphNode
    emit('select', hitNode)
  } else {
    emit('clear-selection')
  }
}

function onResize() {
  if (!canvasContainer.value || !camera || !renderer) return
  const width = canvasContainer.value.clientWidth
  const height = canvasContainer.value.clientHeight
  camera.aspect = width / height
  camera.updateProjectionMatrix()
  renderer.setSize(width, height)
}

function resetCamera() {
  if (!camera) return
  camera.position.set(0, 8, 28)
  camera.lookAt(0, 0, 0)
}

onMounted(() => {
  initThree()
})

onBeforeUnmount(() => {
  if (animFrameId) cancelAnimationFrame(animFrameId)
  if (canvasContainer.value) {
    canvasContainer.value.removeEventListener('mousemove', onMouseMove)
    canvasContainer.value.removeEventListener('click', onClick)
  }
  window.removeEventListener('resize', onResize)

  if (renderer && renderer.domElement && renderer.domElement.parentNode) {
    renderer.domElement.parentNode.removeChild(renderer.domElement)
    renderer.dispose()
  }
})
</script>

<template>
  <div class="flight-deck-wrapper">
    <!-- Floating Toolbar -->
    <div class="scene-toolbar">
      <div class="legend-row">
        <span class="legend-chip"><i class="dot positive"></i> Positive</span>
        <span class="legend-chip"><i class="dot running"></i> Active</span>
        <span class="legend-chip"><i class="dot negative"></i> Negative</span>
        <span class="legend-chip"><i class="dot uncertain"></i> Review</span>
      </div>
      <div class="actions-row">
        <button
          type="button"
          class="btn-tool"
          :class="{ active: showAccessibleTree }"
          title="Toggle Accessible DOM Hierarchy"
          @click="showAccessibleTree = !showAccessibleTree"
        >
          🌳 Tree View
        </button>
        <button type="button" class="btn-tool" title="Reset Viewport Position" @click="resetCamera">
          🎯 Reset
        </button>
      </div>
    </div>

    <!-- 3D WebGL Canvas Container -->
    <div ref="canvasContainer" class="canvas-container" aria-label="3D Live Agent Control Plane Canvas"></div>

    <!-- Hover Tooltip Card -->
    <div
      v-if="hoveredNode"
      class="hover-card"
      :style="{ left: `${tooltipX}px`, top: `${tooltipY}px` }"
    >
      <div class="hover-badge-row">
        <span class="type-tag">{{ hoveredNode.type }}</span>
        <span :class="['status-tag', hoveredNode.status]">{{ hoveredNode.status }}</span>
      </div>
      <strong class="hover-title">{{ hoveredNode.label }}</strong>
      <div v-if="hoveredNode.meta" class="hover-details">
        <span v-if="hoveredNode.meta.cost" class="detail-item">
          Cost: ${{ Number(hoveredNode.meta.cost).toFixed(2) }}
        </span>
        <span v-if="hoveredNode.meta.return" class="detail-item">
          Return: ${{ Number(hoveredNode.meta.return).toLocaleString() }}
        </span>
        <span v-if="hoveredNode.meta.runs" class="detail-item">
          Runs: {{ hoveredNode.meta.runs }}
        </span>
      </div>
    </div>

    <!-- Accessible DOM Semantic Fallback Tree -->
    <div v-if="showAccessibleTree" class="accessible-tree-overlay" role="tree" aria-label="Flight Deck Topology">
      <div class="tree-header">
        <h4>Semantic Control Plane Topology</h4>
        <button class="btn-close-tree" @click="showAccessibleTree = false">✕</button>
      </div>
      <ul class="tree-list">
        <li v-for="node in nodes" :key="node.id" role="treeitem" tabindex="0" @click="emit('select', node)">
          <span class="tree-type">[{{ node.type }}]</span>
          <strong class="tree-label">{{ node.label }}</strong>
          <span :class="['tree-status', node.status]">({{ node.status }})</span>
        </li>
      </ul>
    </div>
  </div>
</template>

<style scoped>
.flight-deck-wrapper {
  position: relative;
  width: 100%;
  height: 480px;
  background: radial-gradient(circle at 50% 30%, #172554 0%, #0a0f1d 75%);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 12px;
  overflow: hidden;
  box-shadow: inset 0 2px 20px rgba(0, 0, 0, 0.4);
}

.canvas-container {
  width: 100%;
  height: 100%;
  cursor: grab;
}
.canvas-container:active {
  cursor: grabbing;
}

.scene-toolbar {
  position: absolute;
  top: 14px;
  left: 16px;
  right: 16px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  z-index: var(--z-sticky, 10);
  pointer-events: none;
}

.legend-row {
  display: flex;
  gap: 10px;
  background: rgba(15, 23, 42, 0.85);
  backdrop-filter: blur(8px);
  padding: 6px 12px;
  border-radius: 6px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  pointer-events: auto;
}

.legend-chip {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  color: #cbd5e1;
  font-weight: 500;
}

.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
}
.dot.positive {
  background: #10b981;
  box-shadow: 0 0 6px #10b981;
}
.dot.running {
  background: #38bdf8;
  box-shadow: 0 0 6px #38bdf8;
}
.dot.negative {
  background: #ef4444;
  box-shadow: 0 0 6px #ef4444;
}
.dot.uncertain {
  background: #f59e0b;
  box-shadow: 0 0 6px #f59e0b;
}

.actions-row {
  display: flex;
  gap: 8px;
  pointer-events: auto;
}

.btn-tool {
  background: rgba(15, 23, 42, 0.85);
  backdrop-filter: blur(8px);
  border: 1px solid rgba(255, 255, 255, 0.12);
  color: #94a3b8;
  font-size: 11px;
  font-weight: 600;
  padding: 6px 12px;
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.15s;
}
.btn-tool:hover {
  color: #f8fafc;
  border-color: #38bdf8;
}
.btn-tool.active {
  background: rgba(56, 189, 248, 0.2);
  color: #38bdf8;
  border-color: #38bdf8;
}

.hover-card {
  position: absolute;
  pointer-events: none;
  background: rgba(15, 23, 42, 0.95);
  backdrop-filter: blur(10px);
  border: 1px solid rgba(56, 189, 248, 0.35);
  border-radius: 8px;
  padding: 10px 14px;
  max-width: 280px;
  z-index: var(--z-tooltip, 30);
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.6);
  animation: fadeIn 0.15s ease-out;
}

@keyframes fadeIn {
  from {
    opacity: 0;
    transform: translateY(4px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.hover-badge-row {
  display: flex;
  gap: 6px;
  margin-bottom: 4px;
}

.type-tag {
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
  background: rgba(255, 255, 255, 0.08);
  color: #94a3b8;
  padding: 2px 6px;
  border-radius: 4px;
}

.status-tag {
  font-size: 10px;
  font-weight: 600;
  text-transform: uppercase;
  padding: 2px 6px;
  border-radius: 4px;
}
.status-tag.positive {
  background: rgba(16, 185, 129, 0.2);
  color: #34d399;
}
.status-tag.running {
  background: rgba(56, 189, 248, 0.2);
  color: #38bdf8;
}
.status-tag.negative {
  background: rgba(239, 68, 68, 0.2);
  color: #f87171;
}
.status-tag.uncertain {
  background: rgba(245, 158, 11, 0.2);
  color: #fbbf24;
}

.hover-title {
  display: block;
  font-size: 13px;
  color: #f8fafc;
  line-height: 1.3;
}

.hover-details {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 6px;
  font-size: 11px;
  color: #cbd5e1;
}

.accessible-tree-overlay {
  position: absolute;
  inset: 0;
  background: rgba(15, 23, 42, 0.95);
  overflow-y: auto;
  padding: 20px;
  z-index: var(--z-modal, 50);
}

.tree-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  border-bottom: 1px solid rgba(255, 255, 255, 0.1);
  padding-bottom: 10px;
  margin-bottom: 14px;
}

.tree-header h4 {
  margin: 0;
  font-size: 15px;
  color: #f8fafc;
}

.btn-close-tree {
  background: transparent;
  border: none;
  color: #94a3b8;
  font-size: 16px;
  cursor: pointer;
}

.tree-list {
  list-style: none;
  padding: 0;
  margin: 0;
}

.tree-list li {
  padding: 8px 12px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.05);
  cursor: pointer;
  display: flex;
  gap: 8px;
  align-items: center;
  font-size: 13px;
}
.tree-list li:hover {
  background: rgba(255, 255, 255, 0.05);
}

.tree-type {
  font-family: monospace;
  color: #38bdf8;
}

.tree-label {
  color: #f8fafc;
}

.tree-status {
  font-size: 11px;
  text-transform: uppercase;
}
.tree-status.positive {
  color: #10b981;
}
.tree-status.negative {
  color: #ef4444;
}
.tree-status.running {
  color: #38bdf8;
}
.tree-status.uncertain {
  color: #f59e0b;
}
</style>
