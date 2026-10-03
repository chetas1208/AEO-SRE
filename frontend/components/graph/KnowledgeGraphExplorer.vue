<script setup lang="ts">
import { ref, computed, watch, onMounted, onBeforeUnmount, nextTick } from 'vue'
import * as THREE from 'three'

export interface GraphNode {
  id: string
  label: string
  type: string
  z?: number
  color?: string
  amount?: number
  status?: string
  props?: Record<string, any>
  x?: number
  y?: number
  vx?: number
  vy?: number
}

export interface GraphEdge {
  id: string
  source: string
  target: string
  type: string
  color?: string
  dashed?: boolean
}

const props = withDefaults(
  defineProps<{
    initialPerspective?: 'intent' | 'perception' | 'campaign' | 'experiment'
    initialFocusId?: string | null
    defaultMode?: '2d' | '3d'
    embedded?: boolean
    title?: string
  }>(),
  {
    initialPerspective: 'intent',
    initialFocusId: null,
    defaultMode: '2d',
    embedded: false,
    title: 'Knowledge Graph Explorer'
  }
)

const emit = defineEmits<{
  selectNode: [node: GraphNode | null]
  perspectiveChange: [perspective: string]
}>()

// Mode: 2D is the analytical default, 3D is spatial exploration
const currentMode = ref<'2d' | '3d'>(props.defaultMode)
const currentPerspective = ref<'intent' | 'perception' | 'campaign' | 'experiment'>(props.initialPerspective)
const layoutType = ref<'hierarchical' | 'force'>('hierarchical')
const isFullscreen = ref(false)
const showTreeFallback = ref(false)

// Graph data
const nodes = ref<GraphNode[]>([])
const edges = ref<GraphEdge[]>([])
const selectedNodeId = ref<string | null>(props.initialFocusId)
const hoveredNodeId = ref<string | null>(null)
const highlightPathIds = ref<Set<string>>(new Set())
const isLoading = ref(false)
const errorMsg = ref<string | null>(null)

// Search & Trace Path
const searchQuery = ref('')
const isTracingPath = ref(false)
const traceFromId = ref<string | null>(null)
const traceToId = ref<string | null>(null)

// Container and Canvas Refs
const graphContainer = ref<HTMLDivElement | null>(null)
const svgContainer = ref<SVGSVGElement | null>(null)
const threeCanvas = ref<HTMLCanvasElement | null>(null)

// 2D Pan and Zoom state
const pan = ref({ x: 50, y: 50 })
const zoom = ref(1.0)
let isPanning = false
let panStart = { x: 0, y: 0 }

// 3D Three.js variables
let threeScene: THREE.Scene | null = null
let threeCamera: THREE.PerspectiveCamera | null = null
let threeRenderer: THREE.WebGLRenderer | null = null
let animFrameId: number | null = null
const threeObjects: Map<string, THREE.Object3D> = new Map()
const threeLines: THREE.Line[] = []

// Color mapping by node family
const TYPE_COLORS: Record<string, string> = {
  intent: '#38bdf8',
  constraint: '#38bdf8',
  product: '#6366f1',
  claim: '#10b981',
  ai_claim: '#f43f5e',
  gap: '#f43f5e',
  engine: '#a855f7',
  campaign: '#6366f1',
  cost: '#f59e0b',
  person: '#38bdf8',
  agent: '#818cf8',
  asset: '#22d3ee',
  video: '#22d3ee',
  channel: '#60a5fa',
  outcome: '#10b981',
  hypothesis: '#f59e0b',
  intervention: '#38bdf8',
  experiment: '#6366f1',
  observation: '#818cf8',
  reward: '#10b981',
  policy: '#a855f7'
}

function getNodeColor(type: string): string {
  return TYPE_COLORS[type.toLowerCase()] || '#94a3b8'
}

// Compute selected node object
const selectedNode = computed(() => {
  if (!selectedNodeId.value) return null
  return nodes.value.find(n => n.id === selectedNodeId.value) || null
})

// Connected edges for inspector
const selectedNodeEdges = computed(() => {
  if (!selectedNodeId.value) return { incoming: [], outgoing: [] }
  const sid = selectedNodeId.value
  return {
    incoming: edges.value.filter(e => e.target === sid),
    outgoing: edges.value.filter(e => e.source === sid)
  }
})

// Fetch graph context from backend API
async function loadGraphData() {
  isLoading.value = true
  errorMsg.value = null
  try {
    const config = useRuntimeConfig()
    const apiBase = useApiBase()
    const res = await $fetch<any>(`${apiBase}/api/graph/context`, {
      params: {
        perspective: currentPerspective.value,
        focus_id: selectedNodeId.value || undefined
      }
    })
    if (res && res.nodes) {
      nodes.value = res.nodes
      edges.value = res.edges || []
      if (res.highlight_paths) {
        const pathList = Object.values(res.highlight_paths).flat() as string[]
        highlightPathIds.value = new Set(pathList)
      } else {
        highlightPathIds.value.clear()
      }
      if (!selectedNodeId.value && res.focus_id) {
        selectedNodeId.value = res.focus_id
      }
    }
  } catch (err: any) {
    console.warn('[KnowledgeGraph] Backend graph context unavailable, using local fallback:', err?.message)
    // Fallback to local perspective definitions
    loadFallbackData(currentPerspective.value)
  } finally {
    isLoading.value = false
    compute2DLayout()
    if (currentMode.value === '3d') {
      nextTick(() => update3DScene())
    }
  }
}

function loadFallbackData(p: string) {
  if (p === 'intent') {
    nodes.value = [
      { id: 'intent-muse-01', label: 'Enterprise CRM Ingestion', type: 'intent', z: -10, color: '#38bdf8', props: { source: 'MUSE', goal: 'Automated pipeline sync' } },
      { id: 'c-saml', label: 'SAML 2.0 SSO on Business', type: 'constraint', z: -5, color: '#38bdf8', props: { status: 'verified', tier: 'Business' } },
      { id: 'c-audit', label: 'Real-time Audit Log Export', type: 'constraint', z: -5, color: '#38bdf8', props: { status: 'verified' } },
      { id: 'prod-acme', label: 'Acme Cloud Platform', type: 'product', z: 0, color: '#6366f1', props: { fit: 0.94 } },
      { id: 'claim-saml', label: 'SAML 2.0 included in Business ($49/mo)', type: 'claim', z: 5, color: '#10b981', props: { doc: '/pricing.html' } },
      { id: 'aiclaim-saml', label: 'Perplexity: SAML requires Enterprise', type: 'ai_claim', z: 8, color: '#f43f5e', props: { engine: 'Perplexity' } },
      { id: 'gap-saml', label: 'Discovery Gap: Wrong Tier Pricing', type: 'gap', z: 10, color: '#f43f5e', props: { severity: 'HIGH' } }
    ]
    edges.value = [
      { id: 'e-1', source: 'intent-muse-01', target: 'c-saml', type: 'REQUIRES' },
      { id: 'e-2', source: 'intent-muse-01', target: 'c-audit', type: 'REQUIRES' },
      { id: 'e-3', source: 'c-saml', target: 'prod-acme', type: 'EVALUATES' },
      { id: 'e-4', source: 'c-audit', target: 'prod-acme', type: 'EVALUATES' },
      { id: 'e-5', source: 'prod-acme', target: 'claim-saml', type: 'ASSERTS' },
      { id: 'e-6', source: 'claim-saml', target: 'aiclaim-saml', type: 'CONTRADICTS' },
      { id: 'e-7', source: 'aiclaim-saml', target: 'gap-saml', type: 'EXPOSES' }
    ]
    highlightPathIds.value = new Set(['intent-muse-01', 'c-saml', 'prod-acme', 'claim-saml', 'aiclaim-saml', 'gap-saml'])
  } else if (p === 'campaign') {
    nodes.value = [
      { id: 'cost-people', label: 'People ($11.4K)', type: 'cost', z: -12, color: '#f59e0b', amount: 11400 },
      { id: 'cost-agents', label: 'Agents & APIs ($2.8K)', type: 'cost', z: -12, color: '#f59e0b', amount: 2770 },
      { id: 'person-sarah', label: 'Sarah Jenkins (PMM Lead)', type: 'person', z: -8, color: '#38bdf8' },
      { id: 'agent-profound', label: 'Profound Citation Agent', type: 'agent', z: -8, color: '#818cf8' },
      { id: 'cmp-01', label: 'Zero-Downtime Migration Blitz', type: 'campaign', z: 0, color: '#6366f1', amount: 42780 },
      { id: 'asset-landing', label: 'Verified Landing Page', type: 'asset', z: 4, color: '#22d3ee' },
      { id: 'dist-web', label: 'Documentation Index', type: 'channel', z: 8, color: '#60a5fa' },
      { id: 'outcome-profound', label: '+9.2pp AI Visibility Gain', type: 'outcome', z: 12, color: '#10b981' }
    ]
    edges.value = [
      { id: 'ce-1', source: 'cost-people', target: 'person-sarah', type: 'FUNDS' },
      { id: 'ce-2', source: 'cost-agents', target: 'agent-profound', type: 'FUNDS' },
      { id: 'ce-3', source: 'person-sarah', target: 'cmp-01', type: 'CONTRIBUTES' },
      { id: 'ce-4', source: 'agent-profound', target: 'cmp-01', type: 'EXECUTES' },
      { id: 'ce-5', source: 'cmp-01', target: 'asset-landing', type: 'PRODUCES' },
      { id: 'ce-6', source: 'asset-landing', target: 'dist-web', type: 'DISTRIBUTED_TO' },
      { id: 'ce-7', source: 'dist-web', target: 'outcome-profound', type: 'MEASURED_BY' }
    ]
    highlightPathIds.value = new Set(['cost-people', 'person-sarah', 'cmp-01', 'asset-landing', 'dist-web', 'outcome-profound'])
  } else if (p === 'perception') {
    nodes.value = [
      { id: 'prod-acme', label: 'Acme Cloud Platform', type: 'product', z: -8, color: '#6366f1' },
      { id: 'claim-sso', label: 'Canonical: SAML on Business', type: 'claim', z: -4, color: '#10b981' },
      { id: 'engine-perplexity', label: 'Perplexity Engine', type: 'engine', z: 0, color: '#a855f7' },
      { id: 'ai-stale', label: 'Perceived: Enterprise Only', type: 'ai_claim', z: 4, color: '#f43f5e' },
      { id: 'gap-saml', label: 'Discovery Gap: Tier Gate', type: 'gap', z: 8, color: '#f43f5e' }
    ]
    edges.value = [
      { id: 'pe-1', source: 'prod-acme', target: 'claim-sso', type: 'ASSERTS' },
      { id: 'pe-2', source: 'claim-sso', target: 'engine-perplexity', type: 'CITED_BY' },
      { id: 'pe-3', source: 'engine-perplexity', target: 'ai-stale', type: 'CONTRADICTED_BY' },
      { id: 'pe-4', source: 'ai-stale', target: 'gap-saml', type: 'CREATES_GAP' }
    ]
    highlightPathIds.value = new Set(['prod-acme', 'claim-sso', 'engine-perplexity', 'ai-stale', 'gap-saml'])
  } else {
    nodes.value = [
      { id: 'gap-exp', label: 'Discovery Gap: Missing Schema.org', type: 'gap', z: -10, color: '#f43f5e' },
      { id: 'hyp-exp', label: 'Hypothesis: Stale Crawler Cache', type: 'hypothesis', z: -6, color: '#f59e0b' },
      { id: 'int-exp', label: 'Intervention: Structured JSON-LD', type: 'intervention', z: -2, color: '#38bdf8' },
      { id: 'exp-01', label: 'EXP-0001 (auth0.com)', type: 'experiment', z: 2, color: '#6366f1' },
      { id: 'obs-01', label: 'Delayed Observation Window', type: 'observation', z: 6, color: '#818cf8' },
      { id: 'reward-01', label: 'Reward (0.35 Vis + 0.30 Cit)', type: 'reward', z: 10, color: '#10b981' }
    ]
    edges.value = [
      { id: 'ee-1', source: 'gap-exp', target: 'hyp-exp', type: 'INVESTIGATES' },
      { id: 'ee-2', source: 'hyp-exp', target: 'int-exp', type: 'PROPOSES' },
      { id: 'ee-3', source: 'int-exp', target: 'exp-01', type: 'ACTIVATES' },
      { id: 'ee-4', source: 'exp-01', target: 'obs-01', type: 'OBSERVES' },
      { id: 'ee-5', source: 'obs-01', target: 'reward-01', type: 'EVALUATES' }
    ]
    highlightPathIds.value = new Set(['gap-exp', 'hyp-exp', 'int-exp', 'exp-01', 'obs-01', 'reward-01'])
  }
}

// 2D LAYOUT ALGORITHMS
function compute2DLayout() {
  const nList = nodes.value
  if (!nList.length) return

  if (layoutType.value === 'hierarchical') {
    // Sort nodes by semantic z level (or topological order)
    const sorted = [...nList].sort((a, b) => (a.z ?? 0) - (b.z ?? 0))
    // Group into columns
    const columns: Map<number, GraphNode[]> = new Map()
    sorted.forEach(node => {
      const zKey = node.z ?? 0
      const col = columns.get(zKey) || []
      col.push(node)
      columns.set(zKey, col)
    })

    const colKeys = Array.from(columns.keys()).sort((a, b) => a - b)
    const colSpacing = 200
    const rowSpacing = 85

    colKeys.forEach((key, colIndex) => {
      const colNodes = columns.get(key) || []
      const totalH = (colNodes.length - 1) * rowSpacing
      colNodes.forEach((node, rowIndex) => {
        node.x = 80 + colIndex * colSpacing
        node.y = 200 + rowIndex * rowSpacing - totalH / 2
      })
    })
  } else {
    // Force-directed layout initial scatter
    const centerX = 450
    const centerY = 240
    nList.forEach((node, i) => {
      const angle = (i / nList.length) * 2 * Math.PI
      const dist = 140 + (i % 3) * 40
      node.x = centerX + Math.cos(angle) * dist
      node.y = centerY + Math.sin(angle) * dist
      node.vx = 0
      node.vy = 0
    })

    // Run simple spring simulation steps
    for (let step = 0; step < 40; step++) {
      for (let i = 0; i < nList.length; i++) {
        for (let j = i + 1; j < nList.length; j++) {
          const a = nList[i]
          const b = nList[j]
          if (!a || !b) continue
          const dx = (b.x ?? 0) - (a.x ?? 0)
          const dy = (b.y ?? 0) - (a.y ?? 0)
          const dist = Math.sqrt(dx * dx + dy * dy) || 1
          if (dist < 180) {
            const force = (180 - dist) / dist * 0.15
            a.x = (a.x ?? 0) - dx * force
            a.y = (a.y ?? 0) - dy * force
            b.x = (b.x ?? 0) + dx * force
            b.y = (b.y ?? 0) + dy * force
          }
        }
      }
      edges.value.forEach(e => {
        const s = nList.find(n => n.id === e.source)
        const t = nList.find(n => n.id === e.target)
        if (s && t) {
          const dx = (t.x ?? 0) - (s.x ?? 0)
          const dy = (t.y ?? 0) - (s.y ?? 0)
          const dist = Math.sqrt(dx * dx + dy * dy) || 1
          const force = (dist - 140) * 0.05
          s.x = (s.x ?? 0) + (dx / dist) * force
          s.y = (s.y ?? 0) + (dy / dist) * force
          t.x = (t.x ?? 0) - (dx / dist) * force
          t.y = (t.y ?? 0) - (dy / dist) * force
        }
      })
    }
  }
}

// 2D Pan and Zoom handlers
function onMouseDown(e: MouseEvent) {
  if (e.button !== 0) return
  isPanning = true
  panStart = { x: e.clientX - pan.value.x, y: e.clientY - pan.value.y }
}

function onMouseMove(e: MouseEvent) {
  if (!isPanning) return
  pan.value = { x: e.clientX - panStart.x, y: e.clientY - panStart.y }
}

function onMouseUp() {
  isPanning = false
}

function onWheel(e: WheelEvent) {
  e.preventDefault()
  const factor = e.deltaY < 0 ? 1.1 : 0.9
  zoom.value = Math.max(0.4, Math.min(2.5, zoom.value * factor))
}

function fitZoom() {
  pan.value = { x: 40, y: 40 }
  zoom.value = 1.0
}

function resetZoom() {
  pan.value = { x: 0, y: 0 }
  zoom.value = 1.0
}

// Node Interaction & Selection
function selectNode(id: string) {
  selectedNodeId.value = id
  const n = nodes.value.find(item => item.id === id) || null
  emit('selectNode', n)

  if (isTracingPath.value) {
    if (!traceFromId.value) {
      traceFromId.value = id
    } else if (!traceToId.value && traceFromId.value !== id) {
      traceToId.value = id
      computeTracedPath(traceFromId.value, traceToId.value)
    } else {
      traceFromId.value = id
      traceToId.value = null
    }
  } else {
    // Progressive focus: highlight active path connected to selected node
    updateHighlightPathForNode(id)
  }
}

function updateHighlightPathForNode(id: string) {
  const related = new Set<string>([id])
  // Downstream
  edges.value.forEach(e => {
    if (e.source === id) related.add(e.target)
    if (e.target === id) related.add(e.source)
  })
  highlightPathIds.value = related
}

function computeTracedPath(startId: string, endId: string) {
  // BFS pathfinding
  const queue: string[][] = [[startId]]
  const visited = new Set<string>([startId])
  let foundPath: string[] | null = null

  while (queue.length > 0) {
    const path = queue.shift()!
    const current = path[path.length - 1]
    if (current === endId) {
      foundPath = path
      break
    }
    const neighbors = edges.value
      .filter(e => e.source === current || e.target === current)
      .map(e => e.source === current ? e.target : e.source)

    for (const nb of neighbors) {
      if (!visited.has(nb)) {
        visited.add(nb)
        queue.push([...path, nb])
      }
    }
  }

  if (foundPath) {
    highlightPathIds.value = new Set(foundPath)
  }
}

function toggleTracePathMode() {
  isTracingPath.value = !isTracingPath.value
  if (!isTracingPath.value) {
    traceFromId.value = null
    traceToId.value = null
    highlightPathIds.value.clear()
  }
}

function expandNeighbors() {
  if (!selectedNodeId.value) return
  // Add 1-hop connected nodes to the current slice
  updateHighlightPathForNode(selectedNodeId.value)
}

function switchPerspective(p: 'intent' | 'perception' | 'campaign' | 'experiment') {
  currentPerspective.value = p
  emit('perspectiveChange', p)
  loadGraphData()
}

function toggleFullscreen() {
  isFullscreen.value = !isFullscreen.value
}

// Node visibility and edge dimming policies
function isNodeDimmed(id: string): boolean {
  if (searchQuery.value) {
    const q = searchQuery.value.toLowerCase()
    const node = nodes.value.find(n => n.id === id)
    if (!node) return true
    return !node.label.toLowerCase().includes(q) && !node.type.toLowerCase().includes(q)
  }
  if (!highlightPathIds.value.size) return false
  return !highlightPathIds.value.has(id)
}

function isEdgeDimmed(source: string, target: string): boolean {
  if (!highlightPathIds.value.size) return false
  return !(highlightPathIds.value.has(source) && highlightPathIds.value.has(target))
}

function edgePathSvg(s: GraphNode, t: GraphNode): string {
  const x1 = (s.x ?? 0) + 70
  const y1 = (s.y ?? 0) + 20
  const x2 = (t.x ?? 0) - 70
  const y2 = (t.y ?? 0) + 20
  const mx = (x1 + x2) / 2
  return `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`
}

// 3D THREE.JS SPATIAL MODE IMPLEMENTATION
function init3DScene() {
  if (!threeCanvas.value) return
  const w = threeCanvas.value.clientWidth || 800
  const h = threeCanvas.value.clientHeight || 450

  threeScene = new THREE.Scene()
  threeScene.fog = new THREE.FogExp2(0x060911, 0.015)

  threeCamera = new THREE.PerspectiveCamera(45, w / h, 0.1, 1000)
  threeCamera.position.set(0, 10, 28)
  threeCamera.lookAt(0, 0, 0)

  threeRenderer = new THREE.WebGLRenderer({ canvas: threeCanvas.value, antialias: true, alpha: true })
  threeRenderer.setSize(w, h)
  threeRenderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))

  const ambient = new THREE.AmbientLight(0xffffff, 0.8)
  threeScene.add(ambient)

  const dir = new THREE.DirectionalLight(0x38bdf8, 1.2)
  dir.position.set(10, 20, 20)
  threeScene.add(dir)

  update3DScene()
  start3DLoop()
}

function update3DScene() {
  if (!threeScene) return
  // Clear old meshes
  threeObjects.forEach(obj => threeScene?.remove(obj))
  threeObjects.clear()
  threeLines.forEach(l => threeScene?.remove(l))
  threeLines.length = 0

  const posMap = new Map<string, THREE.Vector3>()
  // Arrange nodes by z depth
  nodes.value.forEach((node, i) => {
    const z = node.z ?? 0
    const angle = (i / (nodes.value.length || 1)) * Math.PI * 1.5 - Math.PI * 0.75
    const x = Math.sin(angle) * 7
    const y = ((i % 4) - 1.5) * 2.5
    const pos = new THREE.Vector3(x, y, z)
    posMap.set(node.id, pos)

    const geom = new THREE.SphereGeometry(0.7, 24, 24)
    const mat = new THREE.MeshStandardMaterial({
      color: new THREE.Color(getNodeColor(node.type)),
      emissive: new THREE.Color(getNodeColor(node.type)),
      emissiveIntensity: selectedNodeId.value === node.id ? 0.8 : 0.2,
      roughness: 0.3
    })
    const mesh = new THREE.Mesh(geom, mat)
    mesh.position.copy(pos)
    mesh.userData = { id: node.id }
    threeScene?.add(mesh)
    threeObjects.set(node.id, mesh)
  })

  // Render edges in 3D
  edges.value.forEach(e => {
    const p1 = posMap.get(e.source)
    const p2 = posMap.get(e.target)
    if (p1 && p2) {
      const geom = new THREE.BufferGeometry().setFromPoints([p1, p2])
      const isPath = highlightPathIds.value.has(e.source) && highlightPathIds.value.has(e.target)
      const mat = new THREE.LineBasicMaterial({
        color: isPath ? 0x38bdf8 : 0x475569,
        linewidth: isPath ? 2 : 1,
        transparent: true,
        opacity: isPath ? 0.9 : 0.2
      })
      const line = new THREE.Line(geom, mat)
      threeScene?.add(line)
      threeLines.push(line)
    }
  })
}

function start3DLoop() {
  if (animFrameId) return
  const loop = () => {
    if (currentMode.value === '3d' && threeRenderer && threeScene && threeCamera) {
      threeRenderer.render(threeScene, threeCamera)
      animFrameId = requestAnimationFrame(loop)
    }
  }
  animFrameId = requestAnimationFrame(loop)
}

function stop3DLoop() {
  if (animFrameId) {
    cancelAnimationFrame(animFrameId)
    animFrameId = null
  }
}

function dispose3D() {
  stop3DLoop()
  threeObjects.forEach(obj => {
    threeScene?.remove(obj)
    if ((obj as THREE.Mesh).geometry) (obj as THREE.Mesh).geometry.dispose()
  })
  threeObjects.clear()
  threeLines.forEach(l => {
    threeScene?.remove(l)
    l.geometry.dispose()
  })
  threeLines.length = 0
  if (threeRenderer) {
    threeRenderer.dispose()
    threeRenderer = null
  }
  threeScene = null
  threeCamera = null
}

watch(currentMode, (newMode) => {
  if (newMode === '3d') {
    nextTick(() => init3DScene())
  } else {
    dispose3D()
  }
})

watch(() => props.initialFocusId, (newId) => {
  if (newId) {
    selectedNodeId.value = newId
    updateHighlightPathForNode(newId)
  }
})

onMounted(() => {
  loadGraphData()
  if (currentMode.value === '3d') {
    nextTick(() => init3DScene())
  }
})

onBeforeUnmount(() => {
  dispose3D()
})
</script>

<template>
  <div :class="['knowledge-graph-explorer', { 'is-fullscreen': isFullscreen }]">
    <!-- COHERENT GRAPH TOOLBAR -->
    <header class="graph-toolbar">
      <!-- 2D / 3D Mode Toggle -->
      <div class="mode-toggle-group" role="group" aria-label="Graph Render Mode">
        <button
          type="button"
          :class="['mode-btn', { active: currentMode === '2d' }]"
          :aria-pressed="currentMode === '2d'"
          @click="currentMode = '2d'"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
            <rect x="3" y="3" width="18" height="18" rx="2" />
            <line x1="9" y1="3" x2="9" y2="21" />
            <line x1="15" y1="3" x2="15" y2="21" />
          </svg>
          2D Graph
        </button>

        <button
          type="button"
          :class="['mode-btn', { active: currentMode === '3d' }]"
          :aria-pressed="currentMode === '3d'"
          @click="currentMode = '3d'"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
            <path d="M12 2l10 6v8l-10 6-10-6V8z" />
            <path d="M12 22V12" />
            <path d="M22 8l-10 4-10-4" />
          </svg>
          3D Spatial
        </button>
      </div>

      <!-- Perspective Selector -->
      <div class="perspective-selector">
        <span class="toolbar-label">Perspective:</span>
        <button
          v-for="p in ['intent', 'perception', 'campaign', 'experiment'] as const"
          :key="p"
          type="button"
          :class="['perspective-chip', { active: currentPerspective === p }]"
          @click="switchPerspective(p)"
        >
          {{ p.charAt(0).toUpperCase() + p.slice(1) }}
        </button>
      </div>

      <!-- 2D Layout Toggle -->
      <div v-if="currentMode === '2d'" class="layout-toggle">
        <button
          type="button"
          :class="['tool-btn', { active: layoutType === 'hierarchical' }]"
          title="Hierarchical Causal Flow"
          @click="layoutType = 'hierarchical'; compute2DLayout()"
        >
          Hierarchical
        </button>
        <button
          type="button"
          :class="['tool-btn', { active: layoutType === 'force' }]"
          title="Force-Directed Neighborhood"
          @click="layoutType = 'force'; compute2DLayout()"
        >
          Force
        </button>
      </div>

      <!-- Search Input -->
      <div class="search-box">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
          <circle cx="11" cy="11" r="8" />
          <line x1="21" y1="21" x2="16.65" y2="16.65" />
        </svg>
        <input
          v-model="searchQuery"
          type="search"
          placeholder="Filter nodes, claims, entities…"
          aria-label="Filter graph nodes"
        >
      </div>

      <!-- Trace Path Toggle -->
      <button
        type="button"
        :class="['tool-btn', { active: isTracingPath }]"
        title="Trace causal path between two entities"
        @click="toggleTracePathMode"
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
          <circle cx="6" cy="6" r="3" />
          <circle cx="18" cy="18" r="3" />
          <line x1="8.5" y1="8.5" x2="15.5" y2="15.5" />
        </svg>
        Trace Path
      </button>

      <!-- Expand Neighbors -->
      <button
        type="button"
        class="tool-btn"
        title="Expand 1-hop connected neighbors"
        :disabled="!selectedNodeId"
        @click="expandNeighbors"
      >
        Expand
      </button>

      <!-- Fit / Reset Controls -->
      <button type="button" class="icon-tool-btn" title="Fit to Viewport" @click="fitZoom">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
          <path d="M8 3H5a2 2 0 0 0-2 2v3m18 0V5a2 2 0 0 0-2-2h-3m0 18h3a2 2 0 0 0 2-2v-3M3 16v3a2 2 0 0 0 2 2h3" />
        </svg>
      </button>

      <!-- Semantic Tree Fallback Toggle -->
      <button
        type="button"
        :class="['icon-tool-btn', { active: showTreeFallback }]"
        title="Accessible Tree Hierarchy"
        aria-label="Toggle Accessible DOM Tree"
        @click="showTreeFallback = !showTreeFallback"
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
          <line x1="8" y1="6" x2="21" y2="6" />
          <line x1="8" y1="12" x2="21" y2="12" />
          <line x1="8" y1="18" x2="21" y2="18" />
          <line x1="3" y1="6" x2="3.01" y2="6" />
          <line x1="3" y1="12" x2="3.01" y2="12" />
          <line x1="3" y1="18" x2="3.01" y2="18" />
        </svg>
      </button>

      <!-- Fullscreen Toggle -->
      <button
        type="button"
        class="icon-tool-btn"
        :title="isFullscreen ? 'Exit Fullscreen' : 'Fullscreen Graph'"
        @click="toggleFullscreen"
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
          <path v-if="!isFullscreen" d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7" />
          <path v-else d="M4 14h6v6M20 10h-6V4M14 10l7-7M10 14l-7 7" />
        </svg>
      </button>
    </header>

    <!-- TRACE PATH INSTRUCTION BAR -->
    <div v-if="isTracingPath" class="trace-bar">
      <span class="trace-prompt">
        <template v-if="!traceFromId">Select origin node (FROM)</template>
        <template v-else-if="!traceToId">Origin: <strong>{{ traceFromId }}</strong>. Select destination node (TO)</template>
        <template v-else>Tracing path from <strong>{{ traceFromId }}</strong> to <strong>{{ traceToId }}</strong></template>
      </span>
      <button type="button" class="btn-text-sm" @click="toggleTracePathMode">Cancel</button>
    </div>

    <!-- MAIN GRAPH WORKSPACE -->
    <div ref="graphContainer" class="graph-workspace-area">
      <!-- ACCESSIBLE DOM TREE FALLBACK -->
      <div v-if="showTreeFallback" class="tree-fallback-panel" role="tree" aria-label="Hierarchical Knowledge Graph">
        <h4 class="tree-title">Semantic Hierarchy View (Accessibility Fallback)</h4>
        <div v-for="node in nodes" :key="node.id" role="treeitem" class="tree-item" @click="selectNode(node.id)">
          <div class="tree-node-row">
            <span :class="['node-glyph-dot', `type-${node.type}`]" aria-hidden="true" />
            <strong class="tree-node-name">{{ node.label }}</strong>
            <span class="tree-node-type">[{{ node.type }}]</span>
          </div>
          <p v-if="node.props" class="tree-node-props">{{ JSON.stringify(node.props) }}</p>
        </div>
      </div>

      <!-- 2D ANALYTICAL GRAPH (DEFAULT) -->
      <div
        v-else-if="currentMode === '2d'"
        class="canvas-2d-wrap"
        @mousedown="onMouseDown"
        @mousemove="onMouseMove"
        @mouseup="onMouseUp"
        @wheel="onWheel"
      >
        <svg
          ref="svgContainer"
          class="svg-canvas"
          width="100%"
          height="100%"
          role="img"
          aria-label="2D Knowledge Graph"
        >
          <defs>
            <marker id="kg-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto">
              <path d="M0,2 L8,5 L0,8 z" fill="#64748b" />
            </marker>
            <marker id="kg-arrow-highlight" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto">
              <path d="M0,2 L8,5 L0,8 z" fill="#38bdf8" />
            </marker>
          </defs>

          <!-- PAN / ZOOM LAYER -->
          <g :transform="`translate(${pan.x}, ${pan.y}) scale(${zoom})`">
            <!-- EDGES -->
            <g class="edges-group">
              <template v-for="edge in edges" :key="edge.id">
                <path
                  v-if="nodes.find(n => n.id === edge.source) && nodes.find(n => n.id === edge.target)"
                  :d="edgePathSvg(nodes.find(n => n.id === edge.source)!, nodes.find(n => n.id === edge.target)!)"
                  fill="none"
                  :stroke="isEdgeDimmed(edge.source, edge.target) ? '#1e293b' : (highlightPathIds.has(edge.source) && highlightPathIds.has(edge.target) ? '#38bdf8' : '#475569')"
                  :stroke-width="isEdgeDimmed(edge.source, edge.target) ? 1 : (highlightPathIds.has(edge.source) && highlightPathIds.has(edge.target) ? 3 : 1.5)"
                  :stroke-opacity="isEdgeDimmed(edge.source, edge.target) ? 0.08 : 0.85"
                  :marker-end="highlightPathIds.has(edge.source) && highlightPathIds.has(edge.target) ? 'url(#kg-arrow-highlight)' : 'url(#kg-arrow)'"
                  class="edge-path"
                />
              </template>
            </g>

            <!-- NODES -->
            <g class="nodes-group">
              <g
                v-for="node in nodes"
                :key="node.id"
                :transform="`translate(${node.x ?? 0}, ${node.y ?? 0})`"
                :class="['graph-node-g', { 'is-selected': selectedNodeId === node.id, 'is-dimmed': isNodeDimmed(node.id) }]"
                role="button"
                :aria-label="`${node.type}: ${node.label}`"
                tabindex="0"
                @click.stop="selectNode(node.id)"
                @keydown.enter.stop="selectNode(node.id)"
              >
                <!-- Compact node card -->
                <rect
                  x="-75"
                  y="-22"
                  width="150"
                  height="44"
                  rx="6"
                  class="node-card-bg"
                  :stroke="selectedNodeId === node.id ? '#38bdf8' : getNodeColor(node.type)"
                  :stroke-width="selectedNodeId === node.id ? 2.5 : 1"
                />
                <!-- Type glyph tag -->
                <rect x="-70" y="-18" width="6" height="36" rx="2" :fill="getNodeColor(node.type)" />
                <!-- Primary Label -->
                <text x="-58" y="-2" class="node-label-title">{{ node.label.length > 17 ? node.label.slice(0, 16) + '…' : node.label }}</text>
                <!-- Secondary Subtitle -->
                <text x="-58" y="14" class="node-label-sub" :fill="getNodeColor(node.type)">{{ node.type.toUpperCase() }}</text>
              </g>
            </g>
          </g>
        </svg>
      </div>

      <!-- 3D SPATIAL GRAPH -->
      <div v-else class="canvas-3d-wrap">
        <canvas ref="threeCanvas" class="three-canvas" />
        <div class="spatial-legend">
          <span>z = -10: Demand / Inputs</span>
          <span>z = -5: Constraints</span>
          <span>z = 0: Platform / Nexus</span>
          <span>z = +5: Claims / Assets</span>
          <span>z = +10: AI Perception / Gaps</span>
        </div>
      </div>

      <!-- RIGHT SIDEBAR INSPECTOR DRAWER -->
      <aside v-if="selectedNode" class="node-inspector-drawer" aria-label="Entity Inspector">
        <div class="inspector-header">
          <div class="inspector-title-row">
            <span :class="['type-badge', `tone-${selectedNode.type}`]" :style="{ color: getNodeColor(selectedNode.type) }">
              {{ selectedNode.type.toUpperCase() }}
            </span>
            <button type="button" class="btn-close" aria-label="Close Inspector" @click="selectedNodeId = null">✕</button>
          </div>
          <h3 class="inspector-name">{{ selectedNode.label }}</h3>
        </div>

        <div class="inspector-body">
          <!-- Properties List -->
          <div v-if="selectedNode.props" class="inspector-section">
            <h4 class="section-heading">Properties & Provenance</h4>
            <div class="props-table">
              <div v-for="(v, k) in selectedNode.props" :key="k" class="prop-row">
                <span class="prop-key">{{ k }}</span>
                <span class="prop-val">{{ String(v) }}</span>
              </div>
            </div>
          </div>

          <!-- Relationships Summary -->
          <div class="inspector-section">
            <h4 class="section-heading">Active Relationships ({{ selectedNodeEdges.incoming.length + selectedNodeEdges.outgoing.length }})</h4>
            <div class="edges-list">
              <div v-for="e in selectedNodeEdges.incoming" :key="e.id" class="edge-item">
                <span class="edge-badge">← {{ e.type }}</span>
                <span class="edge-target" @click="selectNode(e.source)">{{ e.source }}</span>
              </div>
              <div v-for="e in selectedNodeEdges.outgoing" :key="e.id" class="edge-item">
                <span class="edge-badge">→ {{ e.type }}</span>
                <span class="edge-target" @click="selectNode(e.target)">{{ e.target }}</span>
              </div>
            </div>
          </div>

          <!-- Node Actions -->
          <div class="inspector-actions">
            <button type="button" class="action-btn" @click="expandNeighbors">
              Expand 1-Hop Neighbors
            </button>
            <button
              v-if="selectedNode.type === 'campaign'"
              type="button"
              class="action-btn primary"
              @click="navigateTo('/campaigns')"
            >
              Open Campaign Detail →
            </button>
            <button
              v-else-if="selectedNode.type === 'gap'"
              type="button"
              class="action-btn primary"
              @click="navigateTo('/discovery-gaps')"
            >
              Open Discovery Gap Detail →
            </button>
            <button
              v-else-if="selectedNode.type === 'intent'"
              type="button"
              class="action-btn primary"
              @click="navigateTo('/matches')"
            >
              Open Match Evaluation →
            </button>
            <button
              v-else-if="selectedNode.type === 'experiment'"
              type="button"
              class="action-btn primary"
              @click="navigateTo('/experiments')"
            >
              Open Experiment Spine →
            </button>
          </div>
        </div>
      </aside>
    </div>
  </div>
</template>

<style scoped>
.knowledge-graph-explorer {
  display: flex;
  flex-direction: column;
  background: var(--bg-1, #0a0e1a);
  border: 1px solid var(--border);
  border-radius: 12px;
  overflow: hidden;
  height: 580px;
  position: relative;
}

.knowledge-graph-explorer.is-fullscreen {
  position: fixed;
  inset: 0;
  z-index: var(--z-modal, 50);
  height: 100vh;
  border-radius: 0;
}

/* TOOLBAR */
.graph-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 16px;
  background: rgba(13, 19, 34, 0.95);
  border-bottom: 1px solid var(--border);
  flex-wrap: wrap;
}

.mode-toggle-group {
  display: inline-flex;
  background: rgba(10, 14, 26, 0.9);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 2px;
}

.mode-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 10px;
  border-radius: 4px;
  background: transparent;
  border: 0;
  color: var(--text-dim);
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s ease;
}
.mode-btn.active {
  background: var(--primary, #6366f1);
  color: #ffffff;
}

.perspective-selector {
  display: flex;
  align-items: center;
  gap: 6px;
}

.toolbar-label {
  font-size: 11px;
  color: var(--text-faint);
  font-weight: 600;
  text-transform: uppercase;
}

.perspective-chip {
  background: transparent;
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 4px 8px;
  font-size: 11px;
  color: var(--text-dim);
  cursor: pointer;
}
.perspective-chip.active {
  border-color: #38bdf8;
  color: #38bdf8;
  background: rgba(56, 189, 248, 0.12);
}

.layout-toggle {
  display: inline-flex;
  gap: 4px;
}

.tool-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 10px;
  background: rgba(18, 26, 47, 0.7);
  border: 1px solid var(--border);
  border-radius: 4px;
  font-size: 11px;
  font-weight: 600;
  color: var(--text-dim);
  cursor: pointer;
}
.tool-btn.active {
  border-color: #a855f7;
  color: #c084fc;
  background: rgba(168, 85, 247, 0.15);
}

.icon-tool-btn {
  padding: 5px;
  background: transparent;
  border: 1px solid var(--border);
  border-radius: 4px;
  color: var(--text-dim);
  cursor: pointer;
}
.icon-tool-btn:hover {
  color: #ffffff;
  border-color: var(--border-strong);
}

.search-box {
  display: flex;
  align-items: center;
  gap: 6px;
  background: rgba(10, 14, 26, 0.9);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 4px 8px;
  margin-left: auto;
}
.search-box input {
  background: transparent;
  border: 0;
  color: #ffffff;
  font-size: 12px;
  width: 150px;
  outline: none;
}

/* TRACE BAR */
.trace-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 16px;
  background: rgba(168, 85, 247, 0.15);
  border-bottom: 1px solid rgba(168, 85, 247, 0.3);
  font-size: 12px;
  color: #e2e8f0;
}
.btn-text-sm {
  background: transparent;
  border: 0;
  color: #c084fc;
  cursor: pointer;
  font-weight: 600;
}

/* WORKSPACE AREA */
.graph-workspace-area {
  flex: 1;
  display: flex;
  position: relative;
  overflow: hidden;
  min-height: 0;
}

.canvas-2d-wrap, .canvas-3d-wrap {
  flex: 1;
  position: relative;
  overflow: hidden;
  background: radial-gradient(circle at 50% 50%, rgba(15, 23, 42, 0.6) 0%, rgba(6, 9, 17, 0.95) 100%);
}

.svg-canvas {
  width: 100%;
  height: 100%;
  cursor: grab;
}
.svg-canvas:active {
  cursor: grabbing;
}

.three-canvas {
  width: 100%;
  height: 100%;
  display: block;
}

.spatial-legend {
  position: absolute;
  bottom: 12px;
  left: 12px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 10px;
  font-family: var(--font-mono, monospace);
  color: #64748b;
  background: rgba(10, 14, 26, 0.7);
  padding: 6px 10px;
  border-radius: 4px;
  border: 1px solid var(--border-subtle);
  pointer-events: none;
}

/* 2D GRAPH NODES & EDGES */
.edge-path {
  transition: stroke-opacity 0.2s ease, stroke-width 0.2s ease;
}

.graph-node-g {
  cursor: pointer;
  transition: opacity 0.2s ease, transform 0.15s ease;
}
.graph-node-g.is-dimmed {
  opacity: 0.15;
}
.graph-node-g:hover text {
  fill: #ffffff;
}

.node-card-bg {
  fill: #0d1322;
  transition: all 0.15s ease;
}
.graph-node-g:hover .node-card-bg {
  fill: #151d34;
}

.node-label-title {
  fill: #f1f5f9;
  font-size: 11px;
  font-weight: 600;
}
.node-label-sub {
  font-size: 9px;
  font-weight: 700;
  font-family: var(--font-mono, monospace);
}

/* INSPECTOR DRAWER */
.node-inspector-drawer {
  width: 340px;
  max-width: 90vw;
  background: rgba(13, 19, 34, 0.96);
  border-left: 1px solid var(--border);
  backdrop-filter: blur(8px);
  display: flex;
  flex-direction: column;
  overflow-y: auto;
  z-index: var(--z-panel, 5);
}

.inspector-header {
  padding: 16px;
  border-bottom: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.inspector-title-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.type-badge {
  font-size: 10px;
  font-weight: 700;
  font-family: var(--font-mono, monospace);
}

.btn-close {
  background: transparent;
  border: 0;
  color: var(--text-faint);
  font-size: 14px;
  cursor: pointer;
}

.inspector-name {
  font-size: 15px;
  font-weight: 700;
  color: #ffffff;
}

.inspector-body {
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.section-heading {
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--text-faint);
  margin-bottom: 8px;
}

.props-table {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.prop-row {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  padding: 4px 6px;
  background: rgba(10, 14, 26, 0.6);
  border-radius: 4px;
}
.prop-key { color: var(--text-faint); }
.prop-val { color: #cbd5e1; font-family: var(--font-mono, monospace); word-break: break-all; }

.edges-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.edge-item {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 11px;
}
.edge-badge {
  font-size: 9px;
  font-weight: 700;
  color: #38bdf8;
  background: rgba(56, 189, 248, 0.1);
  padding: 1px 4px;
  border-radius: 3px;
}
.edge-target {
  color: #cbd5e1;
  cursor: pointer;
  text-decoration: underline;
}

.inspector-actions {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 8px;
}

.action-btn {
  padding: 8px 12px;
  background: rgba(18, 26, 47, 0.8);
  border: 1px solid var(--border);
  border-radius: 6px;
  color: #f1f5f9;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  text-align: center;
}
.action-btn.primary {
  background: var(--primary, #6366f1);
  border-color: rgba(255, 255, 255, 0.15);
  color: #ffffff;
}

/* SEMANTIC TREE FALLBACK */
.tree-fallback-panel {
  flex: 1;
  padding: 20px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.tree-title {
  font-size: 13px;
  color: #38bdf8;
  margin-bottom: 6px;
}
.tree-item {
  padding: 8px 12px;
  background: rgba(13, 19, 34, 0.6);
  border: 1px solid var(--border);
  border-radius: 6px;
  cursor: pointer;
}
.tree-node-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.tree-node-name { font-size: 13px; color: #ffffff; }
.tree-node-type { font-size: 11px; color: #64748b; font-family: var(--font-mono, monospace); }
.tree-node-props { font-size: 11px; color: #94a3b8; margin-top: 4px; }
</style>
