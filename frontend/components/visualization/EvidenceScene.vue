<script setup lang="ts">
import * as THREE from 'three'
import type { GraphData, GraphNode, GraphEdge } from '~/types'

const props = defineProps<{
  graph: GraphData
  selectedNodeId?: string | null
  activeEventNodeId?: string | null
}>()

const emit = defineEmits<{
  selectNode: [id: string | null]
}>()

const canvasContainer = ref<HTMLDivElement | null>(null)
const hoveredNode = ref<GraphNode | null>(null)
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
const cameraTarget = new THREE.Vector3(0, 0, 0)
const initialCameraPos = new THREE.Vector3(0, 12, 38)
let currentCameraPos = new THREE.Vector3(0, 12, 38)

// Object pools for cleanup
const nodeMeshes: Map<string, THREE.Group> = new Map()
const edgeLines: THREE.Line[] = []
const texturesToDispose: THREE.Texture[] = []
const materialsToDispose: THREE.Material[] = []
const geometriesToDispose: THREE.BufferGeometry[] = []

// Color semantics for Change Topology & Evidence DAG
const TYPE_COLORS: Record<string, string> = {
  agent: '#6366f1',
  changeset: '#38bdf8',
  target: '#10b981',
  claim: '#2dd4bf',
  canonical_truth: '#06b6d4',
  conflict: '#f43f5e',
  experiment: '#3b82f6',
  prompt_cluster: '#8b5cf6',
  incident: '#f43f5e',
  profound: '#6366f1',
  signal: '#38bdf8',
  prompt: '#8b5cf6',
  citation: '#38bdf8',
  external: '#a855f7',
  competitor: '#f97316',
  owned: '#10b981',
  inference: '#8b5cf6',
  hypothesis: '#a855f7',
  intervention: '#6366f1'
}

function getNodeColor(type: string): string {
  const t = type.toLowerCase()
  for (const key of Object.keys(TYPE_COLORS)) {
    if (t.includes(key)) {
      const col = TYPE_COLORS[key]
      if (col) return col
    }
  }
  return '#94a3b8'
}

function getNodeDepth(node: GraphNode): number {
  const t = (node.type || '').toLowerCase()
  // Deterministic semantic depth layers:
  // z = -8: Profound Agents / Sources
  // z = -4: ChangeSets
  // z = 0: Targets / Claims / Root
  // z = +4: Conflicts / Dependencies
  // z = +8: Decision / Protected Experiment
  if (t.includes('agent') || t.includes('source')) return -8
  if (t.includes('changeset') || t.includes('proposal')) return -4
  if (t.includes('target') || t.includes('claim')) return 0
  if (t.includes('conflict') || t.includes('contradict') || t.includes('depend')) return 4
  if (t.includes('experiment') || t.includes('protect') || t.includes('decision') || t.includes('intervention')) return 8
  if (t.includes('incident') || node.id.includes('root') || (node.level === 0)) return 0
  if (t.includes('signal') || t.includes('prompt')) return -3
  if (t.includes('citation') || t.includes('external') || t.includes('competitor') || t.includes('owned')) return -7
  if (t.includes('hypothesis')) return 4
  return (node.level ?? 0) * 2 - 2
}

// Create a sprite canvas texture for readable node text in 3D
function createTextTexture(title: string, subtitle: string, colorHex: string): THREE.Texture {
  const canvas = document.createElement('canvas')
  canvas.width = 512
  canvas.height = 160
  const ctx = canvas.getContext('2d')
  if (ctx) {
    // Background pill
    ctx.fillStyle = 'rgba(15, 21, 38, 0.95)'
    ctx.strokeStyle = colorHex
    ctx.lineWidth = 6
    ctx.beginPath()
    ctx.roundRect(8, 8, 496, 144, 24)
    ctx.fill()
    ctx.stroke()

    // Title
    ctx.font = 'bold 32px sans-serif'
    ctx.fillStyle = '#ffffff'
    const displayTitle = title.length > 22 ? title.slice(0, 21) + '…' : title
    ctx.fillText(displayTitle, 28, 64)

    // Subtitle / Type
    ctx.font = '600 24px sans-serif'
    ctx.fillStyle = colorHex
    ctx.fillText(subtitle.toUpperCase(), 28, 114)
  }

  const texture = new THREE.CanvasTexture(canvas)
  texture.minFilter = THREE.LinearFilter
  texturesToDispose.push(texture)
  return texture
}

function buildGraphScene() {
  if (!scene) return

  // Clear existing objects
  nodeMeshes.forEach(mesh => scene?.remove(mesh))
  nodeMeshes.clear()
  edgeLines.forEach(line => scene?.remove(line))
  edgeLines.length = 0

  const nodes = props.graph.nodes
  const edges = props.graph.edges

  if (!nodes.length) return

  // Group nodes by depth layers
  const layerGroups = new Map<number, GraphNode[]>()
  nodes.forEach(n => {
    const depth = getNodeDepth(n)
    if (!layerGroups.has(depth)) layerGroups.set(depth, [])
    layerGroups.get(depth)!.push(n)
  })

  // Position nodes
  const nodePositions = new Map<string, THREE.Vector3>()
  const depths = Array.from(layerGroups.keys()).sort((a, b) => a - b)

  depths.forEach(d => {
    const group = layerGroups.get(d)!
    const count = group.length
    const spreadX = Math.max(14, count * 9)
    group.forEach((n, idx) => {
      const x = count === 1 ? 0 : (idx - (count - 1) / 2) * (spreadX / count)
      const y = (Math.sin(idx * 1.5) * 1.5) + (d === 0 ? 3 : d > 0 ? 1 : -1)
      const z = d
      nodePositions.set(n.id, new THREE.Vector3(x, y, z))
    })
  })

  // Build node 3D meshes
  nodes.forEach(node => {
    const pos = nodePositions.get(node.id) || new THREE.Vector3(0, 0, 0)
    const nodeGroup = new THREE.Group()
    nodeGroup.position.copy(pos)
    nodeGroup.userData = { nodeId: node.id, node }

    const colorHex = getNodeColor(node.type)
    const isSelected = props.selectedNodeId === node.id

    // Dimensional base box
    const boxGeo = new THREE.BoxGeometry(6.4, 2.0, 0.4)
    geometriesToDispose.push(boxGeo)
    const boxMat = new THREE.MeshStandardMaterial({
      color: new THREE.Color(colorHex),
      emissive: new THREE.Color(colorHex),
      emissiveIntensity: isSelected ? 0.6 : 0.15,
      roughness: 0.3,
      metalness: 0.2,
      transparent: true,
      opacity: 0.88
    })
    materialsToDispose.push(boxMat)
    const boxMesh = new THREE.Mesh(boxGeo, boxMat)
    nodeGroup.add(boxMesh)

    // Text sprite
    const texture = createTextTexture(node.title, node.type, colorHex)
    const spriteMat = new THREE.SpriteMaterial({ map: texture, transparent: true })
    materialsToDispose.push(spriteMat)
    const sprite = new THREE.Sprite(spriteMat)
    sprite.scale.set(6.2, 1.9, 1)
    sprite.position.set(0, 0, 0.25)
    nodeGroup.add(sprite)

    // Outer glow aura for selected node
    if (isSelected) {
      const auraGeo = new THREE.PlaneGeometry(7.2, 2.8)
      geometriesToDispose.push(auraGeo)
      const auraMat = new THREE.MeshBasicMaterial({
        color: new THREE.Color(colorHex),
        transparent: true,
        opacity: 0.35,
        blending: THREE.AdditiveBlending
      })
      materialsToDispose.push(auraMat)
      const aura = new THREE.Mesh(auraGeo, auraMat)
      aura.position.set(0, 0, -0.05)
      nodeGroup.add(aura)
    }

    // Protected Orbit ring for active experiment or protected target
    const isExperiment = node.type.toLowerCase().includes('experiment') || node.id.toLowerCase().includes('exp-') || node.type.toLowerCase().includes('protected')
    if (isExperiment) {
      const ringGeo = new THREE.TorusGeometry(4.4, 0.08, 16, 64)
      geometriesToDispose.push(ringGeo)
      const ringMat = new THREE.MeshBasicMaterial({
        color: new THREE.Color('#38bdf8'),
        transparent: true,
        opacity: 0.65,
        blending: THREE.AdditiveBlending
      })
      materialsToDispose.push(ringMat)
      const ringMesh = new THREE.Mesh(ringGeo, ringMat)
      ringMesh.rotation.x = Math.PI / 4
      ringMesh.userData = { isProtectedRing: true }
      nodeGroup.add(ringMesh)
    }

    scene!.add(nodeGroup)
    nodeMeshes.set(node.id, nodeGroup)
  })

  // Build 3D bezier curves for edges
  edges.forEach((edge: GraphEdge) => {
    const p1 = nodePositions.get(edge.src)
    const p2 = nodePositions.get(edge.dst)
    if (!p1 || !p2) return

    const mid = new THREE.Vector3().addVectors(p1, p2).multiplyScalar(0.5)
    mid.y += (p1.z !== p2.z) ? 1.5 : 0.8

    const curve = new THREE.QuadraticBezierCurve3(p1, mid, p2)
    const points = curve.getPoints(24)
    const lineGeo = new THREE.BufferGeometry().setFromPoints(points)
    geometriesToDispose.push(lineGeo)

    const isConflict = edge.edgeType === 'contradicts' || edge.conflict || edge.edgeType === 'conflict' || (edge as any).isConflict
    const isProtected = edge.edgeType === 'protects' || edge.edgeType === 'protected_by'
    const edgeColor = isConflict ? '#f43f5e' : isProtected ? '#38bdf8' : '#475569'

    const lineMat = new THREE.LineBasicMaterial({
      color: new THREE.Color(edgeColor),
      transparent: true,
      opacity: isConflict ? 0.9 : isProtected ? 0.75 : 0.45,
      linewidth: 2
    })
    materialsToDispose.push(lineMat)
    const line = new THREE.Line(lineGeo, lineMat)
    scene!.add(line)
    edgeLines.push(line)
  })
}

function initThree() {
  if (!canvasContainer.value || typeof window === 'undefined' || !canRender3d()) return

  const width = canvasContainer.value.clientWidth || 600
  const height = canvasContainer.value.clientHeight || 420

  scene = new THREE.Scene()
  scene.background = new THREE.Color('#070a13')

  camera = new THREE.PerspectiveCamera(45, width / height, 0.1, 1000)
  camera.position.copy(initialCameraPos)
  camera.lookAt(cameraTarget)

  try {
    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true })
    renderer.setSize(width, height)
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    canvasContainer.value.appendChild(renderer.domElement)
  } catch {
    // WebGL fallback is handled gracefully
    return
  }

  // Scene lighting
  const ambientLight = new THREE.AmbientLight(0xffffff, 0.9)
  scene.add(ambientLight)

  const dirLight = new THREE.DirectionalLight(0x818cf8, 1.2)
  dirLight.position.set(10, 20, 30)
  scene.add(dirLight)

  raycaster = new THREE.Raycaster()
  mouse = new THREE.Vector2()

  buildGraphScene()

  function animate() {
    animId = requestAnimationFrame(animate)

    // Smooth camera lerp
    if (camera) {
      camera.position.lerp(currentCameraPos, 0.08)
      camera.lookAt(cameraTarget)
    }

    // Slowly rotate protected orbit rings around protected targets / experiments
    const prefersReduced = typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches
    if (!prefersReduced) {
      nodeMeshes.forEach(group => {
        group.children.forEach(child => {
          if (child.userData?.isProtectedRing) {
            child.rotation.z += 0.006
          }
        })
      })
    }

    if (renderer && scene && camera) {
      renderer.render(scene, camera)
    }
  }

  animate()
  setupEventListeners()
}

function setupEventListeners() {
  const el = canvasContainer.value
  if (!el) return

  const onPointerDown = (e: MouseEvent) => {
    isDragging = true
    previousMousePosition = { x: e.clientX, y: e.clientY }
  }

  const onPointerMove = (e: MouseEvent) => {
    const rect = el.getBoundingClientRect()
    const mouseX = ((e.clientX - rect.left) / rect.width) * 2 - 1
    const mouseY = -((e.clientY - rect.top) / rect.height) * 2 + 1

    if (mouse) {
      mouse.x = mouseX
      mouse.y = mouseY
    }

    if (isDragging) {
      const deltaX = e.clientX - previousMousePosition.x
      const deltaY = e.clientY - previousMousePosition.y

      // Orbit camera slightly around target within calm boundaries
      const theta = -deltaX * 0.005
      const phi = deltaY * 0.005

      const offset = currentCameraPos.clone().sub(cameraTarget)
      const radius = offset.length()

      // Clamp vertical angle to avoid flipping
      offset.x = Math.sin(theta) * radius + offset.x
      offset.y = Math.max(-5, Math.min(25, offset.y + phi * 10))
      offset.normalize().multiplyScalar(radius)

      currentCameraPos.copy(cameraTarget).add(offset)
      previousMousePosition = { x: e.clientX, y: e.clientY }
    } else {
      // Raycasting for hover & tooltip
      if (raycaster && mouse && camera && scene) {
        raycaster.setFromCamera(mouse, camera)
        const intersects = raycaster.intersectObjects(Array.from(nodeMeshes.values()), true)
        const hit = intersects[0]
        if (hit) {
          let topGroup: THREE.Object3D | null = hit.object
          while (topGroup && !topGroup.userData.nodeId && topGroup.parent) {
            topGroup = topGroup.parent
          }
          if (topGroup?.userData?.node) {
            hoveredNode.value = topGroup.userData.node
            tooltipPos.value = { x: e.clientX - rect.left + 12, y: e.clientY - rect.top + 12 }
            el.style.cursor = 'pointer'
            return
          }
        }
        hoveredNode.value = null
        el.style.cursor = 'default'
      }
    }
  }

  const onPointerUp = (e: MouseEvent) => {
    if (!isDragging) return
    isDragging = false
  }

  const onClick = (e: MouseEvent) => {
    if (hoveredNode.value) {
      emit('selectNode', hoveredNode.value.id)
    }
  }

  const onWheel = (e: WheelEvent) => {
    e.preventDefault()
    const zoomFactor = e.deltaY * 0.03
    const offset = currentCameraPos.clone().sub(cameraTarget)
    const newLen = Math.max(16, Math.min(70, offset.length() + zoomFactor))
    offset.normalize().multiplyScalar(newLen)
    currentCameraPos.copy(cameraTarget).add(offset)
  }

  el.addEventListener('mousedown', onPointerDown)
  window.addEventListener('mousemove', onPointerMove)
  window.addEventListener('mouseup', onPointerUp)
  el.addEventListener('click', onClick)
  el.addEventListener('wheel', onWheel, { passive: false })

  onBeforeUnmount(() => {
    el.removeEventListener('mousedown', onPointerDown)
    window.removeEventListener('mousemove', onPointerMove)
    window.removeEventListener('mouseup', onPointerUp)
    el.removeEventListener('click', onClick)
    el.removeEventListener('wheel', onWheel)
  })
}

function resetView() {
  currentCameraPos.copy(initialCameraPos)
  cameraTarget.set(0, 0, 0)
}

watch(() => [props.graph, props.selectedNodeId], () => {
  buildGraphScene()
}, { deep: true })

onMounted(() => {
  initThree()
})

onBeforeUnmount(() => {
  if (animId) cancelAnimationFrame(animId)
  geometriesToDispose.forEach(g => g.dispose())
  materialsToDispose.forEach(m => m.dispose())
  texturesToDispose.forEach(t => t.dispose())
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
</script>

<template>
  <div class="evidence-scene-wrapper">
    <div ref="canvasContainer" class="three-container" aria-label="3D Change Topology Scene" />

    <!-- Accessible screen-reader textual topology fallback -->
    <div class="sr-only" role="region" aria-label="Change Topology Structure">
      <h4>Change Topology Nodes</h4>
      <ul>
        <li v-for="node in graph.nodes" :key="node.id">
          {{ node.type }}: {{ node.title }} (depth layer {{ getNodeDepth(node) }})
        </li>
      </ul>
    </div>

    <!-- Overlay controls -->
    <div class="scene-controls">
      <button class="control-btn" type="button" title="Reset Camera View" @click="resetView">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
          <path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" />
          <path d="M3 3v5h5" />
        </svg>
        <span>Reset</span>
      </button>
      <div class="depth-indicator">
        <span class="depth-pill">3D Depth</span>
      </div>
    </div>

    <!-- Interactive hover tooltip -->
    <div
      v-if="hoveredNode"
      class="graph-tooltip"
      :style="{ left: `${tooltipPos.x}px`, top: `${tooltipPos.y}px` }"
      role="tooltip"
    >
      <strong>{{ hoveredNode.title }}</strong>
      <span class="tooltip-type">{{ hoveredNode.type }}</span>
      <span v-if="hoveredNode.confidence != null" class="tooltip-meta">Confidence: {{ (hoveredNode.confidence * 100).toFixed(0) }}%</span>
    </div>
  </div>
</template>

<style scoped>
.evidence-scene-wrapper {
  position: relative;
  width: 100%;
  height: 400px;
  background: #070a13;
  border-radius: var(--radius);
  overflow: hidden;
  border: 1px solid var(--border);
}
.three-container {
  width: 100%;
  height: 100%;
}
.scene-controls {
  position: absolute;
  top: 10px;
  right: 10px;
  display: flex;
  align-items: center;
  gap: 8px;
  z-index: 2;
}
.control-btn {
  background: rgba(15, 21, 38, 0.85);
  backdrop-filter: blur(8px);
  border: 1px solid var(--border);
  color: var(--text-dim);
  font-size: 11px;
  padding: 4px 8px;
  border-radius: var(--radius-sm);
  display: flex;
  align-items: center;
  gap: 4px;
}
.control-btn:hover {
  color: var(--text-primary);
  border-color: var(--border-strong);
}
.depth-indicator .depth-pill {
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  padding: 3px 8px;
  border-radius: 4px;
  background: rgba(99, 102, 241, 0.15);
  border: 1px solid rgba(99, 102, 241, 0.4);
  color: #818cf8;
}
.graph-tooltip {
  position: absolute;
  pointer-events: none;
  background: rgba(10, 14, 26, 0.95);
  backdrop-filter: blur(10px);
  border: 1px solid var(--border-strong);
  border-radius: 6px;
  padding: 6px 10px;
  font-size: 11px;
  color: var(--text);
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.5);
  z-index: 10;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.tooltip-type {
  font-size: 10px;
  font-weight: 600;
  color: var(--blue);
  text-transform: uppercase;
}
.tooltip-meta {
  color: var(--text-dim);
  font-size: 10px;
}
</style>
