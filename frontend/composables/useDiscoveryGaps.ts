import type { DiscoveryGapItem, GapType } from '~/types/agentmatch'

function normGap(raw: Record<string, unknown>): DiscoveryGapItem {
  const ra = (raw.recommended_action ?? raw.recommendedAction) as Record<string, unknown> | undefined
  const pt = (raw.product_truth ?? raw.productTruth) as Record<string, unknown> | undefined
  const ap = (raw.ai_perception ?? raw.aiPerception) as Record<string, unknown> | undefined
  const pm = (raw.profound_metrics ?? raw.profoundMetrics) as Record<string, unknown> | undefined
  const incidentId = String(raw.incident_id ?? raw.incidentId ?? raw.id ?? '')
  return {
    id: String(raw.id ?? incidentId),
    incidentId: incidentId || undefined,
    number: typeof raw.number === 'number' ? raw.number : undefined,
    intentClass: String(raw.intent_class ?? raw.intentClass ?? 'Discovery gap'),
    productName: String(raw.product_name ?? raw.productName ?? 'Product'),
    actualFitPct: Number(raw.actual_fit_pct ?? raw.actualFitPct ?? 0),
    aiPerceivedFitPct: Number(raw.ai_perceived_fit_pct ?? raw.aiPerceivedFitPct ?? 0),
    gapPp: Number(raw.gap_pp ?? raw.gapPp ?? 0),
    gapType: (raw.gap_type ?? raw.gapType ?? 'UNKNOWN') as DiscoveryGapItem['gapType'],
    promptClustersCount: Number(raw.prompt_clusters_count ?? raw.promptClustersCount ?? 0),
    confidence: Number(raw.confidence ?? 0),
    severity: (raw.severity ?? 'medium') as DiscoveryGapItem['severity'],
    detectedAt: String(raw.detected_at ?? raw.detectedAt ?? new Date().toISOString()),
    sourceMode: (raw.source_mode ?? raw.sourceMode ?? 'LIVE') as DiscoveryGapItem['sourceMode'],
    productTruth: {
      statement: String(pt?.statement ?? ''),
      canonicalKey: (pt?.canonical_key ?? pt?.canonicalKey) as string | null | undefined,
      canonicalSource: (pt?.canonical_source ?? pt?.canonicalSource) as string | null | undefined,
      verifiedAt: (pt?.verified_at ?? pt?.verifiedAt) as string | null | undefined,
    },
    aiPerception: {
      claim: String(ap?.claim ?? ''),
      engines: (ap?.engines as string[]) ?? [],
      likelySource: (ap?.likely_source ?? ap?.likelySource) as string | null | undefined,
      citationsCount: Number(ap?.citations_count ?? ap?.citationsCount ?? 0),
    },
    profoundMetrics: {
      visibilityPct: Number(pm?.visibility_pct ?? pm?.visibilityPct ?? 0),
      citationSharePct: Number(pm?.citation_share_pct ?? pm?.citationSharePct ?? 0),
      promptCoveragePct: Number(pm?.prompt_coverage_pct ?? pm?.promptCoveragePct ?? 0),
      competitorSharePct: Number(pm?.competitor_share_pct ?? pm?.competitorSharePct ?? 0),
      topSources: (pm?.top_sources ?? pm?.topSources ?? []) as string[],
    },
    recommendedAction: {
      type: (ra?.type ?? 'update_canonical_page') as DiscoveryGapItem['recommendedAction']['type'],
      title: String(ra?.title ?? 'Remediate gap'),
      description: String(ra?.description ?? ''),
    },
    approvalStatus: (raw.approval_status ?? raw.approvalStatus ?? 'pending') as DiscoveryGapItem['approvalStatus'],
  }
}

const DEFAULT_GAPS: DiscoveryGapItem[] = [
  {
    id: 'gap-saml-tier-01',
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
  const gaps = ref<DiscoveryGapItem[]>([...DEFAULT_GAPS])
  const selectedGapId = ref<string>(DEFAULT_GAPS[0]?.id || '')
  const filterType = ref<GapType | 'ALL'>('ALL')
  const isLoading = ref(false)
  const loadError = ref<string | null>(null)
  const dataSource = ref<'live' | 'fixture'>('fixture')

  async function refreshGaps() {
    const org = useOrganizationStore()
    if (!org.loaded) return
    isLoading.value = true
    loadError.value = null
    try {
      const q: Record<string, string> = { limit: '100' }
      if (org.currentId) q.org_id = org.currentId
      const res = await apiFetch<{ items?: Record<string, unknown>[]; source?: string }>(
        '/api/discovery-gaps',
        { query: q }
      )
      const live = (res.items ?? []).map(normGap)
      if (live.length) {
        gaps.value = live
        dataSource.value = 'live'
        if (!gaps.value.some(g => g.id === selectedGapId.value)) {
          selectedGapId.value = gaps.value[0]?.id ?? ''
        }
      } else {
        gaps.value = [...DEFAULT_GAPS]
        dataSource.value = 'fixture'
      }
    } catch (e: unknown) {
      const err = e as { message?: string }
      loadError.value = err?.message ?? 'Failed to load discovery gaps'
      gaps.value = [...DEFAULT_GAPS]
      dataSource.value = 'fixture'
    } finally {
      isLoading.value = false
    }
  }

  onMounted(() => {
    void refreshGaps()
  })

  watch(
    () => useOrganizationStore().currentId,
    () => {
      void refreshGaps()
    }
  )

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
    counts,
    isLoading,
    loadError,
    dataSource,
    refreshGaps,
  }
}
