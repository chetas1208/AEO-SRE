import type { DiscoveryGapItem } from '~/types/agentmatch'
import { isUuid } from '~/utils/apiBase'

/** Map gap remediation types to experiment ActionType values. */
export function gapActionToExperimentAction(
  gapAction: DiscoveryGapItem['recommendedAction']['type']
): string {
  const map: Record<DiscoveryGapItem['recommendedAction']['type'], string> = {
    update_canonical_page: 'update_existing_page',
    create_faq: 'create_faq',
    add_structured_evidence: 'structured_data',
    clarify_pricing: 'update_existing_page',
    correct_source: 'publisher_outreach',
    observe: 'observe',
  }
  return map[gapAction] ?? 'update_existing_page'
}

/** Live Profound orgs often lack an `accuracy` signal; pick a measured primary for verification. */
export function gapPrimaryMetric(gap: DiscoveryGapItem): string {
  switch (gap.gapType) {
    case 'MISSING_CITATION':
      return 'citation_share'
    case 'WRONG_TIER_PRICING':
    case 'STALE_INFORMATION':
      return 'visibility'
    case 'MISSING_CAPABILITY':
      return 'citation_share'
    default:
      return 'visibility'
  }
}

export function experimentDefaultsFromGap(gap: DiscoveryGapItem) {
  const incidentRaw = gap.incidentId ?? gap.id
  const incidentId = isUuid(incidentRaw) ? incidentRaw : undefined
  const targetUrl = gap.productTruth.canonicalSource ?? ''
  const targetKey = gap.productTruth.canonicalKey
    ? `claim:${gap.productTruth.canonicalKey}`
    : undefined
  const primaryMetric = gapPrimaryMetric(gap)

  return {
    incidentId,
    name: `Remediate: ${gap.recommendedAction.title}`.slice(0, 256),
    hypothesis:
      `${gap.recommendedAction.description} ` +
      `We expect closing the +${gap.gapPp}pp perception gap to improve ${primaryMetric.replace('_', ' ')} and citation share.`,
    action: gapActionToExperimentAction(gap.recommendedAction.type),
    targetUrl,
    targetKey,
    primaryMetric,
  }
}
