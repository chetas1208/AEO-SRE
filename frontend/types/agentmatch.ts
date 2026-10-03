// AgentMatch domain types (AgentMatch + Muse + Profound + Neo4j)

export type IntentSource = 'MUSE' | 'MANUAL' | 'TEST'

export type ConstraintStatus = 'satisfied' | 'contradicted' | 'unknown'
export type AiPerceptionStatus = 'supports' | 'contradicts' | 'silent' | 'unavailable'

export interface IntentConstraint {
  id: string
  statement: string
  status: ConstraintStatus
  aiPerception: AiPerceptionStatus
  discoveryGap: boolean
  reasons: string[]
  basis: Array<{
    key?: string | null
    statement: string
    source?: string | null
    relation?: string | null
  }>
  perceptionEvidence?: string[]
}

export interface IntentEnvelope {
  id: string
  intent: string
  goal: string
  constraints: IntentConstraint[]
  preferences: string[]
  expiresAt?: string | null
  expiresInSeconds?: number | null
  source: IntentSource
  updatedAt: string
  canonicalClaimsConsidered: number
  discoveryGapsCount: number
}

export type MatchState =
  | 'DISCOVERY_GAP'
  | 'HEALTHY_MATCH'
  | 'NOT_A_MATCH'
  | 'INSUFFICIENT_EVIDENCE'

export interface MatchCandidate {
  id: string
  productName: string
  vendor: string
  actualFitPct: number
  aiPerceivedFitPct: number
  gapPp: number
  state: MatchState
  satisfiedConstraintsCount: number
  totalConstraintsCount: number
  evidenceConfidence: number
  summary: string
  whyItMatches: string
  whyAiMissesIt: string
  marketingGap: string
  recommendedAction: string
  supportingEvidence: Array<{
    claim: string
    source: string
    status: 'verified' | 'contradicted' | 'stale'
    timestamp?: string
  }>
}

export type GapType =
  | 'MISSING_CAPABILITY'
  | 'INCORRECT_CAPABILITY'
  | 'STALE_INFORMATION'
  | 'MISSING_CITATION'
  | 'WRONG_TIER_PRICING'
  | 'UNKNOWN'

export interface DiscoveryGapItem {
  id: string
  incidentId?: string
  number?: number
  intentClass: string
  productName: string
  actualFitPct: number
  aiPerceivedFitPct: number
  gapPp: number
  gapType: GapType
  promptClustersCount: number
  confidence: number
  severity: 'critical' | 'high' | 'medium' | 'low'
  detectedAt: string
  sourceMode: 'LIVE' | 'SIMULATED' | 'TEST'
  productTruth: {
    statement: string
    canonicalKey?: string | null
    canonicalSource?: string | null
    verifiedAt?: string | null
  }
  aiPerception: {
    claim: string
    engines: string[]
    likelySource?: string | null
    citationsCount?: number
  }
  profoundMetrics: {
    visibilityPct: number
    citationSharePct: number
    promptCoveragePct: number
    competitorSharePct: number
    topSources: string[]
  }
  recommendedAction: {
    type: 'update_canonical_page' | 'correct_source' | 'create_faq' | 'clarify_pricing' | 'add_structured_evidence' | 'observe'
    title: string
    description: string
  }
  approvalStatus: 'pending' | 'approved' | 'rejected' | 'modified'
}

export interface GraphNodeSemantic {
  id: string
  label: string
  type: 'intent' | 'constraint' | 'product' | 'claim' | 'ai_claim' | 'discovery_gap' | 'evidence'
  z: number
  color: string
  status?: string
  details?: Record<string, unknown>
}

export interface GraphEdgeSemantic {
  id: string
  source: string
  target: string
  relation: string
  color: string
  dashed?: boolean
}
