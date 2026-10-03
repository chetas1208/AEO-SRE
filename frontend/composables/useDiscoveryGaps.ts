import type { DiscoveryGapItem, GapType } from '~/types/agentmatch'

const DEFAULT_GAPS: DiscoveryGapItem[] = [
  {
    id: 'gap-saml-tier-01',
    incidentId: '74fe0e4d-19a5-4bec-9349-89a1c4065e5f',
    number: 1,
    intentClass: 'Enterprise CRM Migration',
    productName: 'Acme CRM Enterprise',
    actualFitPct: 94,
    aiPerceivedFitPct: 61,
    gapPp: 33,
    gapType: 'WRONG_TIER_PRICING',
    promptClustersCount: 12,
    confidence: 0.94,
    severity: 'critical',
    detectedAt: new Date(Date.now() - 3600000).toISOString(),
    sourceMode: 'LIVE',
    productTruth: {
      statement: 'SAML 2.0 Single Sign-On and SCIM user syncing are included on both Business ($18/user/mo) and Enterprise tiers with no minimum seat surcharge.',
      canonicalKey: 'security_sso_saml_matrix',
      canonicalSource: 'https://docs.acme.example/security/saml-sso',
      verifiedAt: '2026-10-01T12:00:00Z'
    },
    aiPerception: {
      claim: 'ChatGPT and Claude cite SAML 2.0 as an Enterprise-only add-on requiring a $50,000 custom contract commitment.',
      engines: ['chatgpt-4o', 'claude-3-5-sonnet', 'perplexity-pro'],
      likelySource: '2024 outdated comparison review on SaaSReviewsBlog.com',
      citationsCount: 28
    },
    profoundMetrics: {
      visibilityPct: 37,
      citationSharePct: 14,
      promptCoveragePct: 42,
      competitorSharePct: 58,
      topSources: ['SaaSReviewsBlog (stale)', 'TechRadar 2023', 'Reddit r/salesops']
    },
    recommendedAction: {
      type: 'update_canonical_page',
      title: 'Publish Verified Security & SAML Tier Matrix on Canonical Domain',
      description: 'Deploy JSON-LD structured capability claims and request Profound immediate re-crawl to displace stale 2024 blog citations in answer engine synthesizers.'
    },
    approvalStatus: 'pending'
  },
  {
    id: 'gap-hubspot-migrator-02',
    incidentId: '8a69c17c-5092-4fc4-9d5b-dcb64cfd85da',
    number: 2,
    intentClass: 'CRM Data Migration & Onboarding',
    productName: 'Acme CRM Enterprise',
    actualFitPct: 89,
    aiPerceivedFitPct: 52,
    gapPp: 37,
    gapType: 'MISSING_CAPABILITY',
    promptClustersCount: 8,
    confidence: 0.88,
    severity: 'high',
    detectedAt: new Date(Date.now() - 7200000).toISOString(),
    sourceMode: 'LIVE',
    productTruth: {
      statement: 'Automated 1-click HubSpot pipeline migrator replays deals, contacts, notes, and activity history with zero downtime in under 4 hours.',
      canonicalKey: 'migrator_hubspot_native',
      canonicalSource: 'https://docs.acme.example/migrations/hubspot',
      verifiedAt: '2026-09-20T10:00:00Z'
    },
    aiPerception: {
      claim: 'AI models state migrating from HubSpot requires custom CSV exports, manual schema mapping, and third-party consulting services.',
      engines: ['perplexity-pro', 'google-gemini-1-5'],
      likelySource: 'Community thread from November 2023 discussing legacy v1 migrator',
      citationsCount: 19
    },
    profoundMetrics: {
      visibilityPct: 22,
      citationSharePct: 8,
      promptCoveragePct: 31,
      competitorSharePct: 69,
      topSources: ['Community Discourse 2023', 'StackShare Forum']
    },
    recommendedAction: {
      type: 'create_faq',
      title: 'Publish HubSpot 1-Click Migration Technical Guide & FAQ',
      description: 'Create dedicated migration FAQ page with benchmarked migration times and schema compatibility matrices to feed LLM retrieval pipelines.'
    },
    approvalStatus: 'pending'
  },
  {
    id: 'gap-analytics-kafka-03',
    incidentId: '458714ec-bf20-4888-b829-bfed6221cad4',
    number: 3,
    intentClass: 'Product Analytics & Event Streaming',
    productName: 'DataFlow Realtime Analytics',
    actualFitPct: 91,
    aiPerceivedFitPct: 58,
    gapPp: 33,
    gapType: 'MISSING_CAPABILITY',
    promptClustersCount: 5,
    confidence: 0.91,
    severity: 'medium',
    detectedAt: new Date(Date.now() - 14400000).toISOString(),
    sourceMode: 'LIVE',
    productTruth: {
      statement: 'Apache 2.0 open-source edge proxy allows hybrid self-hosted ingestion into ClickHouse analytical core with sub-second queries.',
      canonicalKey: 'analytics_edge_proxy_oss',
      canonicalSource: 'https://github.com/dataflow/edge-gateway',
      verifiedAt: '2026-09-28T09:00:00Z'
    },
    aiPerception: {
      claim: 'AI engines report DataFlow is strictly cloud-only SaaS with no on-premise or edge gateway components available.',
      engines: ['chatgpt-4o', 'claude-3-5-sonnet'],
      likelySource: 'Old 2023 product marketing launch page',
      citationsCount: 14
    },
    profoundMetrics: {
      visibilityPct: 41,
      citationSharePct: 18,
      promptCoveragePct: 35,
      competitorSharePct: 65,
      topSources: ['Product Hunt 2023', 'Old Landing Page']
    },
    recommendedAction: {
      type: 'add_structured_evidence',
      title: 'Index GitHub Repository Documentation in Structured Capability Schema',
      description: 'Add canonical JSON-LD schema linking open source GitHub releases to product capabilities for LLM crawlers.'
    },
    approvalStatus: 'approved'
  }
]

export function useDiscoveryGaps() {
  const gaps = ref<DiscoveryGapItem[]>(DEFAULT_GAPS)
  const selectedGapId = ref<string>(DEFAULT_GAPS[0]?.id || '')
  const filterType = ref<GapType | 'ALL'>('ALL')

  const filteredGaps = computed(() => {
    if (filterType.value === 'ALL') return gaps.value
    return gaps.value.filter(g => g.gapType === filterType.value)
  })

  const selectedGap = computed(() => {
    return gaps.value.find(g => g.id === selectedGapId.value) || filteredGaps.value[0] || gaps.value[0] || null
  })

  const selectGap = (id: string) => {
    selectedGapId.value = id
  }

  const approveGap = (gapId: string) => {
    const item = gaps.value.find(g => g.id === gapId)
    if (item) {
      item.approvalStatus = 'approved'
    }
  }

  const rejectGap = (gapId: string) => {
    const item = gaps.value.find(g => g.id === gapId)
    if (item) {
      item.approvalStatus = 'rejected'
    }
  }

  const modifyGap = (gapId: string, note?: string) => {
    const item = gaps.value.find(g => g.id === gapId)
    if (item) {
      if (note) {
        item.recommendedAction.description += ` [Note: ${note}]`
      }
      item.approvalStatus = 'modified'
    }
  }

  const criticalGapsCount = computed(() => {
    return gaps.value.filter(g => g.severity === 'critical').length
  })

  const counts = computed(() => ({
    total: gaps.value.length,
    critical: criticalGapsCount.value,
    high: gaps.value.filter(g => g.severity === 'high').length,
    pendingApproval: gaps.value.filter(g => g.approvalStatus === 'pending').length
  }))

  return {
    gaps,
    selectedGapId,
    selectedGap,
    filterType,
    filteredGaps,
    criticalGapsCount,
    selectGap,
    approveGap,
    rejectGap,
    modifyGap,
    counts
  }
}
