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

export function experimentDefaultsFromGap(gap: DiscoveryGapItem) {
  const incidentRaw = gap.incidentId ?? gap.id
  const incidentId = isUuid(incidentRaw) ? incidentRaw : undefined
  const targetUrl = gap.productTruth.canonicalSource ?? ''
  const targetKey = gap.productTruth.canonicalKey
    ? `claim:${gap.productTruth.canonicalKey}`
    : undefined

  return {
    incidentId,
    name: `Remediate: ${gap.recommendedAction.title}`.slice(0, 256),
    hypothesis:
      `${gap.recommendedAction.description} ` +
      `We expect closing the +${gap.gapPp}pp perception gap to improve accuracy and citation share.`,
    action: gapActionToExperimentAction(gap.recommendedAction.type),
    targetUrl,
    targetKey,
    primaryMetric: 'accuracy' as const,
  }
}
