<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount, watch, shallowRef } from 'vue'
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
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
  (e: 'open-create-experiment', campaignId?: string): void
}>()

// Display & Navigation Mode
const viewMode = ref<'3d' | '2d'>('3d')
const activeFilter = ref<'ALL' | 'agent' | 'campaign' | 'decision' | 'experiment' | 'outcome' | 'signal'>('ALL')
const isFullscreen = ref(false)
const flowParticlesEnabled = ref(true)
const searchQuery = ref('')

// Container references
const graphContainerRef = ref<HTMLDivElement | null>(null)
const canvasContainerRef = ref<HTMLDivElement | null>(null)

// Selection & Hover State
const internalSelectedId = ref<string | null>(props.selectedNodeId)
const hoveredNode = ref<ControlPlaneGraphNode | null>(null)

watch(
  () => props.selectedNodeId,
  (val) => {
    internalSelectedId.value = val
    if (val) tracePathForNode(val)
  }
)

// Active Trace Set (IDs of nodes and edges in current highlighted path)
const tracedNodeIds = ref<Set<string>>(new Set())
const tracedEdgeIds = ref<Set<string>>(new Set())

// Computed selected node object
const selectedNode = computed(() => {
  if (!internalSelectedId.value) return null
  return props.nodes.find((n) => n.id === internalSelectedId.value) || null
})

// Connected Upstream & Downstream Nodes
const upstreamNodes = computed(() => {
  if (!internalSelectedId.value) return []
  const incoming = props.edges.filter((e) => e.target === internalSelectedId.value).map((e) => e.source)
  return props.nodes.filter((n) => incoming.includes(n.id))
})

const downstreamNodes = computed(() => {
  if (!internalSelectedId.value) return []
  const outgoing = props.edges.filter((e) => e.source === internalSelectedId.value).map((e) => e.target)
  return props.nodes.filter((n) => outgoing.includes(n.id))
})

// Filtering
const filteredNodes = computed(() => {
  let list = props.nodes
  if (activeFilter.value !== 'ALL') {
    list = list.filter((n) => n.type === activeFilter.value)
  }
  if (searchQuery.value.trim()) {
    const q = searchQuery.value.toLowerCase()
    list = list.filter((n) => n.label.toLowerCase().includes(q) || n.type.toLowerCase().includes(q))
  }
  return list
})

const filteredEdges = computed(() => {
  const visibleIds = new Set(filteredNodes.value.map((n) => n.id))
  return props.edges.filter((e) => visibleIds.has(e.source) && visibleIds.has(e.target))
})

// Trace Lineage Logic (Trace Upstream to Signals and Downstream to Outcomes)
function tracePathForNode(nodeId: string) {
  const nodeSet = new Set<string>([nodeId])
  const edgeSet = new Set<string>()

  // Trace Upstream (backward)
  const queueUp = [nodeId]
  while (queueUp.length > 0) {
    const curr = queueUp.shift()!
    for (const e of props.edges) {
      if (e.target === curr && !nodeSet.has(e.source)) {
        nodeSet.add(e.source)
        edgeSet.add(e.id)
        queueUp.push(e.source)
      } else if (e.target === curr) {
        edgeSet.add(e.id)
      }
    }
  }

  // Trace Downstream (forward)
  const queueDown = [nodeId]
  while (queueDown.length > 0) {
    const curr = queueDown.shift()!
    for (const e of props.edges) {
      if (e.source === curr && !nodeSet.has(e.target)) {
        nodeSet.add(e.target)
        edgeSet.add(e.id)
        queueDown.push(e.target)
      } else if (e.source === curr) {
        edgeSet.add(e.id)
      }
    }
  }

  tracedNodeIds.value = nodeSet
  tracedEdgeIds.value = edgeSet
}

function handleNodeClick(node: ControlPlaneGraphNode) {
  internalSelectedId.value = node.id
  tracePathForNode(node.id)
  emit('select', node)
  if (viewMode.value === '3d') {
    focusCameraOnNode(node)
  }
}

function handleClearSelection() {
  internalSelectedId.value = null
  tracedNodeIds.value.clear()
  tracedEdgeIds.value.clear()
  emit('clear-selection')
}

// ----------------------------------------------------------------------
// THREE.JS 3D SPATIAL ENGINE
// ----------------------------------------------------------------------
let scene: THREE.Scene | null = null
let camera: THREE.PerspectiveCamera | null = null
let renderer: THREE.WebGLRenderer | null = null
let controls: OrbitControls | null = null
let animFrameId: number | null = null
let raycaster: THREE.Raycaster | null = null
let mouse: THREE.Vector2 | null = null

// Node floating state registry
interface NodeMeshRef {
  mesh: THREE.Object3D
  node: ControlPlaneGraphNode
  basePos: THREE.Vector3
  phase: number
  speed: number
  amplitude: number
}

const nodeMeshRefs: NodeMeshRef[] = []
const nodeMeshMap = new Map<string, THREE.Object3D>()
const edgeLines: { line: THREE.Line; edge: ControlPlaneGraphEdge }[] = []
let particlesSystem: THREE.Points | null = null

// Semantic colors
const PALETTE: Record<string, number> = {
  campaign: 0x6366f1,    // indigo
  agent: 0x38bdf8,       // cyan
  decision: 0xa855f7,    // violet
  experiment: 0x06b6d4,  // teal
  outcome: 0x10b981,     // emerald
  signal: 0xf43f5e,      // rose
  cost: 0xf59e0b,        // amber
  revenue: 0x10b981,     // emerald
  reward: 0x22d3ee,      // bright cyan
  laya_decision: 0xa855f7 // purple pulse
}

function init3D() {
  if (!canvasContainerRef.value || typeof window === 'undefined') return

  const width = canvasContainerRef.value.clientWidth || 900
  const height = canvasContainerRef.value.clientHeight || 560

  // 1. Scene
  scene = new THREE.Scene()
  scene.fog = new THREE.FogExp2(0x060911, 0.018)

  // 2. Camera
  camera = new THREE.PerspectiveCamera(45, width / height, 0.1, 1000)
  camera.position.set(0, 7, 26)
  camera.lookAt(0, 0, 0)

  // 3. Renderer
  try {
    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: 'high-performance' })
  } catch {
    viewMode.value = '2d'
    return
  }

  renderer.setSize(width, height)
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
  canvasContainerRef.value.appendChild(renderer.domElement)

  // 4. OrbitControls
  controls = new OrbitControls(camera, renderer.domElement)
  controls.enableDamping = true
  controls.dampingFactor = 0.06
  controls.maxDistance = 50
  controls.minDistance = 6
  controls.maxPolarAngle = Math.PI / 2 + 0.15

  // 5. Lights
  const ambient = new THREE.AmbientLight(0xffffff, 0.8)
  scene.add(ambient)

  const dirLight = new THREE.DirectionalLight(0x38bdf8, 1.4)
  dirLight.position.set(12, 24, 18)
  scene.add(dirLight)

  const pointPurple = new THREE.PointLight(0xa855f7, 2.0, 50)
  pointPurple.position.set(-10, -6, -6)
  scene.add(pointPurple)

  const pointEmerald = new THREE.PointLight(0x10b981, 1.5, 40)
  pointEmerald.position.set(12, 2, 8)
  scene.add(pointEmerald)

  // 6. Build Graph Meshes
  rebuild3DGraph()

  // 7. Raycaster & Interactivity
  raycaster = new THREE.Raycaster()
  mouse = new THREE.Vector2()

  renderer.domElement.addEventListener('mousemove', on3DMouseMove)
  renderer.domElement.addEventListener('click', on3DClick)
  window.addEventListener('resize', on3DResize)

  // 8. Animation loop
  const clock = new THREE.Clock()

  const animate = () => {
    animFrameId = requestAnimationFrame(animate)
    const elapsed = clock.getElapsedTime()

    // Smooth floating physics for nodes
    for (const item of nodeMeshRefs) {
      const floatY = Math.sin(elapsed * item.speed + item.phase) * item.amplitude
      const floatX = Math.cos(elapsed * item.speed * 0.7 + item.phase) * (item.amplitude * 0.4)
      item.mesh.position.y = item.basePos.y + floatY
      item.mesh.position.x = item.basePos.x + floatX

      // Subtle rotation for special node types
      if (item.node.type === 'campaign' || item.node.type === 'decision' || item.node.type === 'laya_decision') {
        item.mesh.rotation.y = elapsed * 0.25 + item.phase
      }
    }

    // Update edge line endpoints to track floating node meshes
    for (const { line, edge } of edgeLines) {
      const srcMesh = nodeMeshMap.get(edge.source)
      const tgtMesh = nodeMeshMap.get(edge.target)
      if (srcMesh && tgtMesh) {
        const positions = line.geometry.attributes.position.array as Float32Array
        positions[0] = srcMesh.position.x
        positions[1] = srcMesh.position.y
        positions[2] = srcMesh.position.z
        positions[3] = tgtMesh.position.x
        positions[4] = tgtMesh.position.y
        positions[5] = tgtMesh.position.z
        line.geometry.attributes.position.needsUpdate = true
      }
    }

    // Animate flow particles along active / traced edges
    if (particlesSystem && flowParticlesEnabled.value) {
      const pPos = particlesSystem.geometry.attributes.position.array as Float32Array
      const pProgress = (particlesSystem.geometry as any).userData?.progress as Float32Array
      const pEdgeIndices = (particlesSystem.geometry as any).userData?.edgeIndices as Int16Array

      if (pProgress && pEdgeIndices) {
        for (let i = 0; i < pProgress.length; i++) {
          pProgress[i] = (pProgress[i] + 0.007) % 1.0
          const edgeIdx = pEdgeIndices[i]
          if (edgeIdx >= 0 && edgeIdx < edgeLines.length) {
            const { edge } = edgeLines[edgeIdx]
            const s = nodeMeshMap.get(edge.source)
            const t = nodeMeshMap.get(edge.target)
            if (s && t) {
              const u = pProgress[i]
              pPos[i * 3] = s.position.x + (t.position.x - s.position.x) * u
              pPos[i * 3 + 1] = s.position.y + (t.position.y - s.position.y) * u
              pPos[i * 3 + 2] = s.position.z + (t.position.z - s.position.z) * u
            }
          }
        }
        particlesSystem.geometry.attributes.position.needsUpdate = true
      }
    }

    if (controls) controls.update()
    if (renderer && scene && camera) renderer.render(scene, camera)
  }

  animate()
}

function rebuild3DGraph() {
  if (!scene) return

  // 1. Clear existing node meshes
  for (const item of nodeMeshRefs) {
    scene.remove(item.mesh)
    item.mesh.traverse((obj: any) => {
      if (obj.geometry) obj.geometry.dispose()
      if (obj.material) {
        if (Array.isArray(obj.material)) obj.material.forEach((m: any) => m.dispose())
        else obj.material.dispose()
      }
    })
  }
  nodeMeshRefs.length = 0
  nodeMeshMap.clear()

  // 2. Clear edge lines
  for (const { line } of edgeLines) {
    scene.remove(line)
    line.geometry.dispose()
    ;(line.material as THREE.Material).dispose()
  }
  edgeLines.length = 0

  // 3. Clear particles
  if (particlesSystem) {
    scene.remove(particlesSystem)
    particlesSystem.geometry.dispose()
    ;(particlesSystem.material as THREE.Material).dispose()
    particlesSystem = null
  }

  const isTracing = tracedNodeIds.value.size > 0

  // 4. Create Node Meshes
  for (let i = 0; i < filteredNodes.value.length; i++) {
    const node = filteredNodes.value[i]
    const isSelected = internalSelectedId.value === node.id
    const isTraced = tracedNodeIds.value.has(node.id)
    const isDimmed = isTracing && !isTraced

    const color = PALETTE[node.type] || 0x64748b
    const group = new THREE.Group()

    // Distinct Geometry by Type
    if (node.type === 'campaign') {
      // Anchored central sphere with orbiting halo ring
      const coreGeom = new THREE.SphereGeometry(1.25, 24, 24)
      const coreMat = new THREE.MeshStandardMaterial({
        color: node.status === 'positive' ? 0x10b981 : (node.status === 'negative' ? 0xef4444 : color),
        roughness: 0.2,
        metalness: 0.7,
        emissive: isSelected ? 0x6366f1 : 0x1e1b4b,
        emissiveIntensity: isSelected ? 0.7 : 0.2,
        opacity: isDimmed ? 0.2 : 1.0,
        transparent: isDimmed,
      })
      const coreMesh = new THREE.Mesh(coreGeom, coreMat)
      group.add(coreMesh)

      // Ring
      const ringGeom = new THREE.TorusGeometry(1.9, 0.08, 16, 48)
      const ringMat = new THREE.MeshBasicMaterial({
        color: node.status === 'positive' ? 0x34d399 : 0x818cf8,
        transparent: true,
        opacity: isDimmed ? 0.15 : 0.75,
      })
      const ringMesh = new THREE.Mesh(ringGeom, ringMat)
      ringMesh.rotation.x = Math.PI / 2.3
      group.add(ringMesh)
    } else if (node.type === 'agent') {
      // Floating core orb with cyan glow
      const geom = new THREE.SphereGeometry(0.85, 20, 20)
      const mat = new THREE.MeshStandardMaterial({
        color,
        roughness: 0.15,
        metalness: 0.8,
        emissive: node.status === 'running' ? 0x0284c7 : 0x000000,
        emissiveIntensity: node.status === 'running' ? 0.5 : 0.1,
        opacity: isDimmed ? 0.18 : 1.0,
        transparent: isDimmed,
      })
      group.add(new THREE.Mesh(geom, mat))
    } else if (node.type === 'decision' || node.type === 'laya_decision') {
      // Diamond / Octahedron
      const geom = new THREE.OctahedronGeometry(node.type === 'laya_decision' ? 0.95 : 0.8)
      const mat = new THREE.MeshStandardMaterial({
        color: node.type === 'laya_decision' ? 0xa855f7 : color,
        roughness: 0.1,
        metalness: 0.6,
        emissive: node.type === 'laya_decision' ? 0x7c3aed : 0x000000,
        emissiveIntensity: 0.4,
        opacity: isDimmed ? 0.18 : 1.0,
        transparent: isDimmed,
      })
      group.add(new THREE.Mesh(geom, mat))
    } else if (node.type === 'experiment') {
      // Box with wireframe highlight
      const geom = new THREE.BoxGeometry(0.9, 0.9, 0.9)
      const mat = new THREE.MeshStandardMaterial({
        color,
        roughness: 0.3,
        metalness: 0.5,
        emissive: node.status === 'running' ? 0x0284c7 : 0x000000,
        emissiveIntensity: 0.3,
        opacity: isDimmed ? 0.18 : 1.0,
        transparent: isDimmed,
      })
      group.add(new THREE.Mesh(geom, mat))
    } else if (node.type === 'outcome' || node.type === 'reward') {
      // Forward-facing glowing emerald / cyan sphere
      const geom = new THREE.DodecahedronGeometry(0.95)
      const mat = new THREE.MeshStandardMaterial({
        color,
        roughness: 0.2,
        metalness: 0.6,
        emissive: color,
        emissiveIntensity: 0.35,
        opacity: isDimmed ? 0.2 : 1.0,
        transparent: isDimmed,
      })
      group.add(new THREE.Mesh(geom, mat))
    } else {
      // Cost, Signal, Asset
      const geom = new THREE.TetrahedronGeometry(0.75)
      const mat = new THREE.MeshStandardMaterial({
        color,
        roughness: 0.2,
        metalness: 0.5,
        opacity: isDimmed ? 0.18 : 1.0,
        transparent: isDimmed,
      })
      group.add(new THREE.Mesh(geom, mat))
    }

    // Set position and metadata
    group.position.set(node.x, node.y, node.z)
    group.userData = { node }

    scene.add(group)
    nodeMeshMap.set(node.id, group)

    nodeMeshRefs.push({
      mesh: group,
      node,
      basePos: new THREE.Vector3(node.x, node.y, node.z),
      phase: i * 0.65,
      speed: 0.8 + (i % 5) * 0.15,
      amplitude: 0.10 + (i % 4) * 0.02,
    })
  }

  // 5. Create Edges
  for (const edge of filteredEdges.value) {
    const srcGroup = nodeMeshMap.get(edge.source)
    const tgtGroup = nodeMeshMap.get(edge.target)
    if (!srcGroup || !tgtGroup) continue

    const points = [srcGroup.position.clone(), tgtGroup.position.clone()]
    const lineGeom = new THREE.BufferGeometry().setFromPoints(points)

    const isTracedEdge = tracedEdgeIds.value.has(edge.id)
    const isDimmedEdge = isTracing && !isTracedEdge

    const edgeColor = isTracedEdge
      ? 0x38bdf8
      : (edge.status === 'positive'
          ? 0x10b981
          : (edge.status === 'negative'
              ? 0xef4444
              : (edge.status === 'active' ? 0x38bdf8 : 0x475569)))

    const lineMat = new THREE.LineBasicMaterial({
      color: edgeColor,
      transparent: true,
      opacity: isDimmedEdge ? 0.08 : (isTracedEdge ? 0.95 : 0.45),
      linewidth: isTracedEdge ? 3 : 1.5,
    })

    const line = new THREE.Line(lineGeom, lineMat)
    scene.add(line)
    edgeLines.push({ line, edge })
  }

  // 6. Create GPU Flow Particles for Active/Traced Edges
  const activeEdgeIndices: number[] = []
  edgeLines.forEach((item, idx) => {
    if (tracedEdgeIds.value.has(item.edge.id) || item.edge.status === 'active' || item.edge.status === 'positive') {
      activeEdgeIndices.push(idx)
    }
  })

  if (activeEdgeIndices.length > 0) {
    const particlesPerEdge = 4
    const totalCount = activeEdgeIndices.length * particlesPerEdge
    const pPositions = new Float32Array(totalCount * 3)
    const pProgress = new Float32Array(totalCount)
    const pEdgeMap = new Int16Array(totalCount)

    let idx = 0
    for (const edgeIdx of activeEdgeIndices) {
      for (let p = 0; p < particlesPerEdge; p++) {
        pProgress[idx] = p / particlesPerEdge
        pEdgeMap[idx] = edgeIdx
        idx++
      }
    }

    const pGeom = new THREE.BufferGeometry()
    pGeom.setAttribute('position', new THREE.BufferAttribute(pPositions, 3))
    ;(pGeom as any).userData = { progress: pProgress, edgeIndices: pEdgeMap }

    const pMat = new THREE.PointsMaterial({
      color: 0x38bdf8,
      size: 0.28,
      transparent: true,
      opacity: 0.9,
      blending: THREE.AdditiveBlending,
    })

    particlesSystem = new THREE.Points(pGeom, pMat)
    scene.add(particlesSystem)
  }
}

function on3DMouseMove(e: MouseEvent) {
  if (!canvasContainerRef.value || !raycaster || !camera) return
  const rect = canvasContainerRef.value.getBoundingClientRect()
  mouse.x = ((e.clientX - rect.left) / rect.width) * 2 - 1
  mouse.y = -((e.clientY - rect.top) / rect.height) * 2 + 1

  raycaster.setFromCamera(mouse, camera)
  const intersects = raycaster.intersectObjects(nodeMeshRefs.map((r) => r.mesh), true)

  if (intersects.length > 0) {
    let topGroup: THREE.Object3D | null = intersects[0].object
    while (topGroup && !topGroup.userData?.node && topGroup.parent) {
      topGroup = topGroup.parent
    }
    if (topGroup?.userData?.node) {
      hoveredNode.value = topGroup.userData.node
      if (canvasContainerRef.value) canvasContainerRef.value.style.cursor = 'pointer'
      return
    }
  }

  hoveredNode.value = null
  if (canvasContainerRef.value) canvasContainerRef.value.style.cursor = 'default'
}

function on3DClick(e: MouseEvent) {
  if (!canvasContainerRef.value || !raycaster || !camera) return
  const rect = canvasContainerRef.value.getBoundingClientRect()
  mouse.x = ((e.clientX - rect.left) / rect.width) * 2 - 1
  mouse.y = -((e.clientY - rect.top) / rect.height) * 2 + 1

  raycaster.setFromCamera(mouse, camera)
  const intersects = raycaster.intersectObjects(nodeMeshRefs.map((r) => r.mesh), true)

  if (intersects.length > 0) {
    let topGroup: THREE.Object3D | null = intersects[0].object
    while (topGroup && !topGroup.userData?.node && topGroup.parent) {
      topGroup = topGroup.parent
    }
    if (topGroup?.userData?.node) {
      handleNodeClick(topGroup.userData.node)
      return
    }
  }

  handleClearSelection()
}

function on3DResize() {
  if (!canvasContainerRef.value || !renderer || !camera) return
  const w = canvasContainerRef.value.clientWidth || 900
  const h = canvasContainerRef.value.clientHeight || 560
  camera.aspect = w / h
  camera.updateProjectionMatrix()
  renderer.setSize(w, h)
}

function focusCameraOnNode(node: ControlPlaneGraphNode) {
  if (!camera || !controls) return
  // Smoothly move controls target toward node
  controls.target.set(node.x, node.y, node.z)
  camera.position.set(node.x + 2, node.y + 4, node.z + 14)
}

function resetCamera() {
  if (!camera || !controls) return
  controls.target.set(0, 0, 0)
  camera.position.set(0, 7, 26)
  handleClearSelection()
}

// Watch filters or selections to rebuild meshes
watch(
  () => [props.nodes, props.edges, internalSelectedId.value, activeFilter.value, searchQuery.value],
  () => {
    if (viewMode.value === '3d') {
      rebuild3DGraph()
    }
  },
  { deep: true }
)

watch(viewMode, (newVal) => {
  if (newVal === '3d') {
    setTimeout(() => {
      on3DResize()
      rebuild3DGraph()
    }, 50)
  }
})

// Lifecycle
onMounted(() => {
  init3D()
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', on3DResize)
  if (animFrameId) cancelAnimationFrame(animFrameId)
  if (renderer && renderer.domElement && renderer.domElement.parentNode) {
    renderer.domElement.removeEventListener('mousemove', on3DMouseMove)
    renderer.domElement.removeEventListener('click', on3DClick)
    renderer.domElement.parentNode.removeChild(renderer.domElement)
    renderer.dispose()
  }
  scene = null
  camera = null
  controls = null
})
</script>

<template>
  <div
    ref="graphContainerRef"
    :class="['control-plane-graph', { fullscreen: isFullscreen }]"
    data-testid="control-plane-graph"
  >
    <!-- TOP TOOLBAR OVER GRAPH -->
    <div class="graph-toolbar">
      <div class="toolbar-left">
        <!-- View Mode Switcher -->
        <div class="view-mode-toggle" role="radiogroup" aria-label="Graph View Mode">
          <button
            type="button"
            :class="['mode-btn', { active: viewMode === '3d' }]"
            @click="viewMode = '3d'"
          >
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z" />
            </svg>
            <span>3D Spatial</span>
          </button>
          <button
            type="button"
            :class="['mode-btn', { active: viewMode === '2d' }]"
            @click="viewMode = '2d'"
          >
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <rect x="3" y="3" width="18" height="18" rx="2" ry="2" />
              <line x1="3" y1="9" x2="21" y2="9" />
              <line x1="9" y1="21" x2="9" y2="9" />
            </svg>
            <span>2D Knowledge</span>
          </button>
        </div>

        <!-- Layer Filters -->
        <div class="layer-filter-pills" role="radiogroup" aria-label="Layer Filters">
          <button
            type="button"
            :class="['layer-pill', { active: activeFilter === 'ALL' }]"
            @click="activeFilter = 'ALL'"
          >
            All ({{ nodes.length }})
          </button>
          <button
            type="button"
            :class="['layer-pill', { active: activeFilter === 'agent' }]"
            @click="activeFilter = 'agent'"
          >
            Agents
          </button>
          <button
            type="button"
            :class="['layer-pill', { active: activeFilter === 'campaign' }]"
            @click="activeFilter = 'campaign'"
          >
            Campaigns
          </button>
          <button
            type="button"
            :class="['layer-pill', { active: activeFilter === 'decision' }]"
            @click="activeFilter = 'decision'"
          >
            Decisions
          </button>
          <button
            type="button"
            :class="['layer-pill', { active: activeFilter === 'experiment' }]"
            @click="activeFilter = 'experiment'"
          >
            Experiments
          </button>
          <button
            type="button"
            :class="['layer-pill', { active: activeFilter === 'outcome' }]"
            @click="activeFilter = 'outcome'"
          >
            Outcomes
          </button>
        </div>
      </div>

      <div class="toolbar-right">
        <!-- Live Path Indicator -->
        <div v-if="tracedNodeIds.size > 0" class="active-trace-badge">
          <span class="trace-pulse" />
          <span>Tracing {{ tracedNodeIds.size }} Lineage Nodes</span>
          <button type="button" class="btn-clear-trace" @click="handleClearSelection">✕</button>
        </div>

        <!-- Camera / Utility Controls -->
        <button
          type="button"
          class="tool-btn"
          title="Reset Camera & Selection"
          @click="resetCamera"
        >
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" />
            <path d="M3 3v5h5" />
          </svg>
          <span>Reset</span>
        </button>

        <button
          type="button"
          :class="['tool-btn', { active: flowParticlesEnabled }]"
          title="Toggle Edge Flow Particles"
          @click="flowParticlesEnabled = !flowParticlesEnabled"
        >
          <span class="particle-indicator" />
          <span>Flow</span>
        </button>

        <button
          type="button"
          class="tool-btn"
          :title="isFullscreen ? 'Exit Fullscreen' : 'Expand Graph'"
          @click="isFullscreen = !isFullscreen"
        >
          <svg v-if="!isFullscreen" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <polyline points="15 3 21 3 21 9" />
            <polyline points="9 21 3 21 3 15" />
            <line x1="21" y1="3" x2="14" y2="10" />
            <line x1="3" y1="21" x2="10" y2="14" />
          </svg>
          <svg v-else width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <polyline points="4 14 10 14 10 20" />
            <polyline points="20 10 14 10 14 4" />
            <line x1="14" y1="10" x2="21" y2="3" />
            <line x1="3" y1="21" x2="10" y2="14" />
          </svg>
        </button>
      </div>
    </div>

    <!-- MAIN GRAPH WORKSPACE -->
    <div class="graph-workspace-area">
      <!-- 3D VIEWPORT -->
      <div
        v-show="viewMode === '3d'"
        ref="canvasContainerRef"
        class="spatial-canvas-container"
      >
        <!-- Overlay HUD: Node Hover Tooltip -->
        <div v-if="hoveredNode && !selectedNode" class="hud-hover-tooltip">
          <div class="hover-type-badge">{{ hoveredNode.type.toUpperCase() }}</div>
          <div class="hover-label">{{ hoveredNode.label }}</div>
          <div class="hover-sub">Click to trace complete provenance</div>
        </div>

        <!-- Semantic Spatial Legend -->
        <div class="spatial-legend">
          <span class="legend-item"><span class="legend-dot bg-indigo" /> Campaign</span>
          <span class="legend-item"><span class="legend-dot bg-cyan" /> Agent</span>
          <span class="legend-item"><span class="legend-dot bg-purple" /> Decision / Laya</span>
          <span class="legend-item"><span class="legend-dot bg-teal" /> Experiment</span>
          <span class="legend-item"><span class="legend-dot bg-emerald" /> Outcome</span>
        </div>
      </div>

      <!-- 2D KNOWLEDGE GRAPH VIEWPORT -->
      <div v-if="viewMode === '2d'" class="knowledge-2d-container">
        <div class="dag-columns">
          <!-- Col 1: Signals -->
          <div class="dag-column">
            <div class="column-header">1. Signals & Demand</div>
            <div class="column-nodes">
              <div
                v-for="n in filteredNodes.filter(x => x.type === 'signal')"
                :key="n.id"
                :class="['dag-node-card', { selected: selectedNode?.id === n.id, traced: tracedNodeIds.has(n.id) }]"
                @click="handleNodeClick(n)"
              >
                <div class="node-card-top">
                  <span class="node-type-tag tag-signal">SIGNAL</span>
                  <span class="node-status-dot dot-running" />
                </div>
                <div class="node-card-title">{{ n.label }}</div>
                <div class="node-card-sub">{{ (n.meta as any)?.source || 'AI Discovery' }}</div>
              </div>
            </div>
          </div>

          <!-- Col 2: Agents -->
          <div class="dag-column">
            <div class="column-header">2. Autonomous Agents</div>
            <div class="column-nodes">
              <div
                v-for="n in filteredNodes.filter(x => x.type === 'agent')"
                :key="n.id"
                :class="['dag-node-card', { selected: selectedNode?.id === n.id, traced: tracedNodeIds.has(n.id) }]"
                @click="handleNodeClick(n)"
              >
                <div class="node-card-top">
                  <span class="node-type-tag tag-agent">AGENT</span>
                  <span :class="['node-status-dot', n.status === 'running' ? 'dot-running' : 'dot-neutral']" />
                </div>
                <div class="node-card-title">{{ n.label }}</div>
                <div class="node-card-meta">
                  <span>${{ (n.meta as any)?.cost || 0 }} cost</span>
                  <span>{{ (n.meta as any)?.runs || 0 }} runs</span>
                </div>
              </div>
            </div>
          </div>

          <!-- Col 3: Decisions & Laya -->
          <div class="dag-column">
            <div class="column-header">3. Decisions & Laya</div>
            <div class="column-nodes">
              <div
                v-for="n in filteredNodes.filter(x => x.type === 'decision' || x.type === 'laya_decision')"
                :key="n.id"
                :class="['dag-node-card', { selected: selectedNode?.id === n.id, traced: tracedNodeIds.has(n.id) }]"
                @click="handleNodeClick(n)"
              >
                <div class="node-card-top">
                  <span class="node-type-tag tag-decision">{{ n.type === 'laya_decision' ? 'LAYA PRIOR' : 'DECISION' }}</span>
                  <span class="node-status-dot dot-purple" />
                </div>
                <div class="node-card-title">{{ n.label }}</div>
                <div class="node-card-sub">
                  {{ (n.meta as any)?.choice ? `Laya Choice: ${(n.meta as any).choice}` : ((n.meta as any)?.status || 'Active') }}
                </div>
              </div>
            </div>
          </div>

          <!-- Col 4: Campaigns -->
          <div class="dag-column">
            <div class="column-header">4. Campaigns (Hubs)</div>
            <div class="column-nodes">
              <div
                v-for="n in filteredNodes.filter(x => x.type === 'campaign')"
                :key="n.id"
                :class="['dag-node-card', { selected: selectedNode?.id === n.id, traced: tracedNodeIds.has(n.id) }]"
                @click="handleNodeClick(n)"
              >
                <div class="node-card-top">
                  <span class="node-type-tag tag-campaign">CAMPAIGN</span>
                  <span :class="['node-status-dot', n.status === 'positive' ? 'dot-positive' : 'dot-neutral']" />
                </div>
                <div class="node-card-title">{{ n.label }}</div>
                <div class="node-card-meta">
                  <span class="tone-emerald">+${{ (n.meta as any)?.return?.toLocaleString() || 0 }}</span>
                  <span>${{ (n.meta as any)?.cost?.toLocaleString() || 0 }} cost</span>
                </div>
              </div>
            </div>
          </div>

          <!-- Col 5: Experiments -->
          <div class="dag-column">
            <div class="column-header">5. Experiments</div>
            <div class="column-nodes">
              <div
                v-for="n in filteredNodes.filter(x => x.type === 'experiment')"
                :key="n.id"
                :class="['dag-node-card', { selected: selectedNode?.id === n.id, traced: tracedNodeIds.has(n.id) }]"
                @click="handleNodeClick(n)"
              >
                <div class="node-card-top">
                  <span class="node-type-tag tag-experiment">EXPERIMENT</span>
                  <span :class="['node-status-dot', n.status === 'running' ? 'dot-running' : 'dot-neutral']" />
                </div>
                <div class="node-card-title">{{ n.label }}</div>
                <div class="node-card-sub">{{ (n.meta as any)?.metric || 'Metric Verification' }}</div>
              </div>
            </div>
          </div>

          <!-- Col 6: Outcomes & Rewards -->
          <div class="dag-column">
            <div class="column-header">6. Outcomes & Learning</div>
            <div class="column-nodes">
              <div
                v-for="n in filteredNodes.filter(x => x.type === 'outcome' || x.type === 'reward')"
                :key="n.id"
                :class="['dag-node-card', { selected: selectedNode?.id === n.id, traced: tracedNodeIds.has(n.id) }]"
                @click="handleNodeClick(n)"
              >
                <div class="node-card-top">
                  <span class="node-type-tag tag-outcome">{{ n.type.toUpperCase() }}</span>
                  <span class="node-status-dot dot-positive" />
                </div>
                <div class="node-card-title">{{ n.label }}</div>
                <div class="node-card-sub">{{ (n.meta as any)?.confidence || 'Verified Causal' }}</div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- DOM INSPECTOR PANEL (SLIDES OVER GRAPH RIGHT SIDE) -->
      <aside v-if="selectedNode" class="graph-inspector-panel">
        <header class="inspector-header">
          <div class="inspector-type-row">
            <span :class="['type-pill', `type-${selectedNode.type}`]">{{ selectedNode.type.toUpperCase() }}</span>
            <span :class="['status-badge', `status-${selectedNode.status}`]">{{ selectedNode.status }}</span>
          </div>
          <button type="button" class="btn-inspector-close" @click="handleClearSelection">✕</button>
        </header>

        <h3 class="inspector-title">{{ selectedNode.label }}</h3>
        <p v-if="(selectedNode.meta as any)?.role || (selectedNode.meta as any)?.hypothesis" class="inspector-desc">
          {{ (selectedNode.meta as any)?.role || (selectedNode.meta as any)?.hypothesis }}
        </p>

        <!-- LAYA EVALUATION SECTION (IF DECISION OR LAYA NODE) -->
        <div v-if="(selectedNode.meta as any)?.laya_distribution || selectedNode.type === 'laya_decision'" class="inspector-section laya-card">
          <div class="section-title">
            <span class="laya-icon">⚡</span>
            <span>Laya Typed Decision Prior</span>
            <span class="shadow-tag">SHADOW</span>
          </div>

          <div class="laya-prob-distribution">
            <div
              v-for="(prob, opt) in ((selectedNode.meta as any)?.laya_distribution || (selectedNode.meta as any)?.distribution || {})"
              :key="opt"
              class="dist-row"
            >
              <span class="dist-label">{{ opt }}</span>
              <div class="dist-bar-track">
                <div class="dist-bar-fill" :style="{ width: `${Number(prob) * 100}%` }" />
              </div>
              <span class="dist-val">{{ (Number(prob) * 100).toFixed(0) }}%</span>
            </div>
          </div>

          <div class="laya-meta-footer">
            <span class="laya-model-tag">Model: convaiinnovations/laya-typed-decisions</span>
            <span class="laya-conf-tag">Calibrated Conf: {{ ((selectedNode.meta as any)?.laya_confidence || (selectedNode.meta as any)?.calibrated_confidence || 0.74) * 100 }}%</span>
          </div>
        </div>

        <!-- FINANCIAL & OPERATIONAL METRICS -->
        <div v-if="(selectedNode.meta as any)?.cost !== undefined || (selectedNode.meta as any)?.return !== undefined" class="inspector-section">
          <div class="section-title">Financial Provenance</div>
          <div class="metrics-grid">
            <div v-if="(selectedNode.meta as any)?.cost !== undefined" class="m-card">
              <span class="m-label">Total Cost</span>
              <span class="m-val">${{ Number((selectedNode.meta as any)?.cost).toLocaleString() }}</span>
            </div>
            <div v-if="(selectedNode.meta as any)?.return !== undefined" class="m-card">
              <span class="m-label">Attributed Return</span>
              <span class="m-val tone-emerald">${{ Number((selectedNode.meta as any)?.return).toLocaleString() }}</span>
            </div>
            <div v-if="(selectedNode.meta as any)?.roi !== undefined" class="m-card">
              <span class="m-label">Calculated ROI</span>
              <span class="m-val tone-cyan">{{ Number((selectedNode.meta as any)?.roi).toFixed(0) }}%</span>
            </div>
          </div>
        </div>

        <!-- UPSTREAM LINEAGE -->
        <div v-if="upstreamNodes.length > 0" class="inspector-section">
          <div class="section-title">Upstream Predecessors ({{ upstreamNodes.length }})</div>
          <div class="lineage-chips">
            <button
              v-for="un in upstreamNodes"
              :key="un.id"
              type="button"
              class="lineage-chip"
              @click="handleNodeClick(un)"
            >
              <span class="chip-type">{{ un.type }}</span>
              <span class="chip-name">{{ un.label }}</span>
            </button>
          </div>
        </div>

        <!-- DOWNSTREAM IMPACT -->
        <div v-if="downstreamNodes.length > 0" class="inspector-section">
          <div class="section-title">Downstream Consequences ({{ downstreamNodes.length }})</div>
          <div class="lineage-chips">
            <button
              v-for="dn in downstreamNodes"
              :key="dn.id"
              type="button"
              class="lineage-chip"
              @click="handleNodeClick(dn)"
            >
              <span class="chip-type">{{ dn.type }}</span>
              <span class="chip-name">{{ dn.label }}</span>
            </button>
          </div>
        </div>

        <!-- CONTEXTUAL OPERATIONAL ACTIONS -->
        <footer class="inspector-actions">
          <button
            v-if="selectedNode.type === 'campaign'"
            type="button"
            class="btn-act primary"
            @click="emit('open-create-experiment', selectedNode.id)"
          >
            + Add Experiment to Campaign
          </button>
          <button
            v-if="selectedNode.type === 'decision' && (selectedNode.meta as any)?.status === 'PENDING_REVIEW'"
            type="button"
            class="btn-act success"
          >
            ✓ Approve Action
          </button>
          <button
            type="button"
            class="btn-act secondary"
            @click="handleClearSelection"
          >
            Clear Selection
          </button>
        </footer>
      </aside>
    </div>
  </div>
</template>

<style scoped>
.control-plane-graph {
  display: flex;
  flex-direction: column;
  background: radial-gradient(circle at 50% 30%, #0d1424 0%, #060911 100%);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg, 14px);
  overflow: hidden;
  position: relative;
  height: calc(100vh - 250px);
  min-height: 560px;
  box-shadow: 0 12px 36px rgba(0, 0, 0, 0.45);
}

.control-plane-graph.fullscreen {
  position: fixed;
  inset: var(--topbar-height, 56px) 16px 16px var(--nav-width, 240px);
  height: auto;
  z-index: var(--z-modal, 45);
}

@media (max-width: 1024px) {
  .control-plane-graph.fullscreen {
    left: 16px;
  }
}

/* TOOLBAR */
.graph-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 16px;
  background: rgba(10, 14, 26, 0.85);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  border-bottom: 1px solid var(--border);
  z-index: 10;
  flex-wrap: wrap;
}

.toolbar-left, .toolbar-right {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.view-mode-toggle {
  display: flex;
  background: rgba(15, 21, 38, 0.9);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 2px;
  gap: 2px;
}

.mode-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: transparent;
  border: none;
  color: var(--text-dim);
  font-size: 12px;
  font-weight: 500;
  padding: 4px 10px;
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.15s ease;
}
.mode-btn:hover { color: var(--text-primary); }
.mode-btn.active {
  background: var(--primary);
  color: #ffffff;
  font-weight: 600;
}

.layer-filter-pills {
  display: flex;
  gap: 4px;
}

.layer-pill {
  background: transparent;
  border: 1px solid transparent;
  color: var(--text-dim);
  font-size: 11px;
  font-weight: 500;
  padding: 4px 8px;
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.15s ease;
}
.layer-pill:hover {
  background: var(--surface-hover);
  color: var(--text-primary);
}
.layer-pill.active {
  background: rgba(99, 102, 241, 0.15);
  border-color: rgba(99, 102, 241, 0.4);
  color: #38bdf8;
  font-weight: 600;
}

.active-trace-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: rgba(56, 189, 248, 0.15);
  border: 1px solid rgba(56, 189, 248, 0.4);
  color: #38bdf8;
  font-size: 11px;
  font-weight: 600;
  padding: 3px 8px;
  border-radius: 999px;
}

.trace-pulse {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #38bdf8;
  animation: pulse-glow 1.5s infinite ease-in-out;
}

.btn-clear-trace {
  background: transparent;
  border: none;
  color: #38bdf8;
  font-size: 12px;
  cursor: pointer;
  padding: 0 2px;
}

.tool-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  background: rgba(15, 21, 38, 0.7);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text-dim);
  font-size: 11px;
  padding: 4px 9px;
  cursor: pointer;
  transition: all 0.15s ease;
}
.tool-btn:hover {
  border-color: var(--border-strong);
  color: var(--text-primary);
}
.tool-btn.active {
  background: rgba(16, 185, 129, 0.15);
  border-color: rgba(16, 185, 129, 0.4);
  color: #34d399;
}

.particle-indicator {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
}

/* MAIN WORKSPACE */
.graph-workspace-area {
  flex: 1;
  position: relative;
  overflow: hidden;
  display: flex;
}

.spatial-canvas-container {
  flex: 1;
  height: 100%;
  position: relative;
  outline: none;
}

.hud-hover-tooltip {
  position: absolute;
  top: 16px;
  left: 16px;
  background: rgba(10, 14, 26, 0.9);
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-sm);
  padding: 8px 12px;
  pointer-events: none;
  backdrop-filter: blur(8px);
  z-index: 20;
}
.hover-type-badge {
  font-size: 9px;
  font-weight: 700;
  letter-spacing: 0.05em;
  color: #38bdf8;
}
.hover-label {
  font-size: 13px;
  font-weight: 600;
  color: #ffffff;
}
.hover-sub {
  font-size: 10px;
  color: var(--text-faint);
}

.spatial-legend {
  position: absolute;
  bottom: 12px;
  left: 16px;
  display: flex;
  gap: 12px;
  background: rgba(10, 14, 26, 0.7);
  backdrop-filter: blur(8px);
  border: 1px solid var(--border-subtle);
  border-radius: 999px;
  padding: 4px 12px;
  pointer-events: none;
  font-size: 11px;
  color: var(--text-dim);
}
.legend-item { display: inline-flex; align-items: center; gap: 5px; }
.legend-dot { width: 7px; height: 7px; border-radius: 50%; }
.bg-indigo { background: #6366f1; }
.bg-cyan { background: #38bdf8; }
.bg-purple { background: #a855f7; }
.bg-teal { background: #06b6d4; }
.bg-emerald { background: #10b981; }

/* 2D KNOWLEDGE GRAPH */
.knowledge-2d-container {
  flex: 1;
  height: 100%;
  overflow-x: auto;
  overflow-y: auto;
  padding: 20px;
  background: rgba(6, 9, 17, 0.95);
}

.dag-columns {
  display: flex;
  gap: 16px;
  min-width: 1100px;
  height: 100%;
}

.dag-column {
  flex: 1;
  display: flex;
  flex-direction: column;
  background: rgba(15, 21, 38, 0.4);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius);
  padding: 12px;
  gap: 10px;
  overflow-y: auto;
}

.column-header {
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--text-dim);
  border-bottom: 1px solid var(--border-subtle);
  padding-bottom: 6px;
}

.column-nodes {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.dag-node-card {
  background: rgba(10, 14, 26, 0.85);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 10px 12px;
  cursor: pointer;
  transition: all 0.15s ease;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.dag-node-card:hover {
  border-color: var(--border-strong);
  background: var(--surface-hover);
  transform: translateY(-1px);
}
.dag-node-card.selected {
  border-color: var(--primary);
  background: rgba(99, 102, 241, 0.15);
  box-shadow: 0 0 0 1px var(--primary);
}
.dag-node-card.traced {
  border-color: #38bdf8;
  background: rgba(56, 189, 248, 0.08);
}

.node-card-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.node-type-tag {
  font-size: 9px;
  font-weight: 700;
  letter-spacing: 0.04em;
  padding: 1px 5px;
  border-radius: 3px;
}
.tag-signal { background: rgba(244, 63, 94, 0.15); color: #f43f5e; }
.tag-agent { background: rgba(56, 189, 248, 0.15); color: #38bdf8; }
.tag-decision { background: rgba(168, 85, 247, 0.15); color: #c084fc; }
.tag-campaign { background: rgba(99, 102, 241, 0.15); color: #818cf8; }
.tag-experiment { background: rgba(6, 182, 212, 0.15); color: #22d3ee; }
.tag-outcome { background: rgba(16, 185, 129, 0.15); color: #34d399; }

.node-status-dot { width: 6px; height: 6px; border-radius: 50%; }
.dot-running { background: #38bdf8; animation: pulse-glow 1.5s infinite; }
.dot-positive { background: #10b981; }
.dot-neutral { background: #64748b; }
.dot-purple { background: #a855f7; }

.node-card-title {
  font-size: 12px;
  font-weight: 600;
  color: #ffffff;
  line-height: 1.3;
}
.node-card-sub, .node-card-meta {
  font-size: 10px;
  color: var(--text-dim);
  display: flex;
  justify-content: space-between;
}

/* DOM INSPECTOR PANEL */
.graph-inspector-panel {
  position: absolute;
  top: 0;
  right: 0;
  width: min(380px, 92vw);
  height: 100%;
  background: rgba(10, 14, 26, 0.95);
  backdrop-filter: blur(16px);
  -webkit-backdrop-filter: blur(16px);
  border-left: 1px solid var(--border);
  box-shadow: -8px 0 24px rgba(0, 0, 0, 0.6);
  z-index: 30;
  display: flex;
  flex-direction: column;
  padding: 20px;
  gap: 16px;
  overflow-y: auto;
  animation: slide-inspector 0.2s cubic-bezier(0.16, 1, 0.3, 1);
}

@keyframes slide-inspector {
  from { transform: translateX(100%); }
  to { transform: translateX(0); }
}

.inspector-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.inspector-type-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.type-pill {
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.05em;
  padding: 2px 7px;
  border-radius: 4px;
}
.type-campaign { background: rgba(99, 102, 241, 0.2); color: #818cf8; }
.type-agent { background: rgba(56, 189, 248, 0.2); color: #38bdf8; }
.type-decision, .type-laya_decision { background: rgba(168, 85, 247, 0.2); color: #c084fc; }
.type-experiment { background: rgba(6, 182, 212, 0.2); color: #22d3ee; }
.type-outcome { background: rgba(16, 185, 129, 0.2); color: #34d399; }

.status-badge {
  font-size: 10px;
  font-weight: 600;
  color: var(--text-dim);
  text-transform: uppercase;
}
.status-positive { color: #34d399; }
.status-running { color: #38bdf8; }

.btn-inspector-close {
  background: transparent;
  border: none;
  color: var(--text-faint);
  font-size: 14px;
  cursor: pointer;
  padding: 4px;
}
.btn-inspector-close:hover { color: var(--text-primary); }

.inspector-title {
  font-size: 16px;
  font-weight: 700;
  color: #ffffff;
  margin: 0;
  line-height: 1.3;
}

.inspector-desc {
  font-size: 12px;
  color: var(--text-dim);
  line-height: 1.4;
  margin: 0;
}

.inspector-section {
  display: flex;
  flex-direction: column;
  gap: 8px;
  border-top: 1px solid var(--border-subtle);
  padding-top: 12px;
}

.section-title {
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--text-faint);
  display: flex;
  align-items: center;
  gap: 6px;
}

/* Laya Card */
.laya-card {
  background: rgba(168, 85, 247, 0.08);
  border: 1px solid rgba(168, 85, 247, 0.3);
  border-radius: var(--radius-sm);
  padding: 10px 12px;
}

.laya-icon { font-size: 12px; }

.shadow-tag {
  margin-left: auto;
  font-size: 9px;
  font-weight: 700;
  color: #c084fc;
  background: rgba(168, 85, 247, 0.2);
  padding: 1px 5px;
  border-radius: 3px;
}

.laya-prob-distribution {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-top: 6px;
}

.dist-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 11px;
}

.dist-label {
  width: 50px;
  color: var(--text-dim);
  font-weight: 600;
}

.dist-bar-track {
  flex: 1;
  height: 6px;
  background: rgba(255, 255, 255, 0.08);
  border-radius: 3px;
  overflow: hidden;
}

.dist-bar-fill {
  height: 100%;
  background: linear-gradient(90deg, #6366f1, #a855f7);
  border-radius: 3px;
}

.dist-val {
  width: 32px;
  text-align: right;
  font-family: monospace;
  color: #ffffff;
  font-weight: 600;
}

.laya-meta-footer {
  display: flex;
  flex-direction: column;
  gap: 2px;
  font-size: 10px;
  color: var(--text-dim);
  margin-top: 6px;
}

/* Metrics Grid */
.metrics-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 8px;
}

.m-card {
  background: rgba(15, 21, 38, 0.6);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  padding: 8px 10px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.m-label { font-size: 10px; color: var(--text-faint); text-transform: uppercase; }
.m-val { font-size: 14px; font-weight: 700; color: #ffffff; }

/* Lineage Chips */
.lineage-chips {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.lineage-chip {
  display: flex;
  align-items: center;
  gap: 8px;
  background: rgba(15, 21, 38, 0.6);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  padding: 6px 10px;
  cursor: pointer;
  text-align: left;
  transition: all 0.15s ease;
}
.lineage-chip:hover {
  border-color: var(--border-strong);
  background: var(--surface-hover);
}
.chip-type {
  font-size: 9px;
  text-transform: uppercase;
  color: var(--text-faint);
  font-weight: 700;
}
.chip-name {
  font-size: 12px;
  color: var(--text-primary);
  font-weight: 500;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.inspector-actions {
  margin-top: auto;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding-top: 14px;
  border-top: 1px solid var(--border);
}

.btn-act {
  width: 100%;
  padding: 8px 14px;
  border-radius: var(--radius-sm);
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
.btn-act.primary {
  background: linear-gradient(135deg, #4f46e5, #6366f1);
  color: #fff;
  border: 1px solid rgba(255, 255, 255, 0.15);
}
.btn-act.success {
  background: rgba(16, 185, 129, 0.15);
  border: 1px solid rgba(16, 185, 129, 0.4);
  color: #34d399;
}
.btn-act.secondary {
  background: transparent;
  border: 1px solid var(--border);
  color: var(--text-dim);
}
</style>
