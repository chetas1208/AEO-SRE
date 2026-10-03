import type { IntentEnvelope, MatchCandidate, GraphNodeSemantic, GraphEdgeSemantic } from '~/types/agentmatch'

const DEFAULT_INTENTS: IntentEnvelope[] = [
  {
    id: 'intent-crm-migration-01',
    intent: 'Enterprise CRM Migration',
    goal: 'Find enterprise CRM for scaling go-to-market and partner channels',
    source: 'MUSE',
    updatedAt: new Date(Date.now() - 14000).toISOString(),
    expiresInSeconds: 1620, // 27 min
    expiresAt: new Date(Date.now() + 1620000).toISOString(),
    canonicalClaimsConsidered: 18,
    discoveryGapsCount: 2,
    preferences: [
      'Prefer SOC2 certified vendors with modern REST/GraphQL APIs',
      'Prefer established migration toolkits from HubSpot'
    ],
    constraints: [
      {
        id: 'c-1',
        statement: 'SAML 2.0 and SCIM user provisioning',
        status: 'satisfied',
        aiPerception: 'contradicts',
        discoveryGap: true,
        reasons: ['Product truth supports SAML on Business & Enterprise tiers; AI perception states Enterprise-only.'],
        basis: [{
          key: 'sso_saml_availability',
          statement: 'SAML 2.0 SSO is available on all Business and Enterprise plans with SCIM sync.',
          source: 'https://docs.example.com/security/sso',
          relation: 'supports'
        }],
        perceptionEvidence: ['Comparison review from early 2024 claimed SAML requires $50k custom tier.']
      },
      {
        id: 'c-2',
        statement: 'EU data residency and GDPR compliance',
        status: 'satisfied',
        aiPerception: 'supports',
        discoveryGap: false,
        reasons: ['EU region (Frankfurt & Dublin) verified active.'],
        basis: [{
          key: 'data_residency_eu',
          statement: 'Data centers located in Frankfurt (eu-central-1) and Dublin provide full GDPR isolation.',
          source: 'https://trust.example.com/residency',
          relation: 'supports'
        }]
      },
      {
        id: 'c-3',
        statement: 'Annual contract under $150K for 500 seats',
        status: 'satisfied',
        aiPerception: 'supports',
        discoveryGap: false,
        reasons: ['Standard Business pricing at $18/seat/mo = $108k/year, well within $150k limit.'],
        basis: [{
          key: 'pricing_business_tier',
          statement: 'Business tier pricing is $18 per user per month billed annually.',
          source: 'https://example.com/pricing',
          relation: 'supports'
        }]
      },
      {
        id: 'c-4',
        statement: 'Zero-downtime migration toolkit from HubSpot',
        status: 'satisfied',
        aiPerception: 'contradicts',
        discoveryGap: true,
        reasons: ['Automated HubSpot pipeline sync launched Q3; AI answers cite manual CSV export only.'],
        basis: [{
          key: 'hubspot_migration_tool',
          statement: 'Native HubSpot 1-click schema and record migrator with delta replay.',
          source: 'https://docs.example.com/migrations/hubspot',
          relation: 'supports'
        }],
        perceptionEvidence: ['Top LLM summary cites 2023 forum post stating HubSpot migrations take weeks of manual work.']
      },
      {
        id: 'c-5',
        statement: 'Implementation timeline < 90 days',
        status: 'satisfied',
        aiPerception: 'supports',
        discoveryGap: false,
        reasons: ['Median onboarding duration across 500-seat cohorts is 41 days.'],
        basis: [{
          key: 'onboarding_slas',
          statement: 'Enterprise onboarding package guarantees production rollout within 60 days.',
          source: 'https://example.com/enterprise-sla',
          relation: 'supports'
        }]
      }
    ]
  },
  {
    id: 'intent-analytics-02',
    intent: 'Product Analytics & Event Streaming',
    goal: 'Select real-time product analytics platform with self-hosted ingestion option',
    source: 'TEST',
    updatedAt: new Date(Date.now() - 45000).toISOString(),
    expiresInSeconds: 3200,
    expiresAt: new Date(Date.now() + 3200000).toISOString(),
    canonicalClaimsConsidered: 14,
    discoveryGapsCount: 1,
    preferences: ['Kafka ingestion support', 'SOC2 Type II certified'],
    constraints: [
      {
        id: 'c-an-1',
        statement: 'Sub-second funnel queries over 100M events',
        status: 'satisfied',
        aiPerception: 'supports',
        discoveryGap: false,
        reasons: ['ClickHouse cluster benchmarked at 280ms p95 for 120M events.'],
        basis: [{
          key: 'benchmark_funnel_p95',
          statement: 'Distributed ClickHouse storage executes 100M+ event funnel queries in under 300ms.',
          source: 'https://docs.example.com/benchmarks',
          relation: 'supports'
        }]
      },
      {
        id: 'c-an-2',
        statement: 'Self-hosted gateway or edge proxy available',
        status: 'satisfied',
        aiPerception: 'contradicts',
        discoveryGap: true,
        reasons: ['Open-source Apache 2.0 ingestion proxy exists, but LLM engines report cloud-only.'],
        basis: [{
          key: 'edge_proxy_oss',
          statement: 'Stateless edge event gateway is available as an open-source Docker container and Helm chart.',
          source: 'https://github.com/example/gateway',
          relation: 'supports'
        }],
        perceptionEvidence: ['Synthesizers claim no on-premise components are supported.']
      }
    ]
  }
]

const CANDIDATES_BY_INTENT: Record<string, MatchCandidate[]> = {
  'intent-crm-migration-01': [
    {
      id: 'prod-acme-crm',
      productName: 'Acme CRM Enterprise',
      vendor: 'Acme Software Inc.',
      actualFitPct: 94,
      aiPerceivedFitPct: 61,
      gapPp: 33,
      state: 'DISCOVERY_GAP',
      satisfiedConstraintsCount: 5,
      totalConstraintsCount: 5,
      evidenceConfidence: 0.94,
      summary: 'Matches all 5 required enterprise constraints. However, AI engines underreport tier pricing and native HubSpot migrator capabilities, penalizing discovery.',
      whyItMatches: 'Canonical documentation confirms SAML 2.0 on Business tier, EU region in Frankfurt, pricing at $108k/yr ($18/seat), automated HubSpot 1-click migrator, and 41-day median onboarding.',
      whyAiMissesIt: 'ChatGPT and Claude cite a stale 2024 third-party review claiming SAML requires a custom $50k enterprise contract, and perplexity references a 2023 forum thread saying HubSpot migrations are manual.',
      marketingGap: 'High-intent buyer agents searching for affordable SAML CRM discard Acme prematurely because LLM synthesis produces incorrect tier gating claims.',
      recommendedAction: 'Deploy verified canonical SAML pricing page and structured FAQ; trigger Profound re-crawl to displace outdated third-party blog citations.',
      supportingEvidence: [
        { claim: 'SAML 2.0 on Business plan verified in docs', source: 'https://docs.example.com/security/sso', status: 'verified', timestamp: '2026-10-01' },
        { claim: 'Native HubSpot 1-Click migrator release notes', source: 'https://docs.example.com/migrations/hubspot', status: 'verified', timestamp: '2026-09-15' },
        { claim: 'Frankfurt EU region isolation certified', source: 'https://trust.example.com/residency', status: 'verified', timestamp: '2026-08-20' }
      ]
    },
    {
      id: 'prod-nexus-flow',
      productName: 'Nexus Flow CRM',
      vendor: 'Nexus Systems',
      actualFitPct: 78,
      aiPerceivedFitPct: 82,
      gapPp: -4,
      state: 'HEALTHY_MATCH',
      satisfiedConstraintsCount: 4,
      totalConstraintsCount: 5,
      evidenceConfidence: 0.86,
      summary: 'Strong AI visibility across major LLMs, but onboarding duration exceeds the required 90-day SLA.',
      whyItMatches: 'Offers robust SAML support and EU hosting, but migration from HubSpot requires 120+ days of professional services.',
      whyAiMissesIt: 'No discovery gap; AI perception matches or slightly overstates actual onboarding agility.',
      marketingGap: 'Competitor enjoys high LLM recall despite slower implementation velocity.',
      recommendedAction: 'Benchmark Acme onboarding speed against Nexus Flow in buyer comparison collaterals.',
      supportingEvidence: [
        { claim: 'Standard onboarding contract states 120-day implementation SLA', source: 'https://nexus.example.com/sla', status: 'verified', timestamp: '2026-07-10' }
      ]
    }
  ],
  'intent-analytics-02': [
    {
      id: 'prod-dataflow',
      productName: 'DataFlow Realtime Analytics',
      vendor: 'DataFlow Systems',
      actualFitPct: 91,
      aiPerceivedFitPct: 58,
      gapPp: 33,
      state: 'DISCOVERY_GAP',
      satisfiedConstraintsCount: 2,
      totalConstraintsCount: 2,
      evidenceConfidence: 0.92,
      summary: 'Supports both 100M event sub-second queries and open-source gateway, but LLMs falsely report no self-hosted option exists.',
      whyItMatches: 'ClickHouse architecture delivers benchmarked 280ms funnel queries and Kafka gateway is Apache 2.0.',
      whyAiMissesIt: 'SaaS documentation overshadows developer GitHub repository in public crawler indexes.',
      marketingGap: 'AI agents looking for hybrid-cloud solutions filter DataFlow out prematurely.',
      recommendedAction: 'Deploy structured schema markup referencing the open-source gateway repository.',
      supportingEvidence: [
        { claim: 'Kafka Gateway Apache 2.0 release', source: 'https://github.com/example/gateway', status: 'verified', timestamp: '2026-09-28' }
      ]
    }
  ]
}

export function useMatches() {
  const live = useLiveSystemStore()
  const envelopes = ref<IntentEnvelope[]>(DEFAULT_INTENTS)
  const selectedIntentId = ref<string>(DEFAULT_INTENTS[0]?.id || '')

  const selectedIntent = computed(() => {
    return envelopes.value.find(e => e.id === selectedIntentId.value) || envelopes.value[0] || null
  })

  const candidates = computed<MatchCandidate[]>(() => {
    return CANDIDATES_BY_INTENT[selectedIntentId.value] || []
  })

  const selectedCandidateId = ref<string>(candidates.value[0]?.id || '')

  watch(selectedIntentId, (id) => {
    const list = CANDIDATES_BY_INTENT[id] || []
    if (list.length > 0 && list[0]) {
      selectedCandidateId.value = list[0].id
    }
  }, { immediate: true })

  const selectedCandidate = computed(() => {
    return candidates.value.find(c => c.id === selectedCandidateId.value) || candidates.value[0] || null
  })

  const selectIntent = (id: string) => {
    selectedIntentId.value = id
  }

  const selectCandidate = (id: string) => {
    selectedCandidateId.value = id
  }

  // Causal DAG nodes & edges with strict semantic z-depth layers:
  // z=-8 Intent, z=-4 Constraints, z=0 Products, z=+4 Product Truth, z=+7 AI Perception, z=+10 Discovery Gap
  const nodes = computed<GraphNodeSemantic[]>(() => {
    const list: GraphNodeSemantic[] = []
    const intent = selectedIntent.value
    const cand = selectedCandidate.value
    if (!intent) return list

    // Layer 1: Intent (z=-8)
    list.push({
      id: intent.id,
      label: intent.intent,
      type: 'intent',
      z: -8,
      color: '#38bdf8',
      status: `${intent.source} Source`
    })

    // Layer 2: Constraints (z=-4)
    intent.constraints.forEach(c => {
      list.push({
        id: c.id,
        label: c.statement,
        type: 'constraint',
        z: -4,
        color: c.status === 'satisfied' ? '#60a5fa' : '#f43f5e',
        status: c.status
      })
    })

    // Layer 3: Product Candidate (z=0)
    if (cand) {
      list.push({
        id: cand.id,
        label: cand.productName,
        type: 'product',
        z: 0,
        color: '#a78bfa',
        status: `${cand.actualFitPct}% Actual Fit`
      })

      // Layer 4: Verified Product Truth Claims (z=+4)
      intent.constraints.forEach(c => {
        const firstBasis = c.basis[0]
        if (firstBasis) {
          list.push({
            id: `basis-${c.id}`,
            label: firstBasis.key || firstBasis.statement.slice(0, 30),
            type: 'claim',
            z: 4,
            color: '#34d399',
            status: 'Verified Canonical Fact'
          })
        }
      })

      // Layer 5: AI Perception Citations (z=+7)
      intent.constraints.forEach(c => {
        if (c.perceptionEvidence?.length) {
          list.push({
            id: `ai-${c.id}`,
            label: `AI Synthesis: ${c.perceptionEvidence[0]?.slice(0, 26)}…`,
            type: 'ai_claim',
            z: 7,
            color: '#818cf8',
            status: 'Profound Citation'
          })
        }
      })

      // Layer 6: Discovery Gap (z=+10)
      if (cand.gapPp > 0) {
        list.push({
          id: `gap-${cand.id}`,
          label: `+${cand.gapPp}pp Discovery Gap`,
          type: 'discovery_gap',
          z: 10,
          color: '#f43f5e',
          status: 'Brand Misunderstood by AI'
        })
      }
    }

    return list
  })

  const edges = computed<GraphEdgeSemantic[]>(() => {
    const list: GraphEdgeSemantic[] = []
    const intent = selectedIntent.value
    const cand = selectedCandidate.value
    if (!intent) return list

    // Intent -> Constraints
    intent.constraints.forEach(c => {
      list.push({
        id: `e-${intent.id}-${c.id}`,
        source: intent.id,
        target: c.id,
        relation: 'evaluates',
        color: '#38bdf8'
      })
    })

    // Constraints -> Product
    if (cand) {
      intent.constraints.forEach(c => {
        list.push({
          id: `e-${c.id}-${cand.id}`,
          source: c.id,
          target: cand.id,
          relation: 'satisfies',
          color: '#60a5fa'
        })

        // Product -> Canonical Claims
        if (c.basis.length > 0) {
          list.push({
            id: `e-${cand.id}-basis-${c.id}`,
            source: cand.id,
            target: `basis-${c.id}`,
            relation: 'asserts_truth',
            color: '#34d399'
          })
        }

        // Constraint -> AI Perception
        if (c.perceptionEvidence?.length) {
          list.push({
            id: `e-${c.id}-ai-${c.id}`,
            source: c.id,
            target: `ai-${c.id}`,
            relation: 'ai_perceives',
            color: '#818cf8',
            dashed: true
          })

          // AI Perception -> Discovery Gap
          if (cand.gapPp > 0) {
            list.push({
              id: `e-ai-${c.id}-gap`,
              source: `ai-${c.id}`,
              target: `gap-${cand.id}`,
              relation: 'diverges_from_truth',
              color: '#f43f5e',
              dashed: true
            })
          }
        }
      })
    }

    return list
  })

  const addCustomIntent = (envelope: Partial<IntentEnvelope>) => {
    const id = `intent-${Date.now()}`
    const fullEnvelope: IntentEnvelope = {
      id,
      intent: envelope.intent || 'Custom Agent Request',
      goal: envelope.goal || 'Evaluate product capabilities',
      source: 'MANUAL',
      updatedAt: new Date().toISOString(),
      expiresInSeconds: 1800,
      expiresAt: new Date(Date.now() + 1800000).toISOString(),
      canonicalClaimsConsidered: 8,
      discoveryGapsCount: 1,
      preferences: envelope.preferences || [],
      constraints: envelope.constraints || []
    }
    envelopes.value.unshift(fullEnvelope)
    selectedIntentId.value = id
  }

  // Refresh when SSE reports heartbeat
  watch(() => live.lastHeartbeatAt, () => {
    envelopes.value.forEach(env => {
      if (env.expiresInSeconds && env.expiresInSeconds > 0) {
        env.expiresInSeconds = Math.max(0, env.expiresInSeconds - 1)
      }
    })
  })

  return {
    intents: envelopes,
    envelopes,
    selectedIntentId,
    selectedIntent,
    selectedCandidates: candidates,
    candidates,
    selectedCandidateId,
    selectedCandidate,
    nodes,
    edges,
    selectIntent,
    selectCandidate,
    addCustomIntent
  }
}
