// CampaignGraph ROI Domain Types

export type CampaignStatus = 'DRAFT' | 'ACTIVE' | 'MEASURING' | 'COMPLETED' | 'PAUSED' | 'CANCELLED'

export type MeasurementConfidence = 'HIGH' | 'MEDIUM' | 'LOW' | 'UNKNOWN'

export type CostSourceType = 'ACTUAL' | 'MANUAL' | 'ESTIMATED' | 'OBSERVED'

export interface CostCompositionItem {
  category: string
  amount: number
  pct: number
  source: CostSourceType
}

export interface CostLineageNode {
  id: string
  name: string
  amount: number
  children?: CostLineageNode[]
  details?: string
}

export interface PersonCost {
  id: string
  name: string
  role: string
  hours: number
  hourly_cost: number
  total_cost: number
  source_quality: CostSourceType
  outputs: string[]
}

export interface AgentCost {
  id: string
  name: string
  runs: number
  successful_runs: number
  failed_runs: number
  tokens: number
  model_cost: number
  tool_cost: number
  total_cost: number
  outputs_produced: number
  approved_outputs: number
  approval_rate_pct: number
  cost_per_approved_output: number
}

export interface VideoCost {
  id: string
  title: string
  total_cost: number
  creator_cost: number
  editing_cost: number
  ai_generation_cost: number
  distribution_cost: number
  versions_count: number
  published: boolean
  views: number
  engagement_pct: number
  qualified_visits: number
  profound_citations_observed: number
  muse_shortlist_events: number
  leads_generated: number
}

export interface AssetLineageItem {
  id: string
  title: string
  type: string
  created_by: string
  generated_by_agent?: string | null
  edited_by?: string | null
  reviewed_by?: string | null
  approved_by?: string | null
  distributed_on: string[]
  downstream_outcomes: string[]
}

export type InefficiencyCategory = 'PRODUCTIVE' | 'EXPLORATORY' | 'REWORK' | 'DUPLICATE' | 'ABANDONED' | 'UNKNOWN'

export interface WasteItem {
  category: InefficiencyCategory
  label: string
  amount: number
}

export interface WasteBreakdown {
  potential_inefficiency: number
  items: WasteItem[]
}

export interface ProfoundImpact {
  attribution_note: string
  visibility_shift_pp: number
  citation_share_shift_pp: number
  prompt_coverage_pct: number
  competitor_share_shift_pp: number
  ai_perception_status: string
  affected_clusters_count: number
}

export interface MuseFunnelStep {
  step: string
  count: number
}

export interface MuseOutcomes {
  matched_intents: number
  shortlisted: number
  details_requested: number
  converted: number
  funnel: MuseFunnelStep[]
}

export interface RoiConfidenceBreakdown {
  overall: MeasurementConfidence
  cost_completeness: number
  revenue_coverage: number
  people_cost_quality: string
  profound_contribution: string
  muse_feedback: string
  known_costs: string[]
  partial_costs: string[]
  missing_costs: string[]
}

export interface TimelineEvent {
  time: string
  event: string
  type: string
}

export interface ReturnSources {
  direct: number
  attributed: number
  modeled: number
  proxy?: string
}

export interface OperationalMetrics {
  gross_return: number
  net_return: number
  cost_per_output: number
  cost_per_agent_run: number
  cost_per_approved_asset: number
  cost_per_lead: number
  cost_per_ai_visibility_point: number | null
  cost_per_citation_gain: number | null
  agent_roi: {
    ratio: number
    attribution_label: string
  }
  health_dimensions: {
    cost_completeness: number
    outcome_coverage: number
    attribution_quality: string
    ai_discovery_coverage: string
  }
}

export interface Campaign {
  id: string
  name: string
  owner: string
  status: CampaignStatus
  date_range: string
  channels: string[]
  primary_channel: string
  currency: string
  budget: number
  total_cost: number
  attributed_return: number
  roi: number | null
  measurement_confidence: MeasurementConfidence
  cost_completeness_pct: number
  outcome_coverage_pct: number
  return_sources: ReturnSources
  operational_metrics: OperationalMetrics
  cost_composition: CostCompositionItem[]
  cost_lineage: CostLineageNode
  people: PersonCost[]
  agents: AgentCost[]
  videos: VideoCost[]
  assets: AssetLineageItem[]
  waste_breakdown: WasteBreakdown
  profound_impact: ProfoundImpact
  muse_outcomes: MuseOutcomes
  roi_confidence_breakdown: RoiConfidenceBreakdown
  timeline: TimelineEvent[]
}

export interface CampaignGraphNode {
  id: string
  label: string
  type: 'campaign' | 'cost' | 'person' | 'agent' | 'asset' | 'video' | 'channel' | 'profound_signal' | 'muse_interaction' | 'lead' | 'revenue'
  z: number
  color: string
  amount?: number
  status?: string
  confidence?: 'DIRECT' | 'ATTRIBUTED' | 'MODELED' | 'PROXY'
}

export interface CampaignGraphEdge {
  id: string
  source: string
  target: string
  relation: string
  color: string
  weight?: number
  dashed?: boolean
  confidence?: 'DIRECT' | 'ATTRIBUTED' | 'MODELED' | 'PROXY'
}

export interface CampaignGraphData {
  nodes: CampaignGraphNode[]
  edges: CampaignGraphEdge[]
  summary: {
    total_cost: number
    return: number
    roi: number | null
    measurement_confidence: MeasurementConfidence
  }
}
