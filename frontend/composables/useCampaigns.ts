import type { Campaign, CampaignGraphData, CampaignGraphNode, CampaignGraphEdge } from '~/types/campaign'

const FALLBACK_CAMPAIGNS: Campaign[] = [
  {
    id: 'cmp-ai-discovery-launch-01',
    name: 'Enterprise AI Discovery Launch',
    owner: 'Sarah Jenkins (PMM Lead)',
    status: 'ACTIVE',
    date_range: 'Sep 15, 2026 – Oct 15, 2026',
    channels: ['Search LLMs', 'LinkedIn', 'Developer Docs', 'YouTube'],
    primary_channel: 'Search LLMs & Developer Docs',
    currency: 'USD',
    budget: 50000.0,
    total_cost: 42780.0,
    attributed_return: 118000.0,
    roi: 1.76,
    measurement_confidence: 'MEDIUM',
    cost_completeness_pct: 92,
    outcome_coverage_pct: 74,
    return_sources: {
      direct: 46000.0,
      attributed: 51000.0,
      modeled: 21000.0,
      proxy: '+9.2pp Profound visibility'
    },
    operational_metrics: {
      gross_return: 118000.0,
      net_return: 75220.0,
      cost_per_output: 2037.14,
      cost_per_agent_run: 57.57,
      cost_per_approved_asset: 2415.0,
      cost_per_lead: 2251.58,
      cost_per_ai_visibility_point: 4650.0,
      cost_per_citation_gain: 6684.38,
      agent_roi: {
        ratio: 3.82,
        attribution_label: 'ATTRIBUTED (outputs linked to deal touchpoints)'
      },
      health_dimensions: {
        cost_completeness: 92,
        outcome_coverage: 74,
        attribution_quality: 'Medium',
        ai_discovery_coverage: 'High'
      }
    },
    cost_composition: [
      { category: 'Paid Media', amount: 18000.0, pct: 42.1, source: 'OBSERVED' },
      { category: 'People', amount: 11400.0, pct: 26.6, source: 'ESTIMATED' },
      { category: 'Video Production', amount: 6250.0, pct: 14.6, source: 'ACTUAL' },
      { category: 'Creator Fees', amount: 3500.0, pct: 8.2, source: 'ACTUAL' },
      { category: 'Agent Runs', amount: 2130.0, pct: 5.0, source: 'OBSERVED' },
      { category: 'Tools & Other', amount: 860.0, pct: 2.0, source: 'OBSERVED' },
      { category: 'Model APIs', amount: 640.0, pct: 1.5, source: 'OBSERVED' }
    ],
    cost_lineage: {
      id: 'root-cost',
      name: 'Total Campaign Cost',
      amount: 42780.0,
      children: [
        {
          id: 'c-paid-media',
          name: 'Paid Media',
          amount: 18000.0,
          children: [
            { id: 'c-pm-linkedin', name: 'LinkedIn Sponsored Content', amount: 11500.0 },
            { id: 'c-pm-search', name: 'Technical Search Placements', amount: 6500.0 }
          ]
        },
        {
          id: 'c-people',
          name: 'People (Team & Contractors)',
          amount: 11400.0,
          children: [
            { id: 'c-p-pmm', name: 'Product Marketing Lead (Sarah)', amount: 4200.0, details: '28 hrs @ $150/hr · MANUAL' },
            { id: 'c-p-designer', name: 'Staff Brand Designer (Alex)', amount: 3100.0, details: '21.5 hrs @ $144/hr · MANUAL' },
            { id: 'c-p-eng', name: 'Solutions Engineer (David)', amount: 2600.0, details: '16 hrs @ $162.50/hr · MANUAL' },
            { id: 'c-p-creator-mgr', name: 'Creator Relations Manager (Elena)', amount: 1500.0, details: '15 hrs @ $100/hr · ESTIMATED' }
          ]
        },
        {
          id: 'c-video',
          name: 'Video & Media',
          amount: 6250.0,
          children: [
            { id: 'c-v-prod', name: 'Studio Production & Filming', amount: 3400.0 },
            { id: 'c-v-edit', name: 'Post-Production Editing & Motion', amount: 1800.0 },
            { id: 'c-v-ai', name: 'AI Voice & B-Roll Generation', amount: 1050.0 }
          ]
        },
        {
          id: 'c-creators',
          name: 'Creator & Partner Fees',
          amount: 3500.0,
          children: [
            { id: 'c-cr-deepdive', name: 'Tech Influencer Architectural Review', amount: 3500.0 }
          ]
        },
        {
          id: 'c-agents',
          name: 'Profound & Custom Agent Runs',
          amount: 2130.0,
          children: [
            { id: 'c-ag-citation', name: 'Profound Citation & Ingestion Agent', amount: 1280.0, details: '37 runs · 33 successful' },
            { id: 'c-ag-copy', name: 'Content Structuring Agent', amount: 850.0, details: '18 runs · 15 successful' }
          ]
        },
        {
          id: 'c-model-apis',
          name: 'Model APIs (Anthropic & OpenAI)',
          amount: 640.0,
          children: [
            { id: 'c-api-haiku', name: 'Claude Haiku 4.5 Fast Inference', amount: 210.0 },
            { id: 'c-api-sonnet', name: 'Claude Sonnet 4.6 Deep Reasoning', amount: 430.0 }
          ]
        },
        {
          id: 'c-tools',
          name: 'Software Tools & Hosting',
          amount: 860.0
        }
      ]
    },
    people: [
      {
        id: 'person-1',
        name: 'Sarah Jenkins',
        role: 'PMM Lead',
        hours: 28.0,
        hourly_cost: 150.0,
        total_cost: 4200.0,
        source_quality: 'MANUAL',
        outputs: ['Campaign Brief', 'Landing Page Copy', 'FAQ Schema']
      },
      {
        id: 'person-2',
        name: 'Alex Rivera',
        role: 'Staff Designer',
        hours: 21.5,
        hourly_cost: 144.0,
        total_cost: 3100.0,
        source_quality: 'MANUAL',
        outputs: ['Interactive Architecture Diagram', 'Video Graphics Deck']
      },
      {
        id: 'person-3',
        name: 'David Chen',
        role: 'Solutions Engineer',
        hours: 16.0,
        hourly_cost: 162.5,
        total_cost: 2600.0,
        source_quality: 'MANUAL',
        outputs: ['Docker Gateway Sample', 'Technical Integration Guide']
      },
      {
        id: 'person-4',
        name: 'Elena Rostova',
        role: 'Creator Manager',
        hours: 15.0,
        hourly_cost: 100.0,
        total_cost: 1500.0,
        source_quality: 'ESTIMATED',
        outputs: ['Creator Brief', 'Review Coordination']
      }
    ],
    agents: [
      {
        id: 'agent-profound-citation',
        name: 'Profound Citation Agent',
        runs: 37,
        successful_runs: 33,
        failed_runs: 4,
        tokens: 4200000,
        model_cost: 189.0,
        tool_cost: 312.0,
        total_cost: 501.0,
        outputs_produced: 12,
        approved_outputs: 8,
        approval_rate_pct: 66.7,
        cost_per_approved_output: 62.6
      },
      {
        id: 'agent-content-audit',
        name: 'Canonical Truth Auditor Agent',
        runs: 22,
        successful_runs: 22,
        failed_runs: 0,
        tokens: 2850000,
        model_cost: 124.0,
        tool_cost: 225.0,
        total_cost: 349.0,
        outputs_produced: 9,
        approved_outputs: 9,
        approval_rate_pct: 100.0,
        cost_per_approved_output: 38.8
      }
    ],
    videos: [
      {
        id: 'vid-01',
        title: 'Enterprise SAML & Zero-Downtime Migration Walkthrough',
        total_cost: 4920.0,
        creator_cost: 2300.0,
        editing_cost: 1200.0,
        ai_generation_cost: 700.0,
        distribution_cost: 720.0,
        versions_count: 7,
        published: true,
        views: 48200,
        engagement_pct: 5.4,
        qualified_visits: 3140,
        profound_citations_observed: 14,
        muse_shortlist_events: 28,
        leads_generated: 19
      }
    ],
    assets: [
      {
        id: 'ast-landing-page',
        title: 'Verified Enterprise Migration Center',
        type: 'LandingPage',
        created_by: 'Sarah Jenkins',
        generated_by_agent: 'Canonical Truth Auditor Agent',
        edited_by: 'David Chen',
        reviewed_by: 'PMM Lead',
        approved_by: 'VP Marketing',
        distributed_on: ['Web Canonical', 'LinkedIn'],
        downstream_outcomes: ['+9.2pp Profound visibility', '91 Muse shortlists', '19 Qualified leads']
      },
      {
        id: 'ast-video-01',
        title: 'Video #1: Zero-Downtime Migration Benchmark',
        type: 'Video',
        created_by: 'Alex Rivera',
        generated_by_agent: 'Profound Content Agent',
        edited_by: 'Contract Editor',
        reviewed_by: 'Sarah Jenkins',
        approved_by: 'PMM Lead',
        distributed_on: ['YouTube', 'LinkedIn'],
        downstream_outcomes: ['48.2K Views', '14 Citations']
      }
    ],
    waste_breakdown: {
      potential_inefficiency: 12360.0,
      items: [
        { category: 'REWORK', label: 'Discarded video revisions (v1-v4)', amount: 4600.0 },
        { category: 'DUPLICATE', label: 'Duplicate Agent web crawl runs', amount: 2180.0 },
        { category: 'ABANDONED', label: 'Unused comparison one-pagers', amount: 3200.0 },
        { category: 'REWORK', label: 'Repeated Model API queries from rate limits', amount: 980.0 },
        { category: 'REWORK', label: 'Excessive revision loops on schema copy', amount: 1400.0 }
      ]
    },
    profound_impact: {
      attribution_note: 'Observed after campaign across 14 prompt clusters',
      visibility_shift_pp: 9.2,
      citation_share_shift_pp: 6.4,
      prompt_coverage_pct: 78,
      competitor_share_shift_pp: -8.1,
      ai_perception_status: 'Contradiction resolved in Claude & ChatGPT citations',
      affected_clusters_count: 14
    },
    muse_outcomes: {
      matched_intents: 312,
      shortlisted: 91,
      details_requested: 37,
      converted: 8,
      funnel: [
        { step: 'Personal Agent Demand', count: 312 },
        { step: 'Profound Perception Aligned', count: 218 },
        { step: 'AgentMatch Surfaced', count: 144 },
        { step: 'Shortlisted by Buyer Agent', count: 91 },
        { step: 'Technical Details Requested', count: 37 },
        { step: 'Conversion / Closed Won', count: 8 }
      ]
    },
    roi_confidence_breakdown: {
      overall: 'MEDIUM',
      cost_completeness: 92,
      revenue_coverage: 84,
      people_cost_quality: 'ESTIMATED',
      profound_contribution: 'Associated observation (+9.2pp visibility)',
      muse_feedback: 'Observed real agent interaction telemetry',
      known_costs: ['Paid media', 'Agents', 'Videos', 'Tools & Hosting'],
      partial_costs: ['People internal hourly rates'],
      missing_costs: ['Creator agency ancillary travel invoice #4']
    },
    timeline: [
      { time: 'Sep 15, 09:14', event: 'Campaign budget & brief initialized ($50K budget)', type: 'budget' },
      { time: 'Sep 16, 11:20', event: 'Agent run started: Canonical claim extraction', type: 'agent' },
      { time: 'Sep 18, 14:45', event: 'Landing Page Copy drafted & reviewed by PMM Lead', type: 'asset' },
      { time: 'Sep 22, 10:12', event: 'Video #1 rendered and approved by PMM Lead', type: 'video' },
      { time: 'Sep 23, 08:00', event: 'Campaign launched on LinkedIn and Search', type: 'launch' },
      { time: 'Sep 27, 16:30', event: 'Profound observed +9.2pp visibility shift', type: 'profound' },
      { time: 'Oct 01, 13:10', event: 'Muse recorded 91st shortlist event', type: 'muse' },
      { time: 'Oct 02, 18:40', event: 'First enterprise deal closed ($46,000 direct revenue)', type: 'revenue' }
    ]
  },
  {
    id: 'cmp-security-blitz-02',
    name: 'Q3 SAML & Security Verification Blitz',
    owner: 'David Chen (Solutions Eng)',
    status: 'COMPLETED',
    date_range: 'Aug 01, 2026 – Aug 31, 2026',
    channels: ['Security Documentation', 'Trust Portal', 'Profound Ingestion'],
    primary_channel: 'Trust Portal & Documentation',
    currency: 'USD',
    budget: 20000.0,
    total_cost: 18400.0,
    attributed_return: 54200.0,
    roi: 1.95,
    measurement_confidence: 'HIGH',
    cost_completeness_pct: 98,
    outcome_coverage_pct: 88,
    return_sources: {
      direct: 32000.0,
      attributed: 22200.0,
      modeled: 0.0,
      proxy: '+14.0pp Profound citation share'
    },
    operational_metrics: {
      gross_return: 54200.0,
      net_return: 35800.0,
      cost_per_output: 1226.67,
      cost_per_agent_run: 121.43,
      cost_per_approved_asset: 18400.0,
      cost_per_lead: 3066.67,
      cost_per_ai_visibility_point: 1483.87,
      cost_per_citation_gain: 1314.29,
      agent_roi: {
        ratio: 4.65,
        attribution_label: 'DIRECT (schema adoption led to enterprise deal)'
      },
      health_dimensions: {
        cost_completeness: 98,
        outcome_coverage: 88,
        attribution_quality: 'High',
        ai_discovery_coverage: 'High'
      }
    },
    cost_composition: [
      { category: 'People', amount: 9200.0, pct: 50.0, source: 'ACTUAL' },
      { category: 'Agent Runs', amount: 3400.0, pct: 18.5, source: 'OBSERVED' },
      { category: 'Paid Media', amount: 3000.0, pct: 16.3, source: 'ACTUAL' },
      { category: 'Tools & Hosting', amount: 1800.0, pct: 9.8, source: 'ACTUAL' },
      { category: 'Model APIs', amount: 1000.0, pct: 5.4, source: 'OBSERVED' }
    ],
    cost_lineage: {
      id: 'root-cost-2',
      name: 'Total Campaign Cost',
      amount: 18400.0,
      children: [
        { id: 'c2-people', name: 'Security & Eng Staff', amount: 9200.0 },
        { id: 'c2-agents', name: 'Documentation & Schema Agents', amount: 3400.0 },
        { id: 'c2-paid', name: 'Technical Syndicate Placements', amount: 3000.0 },
        { id: 'c2-tools', name: 'Security Portal Hosting', amount: 1800.0 },
        { id: 'c2-api', name: 'Model APIs', amount: 1000.0 }
      ]
    },
    people: [
      {
        id: 'person-sec-1',
        name: 'David Chen',
        role: 'Solutions Engineer',
        hours: 32.0,
        hourly_cost: 162.5,
        total_cost: 5200.0,
        source_quality: 'ACTUAL',
        outputs: ['Security Whitepaper', 'JSON-LD Trust Matrix']
      },
      {
        id: 'person-sec-2',
        name: 'Marcus Vance',
        role: 'Security Architect',
        hours: 20.0,
        hourly_cost: 200.0,
        total_cost: 4000.0,
        source_quality: 'ACTUAL',
        outputs: ['SOC2 Evidence Pack', 'SAML Spec Review']
      }
    ],
    agents: [
      {
        id: 'agent-sec-audit',
        name: 'Security Schema Synthesizer',
        runs: 28,
        successful_runs: 28,
        failed_runs: 0,
        tokens: 3100000,
        model_cost: 160.0,
        tool_cost: 240.0,
        total_cost: 400.0,
        outputs_produced: 14,
        approved_outputs: 14,
        approval_rate_pct: 100.0,
        cost_per_approved_output: 28.5
      }
    ],
    videos: [],
    assets: [
      {
        id: 'ast-sec-portal',
        title: 'Canonical Security & Compliance Portal',
        type: 'TrustPortal',
        created_by: 'David Chen',
        generated_by_agent: 'Security Schema Synthesizer',
        edited_by: 'Marcus Vance',
        reviewed_by: 'Security Architect',
        approved_by: 'CISO',
        distributed_on: ['trust.acme.example', 'Profound Ingest'],
        downstream_outcomes: ['+14.0pp Citation share', '$32K Direct deal signed']
      }
    ],
    waste_breakdown: {
      potential_inefficiency: 1800.0,
      items: [
        { category: 'REWORK', label: 'Schema re-indexing after domain redirect', amount: 1800.0 }
      ]
    },
    profound_impact: {
      attribution_note: 'Verified experiment outcome across 8 security prompt clusters',
      visibility_shift_pp: 12.4,
      citation_share_shift_pp: 14.0,
      prompt_coverage_pct: 92,
      competitor_share_shift_pp: -11.2,
      ai_perception_status: 'All AI synthesizers now cite official SAML tier docs',
      affected_clusters_count: 8
    },
    muse_outcomes: {
      matched_intents: 184,
      shortlisted: 76,
      details_requested: 42,
      converted: 6,
      funnel: [
        { step: 'Personal Agent Demand', count: 184 },
        { step: 'Security Constraints Checked', count: 160 },
        { step: 'Shortlisted by Buyer Agent', count: 76 },
        { step: 'Direct Conversion', count: 6 }
      ]
    },
    roi_confidence_breakdown: {
      overall: 'HIGH',
      cost_completeness: 98,
      revenue_coverage: 88,
      people_cost_quality: 'ACTUAL',
      profound_contribution: 'Verified experiment (+14.0pp citation share)',
      muse_feedback: 'Observed verified agent shortlist events',
      known_costs: ['All contractor and internal hours logged', 'Direct cloud bills'],
      partial_costs: [],
      missing_costs: []
    },
    timeline: [
      { time: 'Aug 01, 10:00', event: 'Campaign initialized with $20K budget', type: 'budget' },
      { time: 'Aug 10, 14:00', event: 'Security Whitepaper & Schema published', type: 'asset' },
      { time: 'Aug 18, 12:00', event: 'Profound verified +14pp citation share', type: 'profound' },
      { time: 'Aug 29, 16:30', event: '$32,000 direct expansion closed', type: 'revenue' }
    ]
  }
]

export function useCampaigns() {
  const campaigns = ref<Campaign[]>(FALLBACK_CAMPAIGNS)
  const selectedCampaignId = ref<string>(FALLBACK_CAMPAIGNS[0]?.id || '')
  const graphData = ref<CampaignGraphData | null>(null)
  const highlightedPathNodeIds = ref<string[]>([])
  const highlightedPathEdgeIds = ref<string[]>([])

  const selectedCampaign = computed<Campaign | null>(() => {
    return campaigns.value.find(c => c.id === selectedCampaignId.value) || campaigns.value[0] || null
  })

  const selectCampaign = async (id: string) => {
    selectedCampaignId.value = id
    clearHighlights()
    await loadCampaignGraph(id)
  }

  // Load from backend API if reachable
  const loadCampaignsFromApi = async () => {
    try {
      const config = useRuntimeConfig()
      const apiBase = config.public?.apiBaseUrl || ''
      const res = await $fetch<{ campaigns: Campaign[] }>(`${apiBase}/api/campaigns`)
      if (res && res.campaigns && res.campaigns.length > 0) {
        campaigns.value = res.campaigns
      }
    } catch {
      // Use resilient fallback data
    }
  }

  const loadCampaignGraph = async (campaignId: string) => {
    try {
      const config = useRuntimeConfig()
      const apiBase = config.public?.apiBaseUrl || ''
      const res = await $fetch<CampaignGraphData>(`${apiBase}/api/campaigns/${campaignId}/graph`)
      if (res && res.nodes && res.edges) {
        graphData.value = res
        return
      }
    } catch {
      // Build local fallback graph
    }

    const c = selectedCampaign.value
    if (!c) return

    const nodes: CampaignGraphNode[] = [
      { id: c.id, label: c.name, type: 'campaign', z: 0, color: '#6366f1', amount: c.total_cost, status: c.status },
      { id: fCost(c.id, 'media'), label: 'Paid Media ($18.0K)', type: 'cost', z: -12, color: '#f59e0b', amount: 18000.0 },
      { id: fCost(c.id, 'people'), label: 'People ($11.4K)', type: 'cost', z: -12, color: '#f59e0b', amount: 11400.0 },
      { id: fCost(c.id, 'agents'), label: 'Agents & APIs ($2.8K)', type: 'cost', z: -12, color: '#f59e0b', amount: 2770.0 },
      { id: 'node-person-sarah', label: 'Sarah Jenkins (PMM)', type: 'person', z: -8, color: '#38bdf8', amount: 4200.0 },
      { id: 'node-person-alex', label: 'Alex Rivera (Designer)', type: 'person', z: -8, color: '#38bdf8', amount: 3100.0 },
      { id: 'node-agent-profound', label: 'Profound Citation Agent', type: 'agent', z: -8, color: '#818cf8', amount: 501.0 },
      { id: 'node-asset-landing', label: 'Verified Migration Page', type: 'asset', z: -3, color: '#22d3ee' },
      { id: 'node-asset-video', label: 'Zero-Downtime Video #1', type: 'video', z: -3, color: '#22d3ee' },
      { id: 'node-dist-linkedin', label: 'LinkedIn Channel', type: 'channel', z: 5, color: '#60a5fa' },
      { id: 'node-dist-web', label: 'Canonical Web Index', type: 'channel', z: 5, color: '#60a5fa' },
      { id: 'node-sig-profound', label: 'Profound: +9.2pp Visibility', type: 'profound_signal', z: 9, color: '#a855f7' },
      { id: 'node-sig-muse', label: 'Muse: 91 Shortlists', type: 'muse_interaction', z: 9, color: '#2dd4bf' },
      { id: 'node-outcome-leads', label: '19 Qualified Leads ($51K)', type: 'lead', z: 13, color: '#10b981', amount: 51000.0, confidence: 'ATTRIBUTED' },
      { id: 'node-outcome-revenue', label: '$46K Direct Won Revenue', type: 'revenue', z: 13, color: '#10b981', amount: 46000.0, confidence: 'DIRECT' }
    ]

    const edges: CampaignGraphEdge[] = [
      { id: 'e-cost-1', source: fCost(c.id, 'media'), target: c.id, relation: 'COST_OF', color: '#f59e0b', weight: 4 },
      { id: 'e-cost-2', source: fCost(c.id, 'people'), target: c.id, relation: 'COST_OF', color: '#f59e0b', weight: 3 },
      { id: 'e-cost-3', source: fCost(c.id, 'agents'), target: c.id, relation: 'COST_OF', color: '#f59e0b', weight: 2 },
      { id: 'e-p1', source: 'node-person-sarah', target: c.id, relation: 'CONTRIBUTED_TO', color: '#38bdf8' },
      { id: 'e-p2', source: 'node-person-alex', target: c.id, relation: 'CONTRIBUTED_TO', color: '#38bdf8' },
      { id: 'e-ag1', source: 'node-agent-profound', target: c.id, relation: 'CONTRIBUTED_TO', color: '#818cf8' },
      { id: 'e-prod-1', source: 'node-person-sarah', target: 'node-asset-landing', relation: 'PRODUCED', color: '#22d3ee' },
      { id: 'e-prod-2', source: 'node-person-alex', target: 'node-asset-video', relation: 'PRODUCED', color: '#22d3ee' },
      { id: 'e-prod-3', source: 'node-agent-profound', target: 'node-asset-landing', relation: 'GENERATED', color: '#818cf8' },
      { id: 'e-dist-1', source: 'node-asset-landing', target: 'node-dist-web', relation: 'DISTRIBUTED', color: '#60a5fa' },
      { id: 'e-dist-2', source: 'node-asset-video', target: 'node-dist-linkedin', relation: 'DISTRIBUTED', color: '#60a5fa' },
      { id: 'e-sig-1', source: 'node-dist-web', target: 'node-sig-profound', relation: 'AFFECTED', color: '#a855f7' },
      { id: 'e-sig-2', source: 'node-dist-web', target: 'node-sig-muse', relation: 'AFFECTED', color: '#2dd4bf' },
      { id: 'e-out-1', source: 'node-sig-muse', target: 'node-outcome-leads', relation: 'RESULTED_IN', color: '#10b981', confidence: 'ATTRIBUTED', dashed: true },
      { id: 'e-out-2', source: 'node-outcome-leads', target: 'node-outcome-revenue', relation: 'RESULTED_IN', color: '#10b981', confidence: 'DIRECT' }
    ]

    graphData.value = {
      nodes,
      edges,
      summary: {
        total_cost: c.total_cost,
        return: c.attributed_return,
        roi: c.roi,
        measurement_confidence: c.measurement_confidence
      }
    }
  }

  function fCost(cid: string, suffix: string) {
    return `cost-${suffix}-${cid}`
  }

  // Path Highlighting
  const highlightOutcomePath = () => {
    const c = selectedCampaign.value
    if (!c) return
    highlightedPathNodeIds.value = [
      c.id,
      'node-asset-landing',
      'node-dist-web',
      'node-sig-profound',
      'node-sig-muse',
      'node-outcome-leads',
      'node-outcome-revenue'
    ]
    highlightedPathEdgeIds.value = [
      'e-dist-1',
      'e-sig-1',
      'e-sig-2',
      'e-out-1',
      'e-out-2'
    ]
  }

  const highlightCostPath = (categoryKey?: string) => {
    const c = selectedCampaign.value
    if (!c) return
    if (categoryKey === 'agents' || categoryKey?.includes('agent')) {
      highlightedPathNodeIds.value = [
        c.id,
        fCost(c.id, 'agents'),
        'node-agent-profound',
        'node-asset-landing'
      ]
      highlightedPathEdgeIds.value = ['e-cost-3', 'e-ag1', 'e-prod-3']
    } else if (categoryKey === 'people' || categoryKey?.includes('people')) {
      highlightedPathNodeIds.value = [
        c.id,
        fCost(c.id, 'people'),
        'node-person-sarah',
        'node-person-alex',
        'node-asset-landing',
        'node-asset-video'
      ]
      highlightedPathEdgeIds.value = ['e-cost-2', 'e-p1', 'e-p2', 'e-prod-1', 'e-prod-2']
    } else {
      highlightedPathNodeIds.value = [
        c.id,
        fCost(c.id, 'media'),
        fCost(c.id, 'people'),
        fCost(c.id, 'agents')
      ]
      highlightedPathEdgeIds.value = ['e-cost-1', 'e-cost-2', 'e-cost-3']
    }
  }

  const clearHighlights = () => {
    highlightedPathNodeIds.value = []
    highlightedPathEdgeIds.value = []
  }

  onMounted(() => {
    loadCampaignsFromApi()
    loadCampaignGraph(selectedCampaignId.value)
  })

  return {
    campaigns,
    selectedCampaignId,
    selectedCampaign,
    graphData,
    highlightedPathNodeIds,
    highlightedPathEdgeIds,
    selectCampaign,
    loadCampaignGraph,
    highlightOutcomePath,
    highlightCostPath,
    clearHighlights
  }
}
