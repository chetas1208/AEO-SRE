# To-do (queued, not started)

1. **Muse / Meta integration (integration, not a pivot).** The product stays AEO SRE / Change Guard. Muse is an integration surface in two separable roles: (a) a **Muse connector** exposing existing capabilities as tools (e.g. check a proposed change against canonical truth and active experiments, verify a claim against canonical truth, report discovery gaps) behind our own bearer-key auth; (b) **Muse Spark as a ModelGateway provider** (OpenAI-compatible Responses or Anthropic-compatible Messages) selected by env config only. Step 1 is research only: verify from Meta's public docs the connector submission/auth/scopes/review requirements, whether connectors can receive authorized task context, tool read/write classification, rate limits, and model endpoint/ID/protocol. Credentials stay separate (model key, connector credential, downstream keys). No code until access is confirmed. Source of the idea: user-pasted notes, unverified by us.
2. Profound Agent → Call API reachability for Change Guard (needs a tunnel/deploy; external).
3. Real canonical claims for the Mixpanel org (must come from the brand owner via Settings → Canonical truth; we do not invent them).
4. Competitor assets tracked in Profound (so competitor share-of-voice series exist).
5. Fixture Experiment 1 verification after 2026-10-04T20:29:43Z (human action; do not force).

## Muse adapter shape (when access is confirmed)
Muse is an input surface / context provider, not a product rename. Adapter lives beside other integrations (e.g. `backend/app/integrations/muse/`, next to the Profound connector and the Neo4j graph package) and only converts a Muse request into a provider-agnostic `IntentEnvelope` (intent, constraints, task-relevant preferences, expiry; no identity, no personal profile) and converts our response back. Everything downstream (Change Guard, canonical truth, discovery gaps, graph, policy, experiments) stays provider-agnostic and never imports Muse types.
