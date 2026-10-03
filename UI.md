# AEO SRE — UI Spec (from mockup)

Two dark-theme mockups, both showing incident #1042 "Enterprise SSO visibility drop":
- **Mockup A** (sections 1–9): dense full dashboard, 10-item nav, right rail.
- **Mockup B** (section 10): simplified, 3-item nav, 2-column Incidents page. Closer to Plan.md scope.
- **Reference screens (§10.8–10.9):** high-fidelity **Incidents** + **Experiments** pair saved in-repo (March 2026). Same 3-item nav as B; Experiments detail is the authoritative UX for the intervention ledger.

Reference image files (do not commit secrets; visuals only):
- Incidents: `.cursor/projects/.../assets/image-68003fff-9608-4d21-a6fe-30addfa2f453.jpg` (also under workspace `assets/` when copied)
- Experiments: `.cursor/projects/.../assets/image-a2dc58af-90ff-4a44-a536-58bbb81c0f03.jpg`

Each is recorded below, mapped to backend data, with conflicts against [Plan.md](Plan.md). **Recommendation: build Mockup B as the base (section 10.6–10.7), implement Experiments per §10.9, pull Mockup A extras in as stretch.**

Layout: left nav | incident list | incident detail (center) | live panels (right). Bottom-left system status. Top bar global.

---

## 1. Top bar
| Element | Behavior | Data source |
|---|---|---|
| Logo + "AEO SRE — Incident Response for AI Discovery" | Branding | static |
| Global search | "Search prompts, incidents, competitors…" | `GET /api/search?q=` over prompts, incidents, competitors |
| **Live** pill (green) | Shows SSE connection / ingest health. Must turn red/grey when disconnected, never stay green by default | SSE heartbeat |
| Date range dropdown | "Last 7 days"; scopes all KPI cards + lists | query param `range` |
| Notification bell | New incidents, approvals needed, rewards evaluated | SSE `notifications` |
| User avatar | Account menu | auth |

## 2. Left nav
Overview · **Incidents** (badge = active count, 4) · Experiments · Knowledge Graph · Prompt Intelligence · Competitors · Sources & Evidence · Executions · Learning · Settings

| Item | Maps to |
|---|---|
| Overview | KPI cards + cross-incident summary |
| Incidents | list + detail (core screen) |
| Experiments | experiment ledger table (Plan §6 route 3) |
| Knowledge Graph | org-wide evidence graph (stretch; Plan lists "giant knowledge graph UI" as non-goal → keep as per-incident graph only) |
| Prompt Intelligence | prompt clusters, volume, visibility per prompt (Profound tracked prompts) |
| Competitors | competitor visibility/citation share, page-change history |
| Sources & Evidence | all crawled sources, snapshots, hashes, EvidenceRanker scores |
| Executions | Intervention packages and executions (manual by default; optional adapters), approval + status |
| Learning | policy versions, bandit state, reward history, OPE metrics |
| Settings | org/domain, Profound creds, executors (Manual always on; GitHub optional), reward weights, thresholds |

### System Status (bottom-left, collapsible)
Green/red dots, each from a real health check:
- Profound Connected
- Web Crawlers Active
- ML Models Loaded (EvidenceRanker artifact)
- Policy v0.3.1 (current `PolicyVersion`)
- Workers Running (ARQ)

## 3. KPI row (4 cards, each with sparkline)
| Card | Value shown | Source |
|---|---|---|
| Active Incidents | 4, "↑ 2 new", dismiss ✕ | incidents where status ∉ resolved |
| Visibility (Avg) | −12.4%, "across 3 key topics" | Profound visibility time series |
| Citation Share | +8.1%, "recovered this week" | Profound citation share |
| Experiments Running | 6, "3 awaiting results" | experiments in `executed` / `awaiting_reward` |

## 4. Incident list (left of center)
- Title "Incidents"; filter tabs with counts: **All 8 · Investigating 3 · Action Needed 4**
- Card per incident: title, severity chip (High red / Medium amber / Low green), one-line delta, topic/tag, relative detected time, colored left border by severity, selected card highlighted.
- Sample types shown (these define the incident taxonomy):
  - Visibility drop (Enterprise SSO, −24pp)
  - Competitor gained citations (+18pp, Pricing)
  - Incorrect product capability (factual conflict, Product)
  - Lost citation source (−3 key sources, Integrations)
  - Prompt volume spike (+180%, Security)
  - New competitor content (new page, Analytics)
- Sorted by Intervention Priority; severity chip derived from priority bucket.

## 5. Incident detail (center)
### Header
- Title, **High Priority** chip, `#1042`, subtitle (plain-language summary)
- Meta: Detected "2 hours ago", Status "Investigating"
- **Investigate** button → triggers/re-runs investigation (starts SSE stream)

### Three-step flow (numbered columns joined by flowing connector lines)
**① What Happened**
- Visibility 61% → 37% with chart and "−24pp" badge
- Prompts: 14 affected
- Citation Share: −18pp
- Competitor: +33pp
- Prompt Volume: High

**② Why It Happened** — ranked hypotheses, each with confidence badge:
- Competitor content update 0.84 — "New SAML SSO section published 4 days ago"
- New citation sources 0.71 — "3 new sources citing competitor"
- Our content exists but weak 0.63 — "Information is on a nested page"
- High enterprise intent 0.91 — "Affects buyer persona" (context factor, not root cause)

**③ Recommended Action** (highlighted card, policy output)
- "Update existing page" 0.71 — "Enhance /enterprise/security with clear SAML SSO information"
- **Alternative actions** with bandit scores: Create dedicated page 0.62 · FAQ content 0.48 · Comparison page 0.31 · Publisher outreach 0.28 · Structured data 0.22
- Buttons: **Approve** (primary) · **Modify**
- (Reject is required by Plan §4.8 — add; not in mockup.)

Connector lines visually link each hypothesis to the recommended action (weight/colour by contribution).

### Tabs (bottom of center)
**Evidence Graph** · Affected Prompts (14) · Metrics · Proposed Changes · Bandit Analysis

**Evidence Graph tab**
- Root node "Visibility Loss −24pp for enterprise SSO"
- Children: Affected Prompts (14 high-intent), Competitor Gained (+33pp), Citation Changes (3 new, 2 lost), Our Content (SSO info exists, nested)
- Leaves: Competitor Page (new SAML section, 4 days ago), New Source A (TechRadar comparison), New Source B (G2 review), Our Page (/support/saml, low visibility)
- Left filter dropdown "All Evidence" + legend toggles: Profound Signal · Our Content · Competitor Content · External Source · Inference (colour-coded)
- Zoom in / out / fit controls
- Nodes clickable → evidence detail (source URL, timestamp, confidence, extract, hash, retrieval method)

**Affected Prompts** — table of 14 prompts: volume, visibility before/after, engines, competitor cited.
**Metrics** — time series: visibility, citation share, accuracy, competitor share.
**Proposed Changes** — LLM-generated patch / diff for the chosen action, risk level, target file/URL.
**Bandit Analysis** — context vector, per-action score + uncertainty, policy version, cold-start flag, `policy_probability`.

### Expected Outcome strip (bottom, "Based on Similar Incidents")
Visibility recovery +8–15pp · Citation share +5–10pp · Success probability High (bar) · Expected time to impact ~3–7 days.

## 6. Right rail
### Live Investigation (SSE log, "Live" pill)
Timestamped steps with colored dots (blue = in progress, green = done):
- Fetching affected prompts — 14 loaded
- Retrieving citation changes — 21 sources analyzed
- Crawling competitor source — page fetched
- Analyzing content changes — new SAML section detected
- Running evidence ranking — contradiction score 0.18
- Generating root-cause hypotheses — 4 generated
- Computing intervention policy — action: update_page (0.71)

### Key Evidence
Cards with icon, title, age, confidence: Competitor page update 0.84 · TechRadar comparison 0.76 · G2 review citation 0.68 · Our SAML support page 0.63. "View all evidence →" link.

### Recent Events
Timeline: Incident detected → Investigation started → Root cause identified → Action recommended.

---

## 7. Cross-cutting UI behavior
- All numbers come from the live backend; no hardcoded values (Plan hard rule #1).
- Every widget needs **empty / unavailable / loading / error** states (Profound down, crawl failed, LLM down, investigation incomplete).
- Severity colours: High red, Medium amber, Low green. Confidence badges green chips.
- Dark theme default; responsive not required for hackathon.
- Real-time: incident list, KPI, event log, and status update via SSE without reload.

---

## 8. Conflicts with Plan.md — resolve before build

| Mockup | Conflict | Resolution |
|---|---|---|
| Rich styled UI | Plan says wiring only, no CSS, 3 plain routes | Build Plan's 3 routes first (Incidents, Incident detail, Experiments). Layer this styling only after backend works. If time-boxed, ship incident detail in the mockup's 3-step layout with minimal CSS |
| **Expected Outcome** (+8–15pp, "High" success probability, 3–7 days) | Plan forbids invented numbers; policy has no history on day one | Compute only from logged experiments of the same incident class. Fewer than N evaluated experiments → show "Insufficient history (cold-start)". Never show static ranges |
| Success probability "High" | No calibrated model exists | Same as above; drop until OPE/history supports it |
| Bandit scores 0.71 / 0.62 … | Look like probabilities | Label as UCB/Thompson scores; show `policy_probability` separately |
| Hypothesis "High enterprise intent 0.91" | A context factor, not a root cause | Move into Priority breakdown, not hypotheses list |
| Knowledge Graph nav item | Plan non-goal | Keep per-incident graph only; hide org-wide page |
| Overview, Prompt Intelligence, Competitors, Learning, Settings pages | Beyond 3 routes | Stretch. Priority: Learning (policy versions) > Executions > rest |
| Missing: Reject, priority breakdown, awaiting_reward state, cold-start label, policy version on decision | Plan requires | Add: Reject button; Priority component bars (demand, intent, severity, persona, displacement, confidence, feasibility); experiment status chips; "cold-start policy" badge |
| Search bar | Not in Plan | Stretch, low value |

## 9. Build priority for UI
1. Incident list + filters (Plan route 1)
2. Incident detail: What / Why / Action columns, Approve / Reject / Modify, SSE log
3. Evidence Graph tab (nested tree first, graph visual later)
4. Experiments table with status, reward, policy version
5. KPI row + System Status
6. Tabs: Affected Prompts, Metrics, Proposed Changes, Bandit Analysis
7. Key Evidence + Recent Events rails
8. Styling pass to match mockup
9. Stretch pages: Learning, Executions, Competitors, Prompt Intelligence, Sources

---

# 10. Mockup B — simplified Incidents page

Page title "**Incidents**", subtitle "High-signal changes in your AI discovery performance". Layout: slim left sidebar | incident list | incident detail. No right rail, no KPI row.

## 10.1 Sidebar
- Logo: "AEO SRE — AI Discovery Incident Response"
- Nav (only 3): **Incidents** (badge = active count, 4) · **Experiments** · **Settings** — matches Plan §6 routes exactly.
- **Monitoring status** card: green dot, "Monitoring", "Last sync 12 min ago". Must be real: time since last successful Profound ingest. Turns amber/red when stale or Profound unavailable.
- **Org switcher** (bottom): avatar, org name "Acme Corp", domain `acme.com`, chevron. Backed by `POST /organizations {domain}`; switching changes the scope of every query.

## 10.2 Top bar
- Global search "Search incidents, topics, competitors…"
- **Live** pill (green dot) — SSE connection state
- Date range dropdown "Last 7 days" with calendar icon

## 10.3 Incident list
- Severity filter tabs with counts: **All 4 · Critical 1 · High 2 · Medium 1**
- Card: category icon (shield, trophy, document/fact, trend arrow), title, one-line delta + tag, relative time, severity chip, mini sparkline coloured by direction. Selected card has glowing blue border.
- Sample incidents (taxonomy):
  | Incident | Delta / tag | Severity |
  |---|---|---|
  | Enterprise SSO visibility drop | −24pp across high-intent prompts | Critical |
  | Competitor gained citations | +18pp share · Pricing comparisons | High |
  | Incorrect product capability | Factual conflict · AI answers | High |
  | Prompt volume spike | +180% volume · Security topics | Medium |
- Severity now has 4 levels (Critical/High/Medium/Low) — derive from Intervention Priority buckets.

## 10.4 Incident detail
**Header:** icon, title, **Critical** chip, `#1042`, "Detected 2 hours ago · Investigating" (status in blue), **Investigate** button (starts investigation + SSE).

**4 metric cards** (value before → after, delta, sparkline):
| Card | Example | Source |
|---|---|---|
| Visibility | 61% → 37%, −24pp (red) | Profound visibility |
| Citation Share | 32% → 14%, −18pp (red) | Profound citation share |
| Prompt Volume | 1.2K, +22% (green) | Profound prompt volume |
| Competitor Share | 21% → 54%, +33pp (red) | Profound competitor comparison |
Colour = good/bad for the brand, not sign of number (volume up is green, competitor up is red).

**Tabs:** **Analysis** · Affected Prompts (14) · Evidence (21) · Recommended Action

**Analysis tab, left column — three numbered steps:**
1. **What happened?** Plain-language sentence + example prompt callout: "best analytics platform with enterprise SSO" → Your visibility 61% → 37% (−24pp chip).
2. **Why it happened?** Plain-language root cause + linked evidence card: "New competitor content — acme-competitor.com/security/saml — 4 days ago" (external-link icon).
3. **Recommended action.** Text: "Update your existing /security page with clear SAML SSO information, supported IdPs and an FAQ section." Buttons **Approve** (primary) · **Modify**.

**Analysis tab, right column:**
- **Evidence Graph** (DAG): root "Visibility Loss −24pp for enterprise SSO" → level 1: Competitor Update (new SAML section, 4 days ago), New Citations (3 sources citing competitor), Our Content (info exists, nested) → level 2: TechRadar Article (high authority), G2 Review (confirms SSO support), Our Page /security (low visibility). Colour-coded edges/nodes (red = competitor, blue = citations, green = ours).
- **Expected Outcome** panel: Visibility recovery +8–15pp · Citation share +5–10pp · Time to impact ~3–7 days, each with small trend sparkline.

## 10.5 Differences vs Mockup A
| Area | A | B |
|---|---|---|
| Nav | 10 items | 3 items (Incidents, Experiments, Settings) |
| Severity levels | High/Medium/Low | Critical/High/Medium (+Low) |
| Incident filters | All / Investigating / Action Needed | All / Critical / High / Medium |
| Detail metrics | inline in "What happened" | 4 dedicated metric cards incl. Competitor Share |
| Hypotheses | 4 ranked with confidence badges | single narrative root cause + linked evidence |
| Alternatives + bandit scores | shown | **absent** |
| Live Investigation log, Recent Events, Key Evidence | right rail | **absent** |
| KPI row, System Status panel | present | replaced by "Monitoring / Last sync" |
| Org switcher | absent | present (Acme Corp) |
| Success probability | shown | dropped (only 3 outcome figures) |

## 10.6 Gaps and conflicts vs Plan.md
| Mockup B | Issue | Resolution |
|---|---|---|
| Expected Outcome (+8–15pp, +5–10pp, ~3–7 days) | Invented numbers; no history day one | Derive only from evaluated experiments of same incident class; else "Insufficient history (cold-start)". Never static |
| No alternatives / bandit scores | Plan requires showing alternatives + scores | Add to Recommended Action tab (list with scores, policy version, cold-start badge) |
| No Reject | Plan requires Approve/Reject/Modify | Add Reject next to Modify |
| No live event log | Plan: SSE log is core | Add collapsible log under Investigate or in Analysis tab |
| No hypothesis confidence | Plan: hypotheses carry confidence | Show confidence next to root-cause text; multiple hypotheses listed |
| No priority breakdown | Plan: expose each component | Add to header or Analysis tab (demand, intent, severity, persona, displacement, confidence, feasibility) |
| No experiment/reward state | Plan: `awaiting_reward` etc. | Experiments page + status chip on incident once executed |
| Single org in list but org switcher | `POST /organizations` | Switcher lists orgs; "Add organization" opens domain input |
| Evidence tab count 21 | Must equal real evidence rows | Bind to DB count |
| "Last sync 12 min ago" | Easy to fake | Bind to last ingest timestamp |

## 10.7 Recommended build (merges A + B)
1. Sidebar (Incidents / Experiments / Settings), monitoring status, org switcher
2. Incidents page: severity tabs + list + detail with 4 metric cards
3. Analysis tab: What / Why / Action + Evidence Graph (nested tree first)
4. Recommended Action tab: patch diff, alternatives with bandit scores, Approve / Reject / Modify
5. SSE: Live pill, Investigate log, list auto-updates
6. Evidence tab + Affected Prompts tab (real rows)
7. Experiments page: status, before/after, reward, policy version
8. Expected Outcome panel gated on real experiment history
9. Styling pass to match mockup
10. Stretch: Mockup A extras (KPI row, Key Evidence rail, Recent Events, Learning/Executions/Competitors pages)

## 10.8 Reference mockup — Incidents dashboard

Dark theme, neon accents (blue/purple/red/orange/green by severity and metric direction). Layout: **left sidebar | incident list | incident detail** (no right rail in this reference).

### Global chrome

| Element | Behavior | Data / API |
|---|---|---|
| Brand | **AEO SRE** + subtitle **AI Discovery Incident Response** | static |
| Nav | **Incidents** (badge = open/active count) · **Experiments** · **Settings** | `GET /api/incidents` count; routes `/incidents`, `/experiments`, `/settings` |
| Monitoring card (sidebar bottom) | Green **Live** dot, label **Monitoring**, **Last sync** relative time | Last successful Profound ingest (`/api/system/capabilities`, settings `profound.last_sync.*`); amber/red when stale or Profound unavailable — never fake “Live” |
| Org block | Avatar, org name (e.g. Acme Corp), domain (e.g. acme.com) | `Organization`; org switcher when multi-tenant |

### Incidents list (center-left)

| Element | Behavior | Data / API |
|---|---|---|
| Page title | **Incidents** | — |
| Search | Placeholder: search incidents, topics, competitors | `GET /api/search?q=` (or list filters when search not built) |
| **Live** pill | SSE / ingest health | SSE heartbeat + capabilities |
| Date range | e.g. **Last 7 days** | Query `range` on list metrics |
| Severity filters | **All** · **Critical** · **High** · **Medium** (each with count) | Derived from `priority` / severity buckets on `Incident` |
| Incident card | Title, one-line impact (e.g. −24pp across high-intent prompts), time since detected, severity chip, **mini sparkline** trend | `IncidentSummary` + metric deltas; sort by priority |
| Selection | Highlight selected card; drives detail pane | `GET /api/incidents/{id}` |

Sample taxonomy (same as §10.3): visibility drop, competitor citations, factual conflict, citation loss, prompt volume spike, new competitor page.

### Incident detail (center-right)

| Element | Behavior | Data / API |
|---|---|---|
| Header | Title, **#1042**, status (e.g. **Investigating**), primary CTA **Investigate** | `IncidentDetail`, `primary_cta`, `POST …/investigate` |
| Provenance badge | **Live Profound** vs **Test fixture** vs **Historical replay** vs **Mixed** | `context.detection[].source` / `context.provenance`; never label fixture as Live |
| **Four metric cards** | Visibility, Citation Share, Prompt Volume, Competitor Share — each: before → after, delta, directional colour, sparkline | `IncidentDetail.metrics` (`MetricDelta[]`) |
| Tabs | **Analysis** · **Affected Prompts (n)** · **Evidence (n)** · **Recommended Action** | Counts from API; tab query `?tab=` |

**Analysis tab**

1. **What happened?** — Narrative + example affected prompt callout (quoted user query).  
   - Source: `IncidentExplanation.what_changed` (+ prompt examples from cluster).
2. **Why it happened?** — Root cause narrative + **linked evidence** (competitor URL, “new content N days ago”).  
   - Source: `leading_hypothesis`, supporting evidence rows / graph nodes.
3. **Recommended action** — Policy-selected intervention text.  
   - Buttons: **Approve & Execute** (primary; maps to Approve + activation path) · **Modify** · **Reject** (required by Plan; add if missing in visual).  
   - Source: `recommended_action`, `GET /api/interventions`, policy scores.

**Right column (Analysis)**

| Panel | Content | Data / API |
|---|---|---|
| **Evidence graph** | Layered DAG: root (e.g. Visibility Loss) → competitor update, new citations, our content → leaf sources (TechRadar, G2, our URL) | `GET /api/incidents/{id}/graph` (`GraphOut`); textual `<details>` alternative required (a11y) |
| **Expected outcome** | Forecast bands: visibility recovery, citation share, time to impact | `expected_outcome` on incident; cold-start copy when `available: false` |

**Other tabs**

- **Affected Prompts** — table: prompt text, volume, visibility before/after, engines, competitor cited.  
  - `GET /api/incidents/{id}/prompts`
- **Evidence** — list/graph inspector: source, URL, hash, excerpt, retrieval method, ranker scores.  
  - `GET /api/incidents/{id}/evidence`
- **Recommended Action** — intervention package, diff preview, approval panel, execution record (manual).  
  - Interventions + approve/modify/reject APIs

### Cross-cutting (Incidents screen)

- **Live means live:** Live pill and “Live Profound” badge only when data provenance is Profound.
- **Investigate** starts/re-runs investigation; progress via **SSE** (`/api/incidents/{id}/events`).
- Severity colour: Critical (red) · High (orange) · Medium (amber) · Low (green).

---

## 10.9 Reference mockup — Experiments dashboard

Same sidebar chrome as §10.8. Primary surface for **intervention outcomes**, policy learning, and verification timing.

### Experiments list header

| Element | Behavior | Data / API |
|---|---|---|
| Title | **Experiments** | — |
| Search | Search experiments (by id, incident, action) | `GET /api/experiments?q=` |
| Date range | e.g. **Last 30 days** | Filter on `created_at` / `executed_at` |

### Summary metric row (four cards)

| Card | Meaning | Data / API |
|---|---|---|
| **Active experiments** | Running or in verification pipeline | Count by `Experiment.status` ∈ active/post-execution set |
| **Awaiting verification** | Executed, window open or not yet measured | `awaiting_verification` |
| **Rewarded** | Completed with reward recorded | `rewarded` |
| **Policy version** | Current bandit version (e.g. v0.7) + note “from N rewarded experiments” | `GET /api/policy`, `PolicyVersion` |

### Recent experiments list (sidebar or left column)

Each row:

| Field | Example | Data / API |
|---|---|---|
| Code | EXP-0012 | `Experiment.number` / display code |
| **Provenance tag** | **LIVE** · **REPLAY** · **TEST** (fixture) | Derive from incident/signal `source` — must match backend `source_mode` semantics |
| Title | Linked to incident title | `Experiment` → `Incident.title` |
| Intervention type | e.g. Update existing page · Observe | `selected_action` |
| Status chip | Awaiting verification · Rewarded · In progress · No impact · Unfavorable | `Experiment.status` + reward/outcome |
| Date | Initiated / executed | `executed_at` or `created_at` |

Colour semantics: orange = awaiting verification; green = rewarded/favorable; blue = in progress; grey = no impact; red = unfavorable.

### Experiment detail (selected experiment)

**Header**

- Experiment code (EXP-0012), link to **incident** (INC-0043), target domain, detection date.
- Status line consistent with list chip.

**Nine-step intervention workflow (vertical timeline)**

Each step is a labeled stage; collapsed when empty, expanded when data exists:

| Step | Label | Content | Data / API |
|---|---|---|---|
| 1 | **Hypothesis** | What we expect the intervention to change | Experiment `reason`, incident hypotheses |
| 2 | **Intervention** | Selected action + **View changes** (diff/package) | `selected_action`, `proposed_change` / `approved_change` |
| 3 | **Approval** | Approver, timestamp, note | `Approval`, `approver` |
| 4 | **Baseline (before)** | Metrics over pre-intervention window: visibility, citation share, competitor share, prompt volume | `before_metrics` (immutable after activation) |
| 5 | **Verification window** | Duration after execution; **Eligible after** absolute UTC timestamp | `verification_window_start` / `_end`; show countdown optional, timestamp required |
| 6 | **After metrics** | Post-window measurements | `after_metrics` (null until verified) |
| 7 | **Outcome** | Verified / inconclusive / pending | `status`, evaluation result |
| 8 | **Reward** | Scalar + components | `Reward` row when present |
| 9 | **Learning** | Policy update from this experiment | New `PolicyVersion`, link from reward |

**Primary metrics visualization**

- Grouped **Before → After** bars for visibility, citation share, competitor share, prompt volume.
- Source: `before_metrics` vs `after_metrics`; empty after state until verification.

**Policy learning (contextual bandit)**

- List actions with **policy scores** (e.g. update existing page 0.42, create FAQ 0.31, observe …).
- Show policy version, cold-start badge, selection probability for the chosen action.
- Source: `alternatives`, `policy_probability`, `GET /api/policy`.

**Potential confounders**

- Automated flags: competitor activity, algorithm/model shifts, seasonality — from observation `_confounders` or investigation context.
- Copy must stay **associational**, not causal (see DEC-034).

**Evidence & provenance**

- Attachments roll-up: updated page (diff), SERP snapshot, LLM answer, Profound signals — count matches DB.
- Source: `evidence_snapshot`, linked evidence ids, execution references.

### Experiments UX rules (from mockup)

- **TEST / FIXTURE** tag on dev_fixture experiments; never display as LIVE.
- **Awaiting verification** must show **Eligible after: &lt;ISO-8601 UTC&gt;** (not a silent disabled button).
- Do not show after metrics or reward before backend verification window and qualifying observation.
- Experiment detail is the home for reward/policy narrative — not the Incidents queue.

### Mapping to current build (honest)

| Mockup element | Shipped today | Gap |
|---|---|---|
| 3-item nav + Incidents layout | Yes (Nuxt) | Full sparklines / severity tabs polish |
| 4 metric cards + Analysis tabs | Partial | Expected outcome history gating |
| Evidence graph SVG + text alt | Yes | Browser E2E not verified in all envs |
| Experiments summary cards | Partial | Match counts API |
| 9-step experiment timeline | Partial | Expand detail page to full stepper |
| LIVE/REPLAY/TEST badges on experiments | Partial | Wire from provenance everywhere |
| Policy scores on experiment detail | Partial | Surface `alternatives` on experiment page |

---

# AEO SRE — UI Implementation Specification

> **Product:** AEO SRE — Incident Response for AI Discovery  
> **Frontend:** Nuxt 4 + TypeScript  
> **Scope:** UI architecture, interaction model, frontend wiring, component behavior, and visual direction  
> **Current phase:** Functional frontend wiring first. Styling comes after correctness.  
> **Primary principle:** The product must feel like an incident-response console for AI discovery, not a generic analytics dashboard, CRM, SEO tool, or AI chatbot.

---

# 1. Product UI Thesis

AEO SRE is not a dashboard whose job is to show more metrics.

Its interface exists to answer four operational questions as fast as possible:

1. **What changed?**
2. **Why did it change?**
3. **What should we do?**
4. **Did the intervention work?**

The UI should therefore behave more like **Sentry / Datadog / PagerDuty for AI discovery incidents** than like a traditional marketing analytics suite.

The user is a marketing executive, growth lead, SEO/AEO lead, product marketer, content lead, or technical marketing operator. They should not need to understand reinforcement learning, vector databases, agent graphs, embeddings, or model internals to use the product.

The system may be technically sophisticated underneath. The UI must remain operationally simple.

---

# 2. Non-Negotiable UI Rules

## 2.1 Maximum top-level navigation

Use **three tabs only**:

1. **Incidents**
2. **Experiments**
3. **Settings**

Do not add Overview, Analytics, Knowledge Graph, Prompt Intelligence, Competitors, Sources, Learning, Reports, or similar top-level sections.

If information belongs to an incident, it lives inside the incident view.

If information belongs to an intervention outcome, it lives inside Experiments.

If it configures integrations or behavior, it lives inside Settings.

---

## 2.2 No chatbot-first UX

Do not put a chat box in the center of the product.

Do not make the user ask:

> “What happened to my visibility?”

The system should already know what changed and present it directly.

An optional command/search bar is acceptable for filtering incidents, topics, prompts, domains, and competitors, but it is **not** a conversational agent surface.

---

## 2.3 No generic dashboard home

There is no separate home dashboard.

**Incidents is the default route and operational home.**

The user opens the product and immediately sees the things that need attention.

---

## 2.4 No decorative complexity

Do not build:

- giant KPI walls
- 12-card overview grids
- fake maps
- generic funnel diagrams
- unnecessary pie charts
- generic bar-chart collections
- animated globes
- 3D scenes
- decorative agent avatars
- “AI thinking” blobs
- fake terminal windows
- massive sidebars
- nested dashboards
- hidden critical actions behind menus

Every visible element must support a decision.

---

## 2.5 Evidence before recommendation

A recommendation must never appear as an unexplained AI answer.

Every recommendation must expose:

- observed change
- affected prompt/topic cluster
- root-cause hypothesis
- evidence supporting the hypothesis
- confidence
- alternative interventions
- action risk
- whether the recommendation came from cold-start priors or learned policy

---

## 2.6 Live means live

The product should visually communicate live system state.

The UI must expose:

- last synchronization time
- current investigation state
- worker progress
- Profound connection state
- whether the evidence collector is active
- whether the policy engine is available
- whether an experiment is waiting for delayed reward

Never fake completed outcomes.

If data is unavailable, say so.

---

# 3. Core Information Architecture

```text
AEO SRE
│
├── Incidents                  /incidents
│   └── Incident Detail        /incidents/:id
│
├── Experiments                /experiments
│   └── Experiment Detail      /experiments/:id
│
└── Settings                   /settings
```

Default route:

```text
/ → /incidents
```

---

# 4. Global Layout

The desktop layout should use three structural regions.

```text
┌───────────────────────────────────────────────────────────────┐
│ Top utility bar                                               │
├───────────────┬───────────────────────────────────────────────┤
│               │                                               │
│ Compact       │                                               │
│ navigation    │ Main product surface                          │
│ rail          │                                               │
│               │                                               │
│               │                                               │
├───────────────┴───────────────────────────────────────────────┤
```

There should be **no permanent third global sidebar**.

Contextual side panels may appear inside a detail page when appropriate.

---

# 5. Navigation Rail

Width should remain compact.

The navigation contains only:

```text
AEO SRE logo

Incidents
Experiments
Settings

--- bottom ---
Live system status
Organization switcher
```

## Active state

The active tab must be unmistakable.

Use:

- subtle illuminated background
- left/right edge glow or accent
- stronger text weight
- active icon treatment

Do not use oversized selected cards.

## Counts

Incidents may show an attention count:

```text
Incidents   4
```

Only show the number of **unresolved actionable incidents**, not total history.

Experiments may show:

```text
Experiments   2 waiting
```

if there are experiments currently awaiting measurement.

---

# 6. Global Utility Bar

The top bar should remain light and operational.

Desktop contents:

```text
[Current page title / breadcrumb]

                   [Search] [Live status] [Time range] [User]
```

Search placeholder:

```text
Search incidents, topics, prompts, competitors…
```

Search supports:

- incident title
- incident ID
- topic
- prompt text
- competitor
- cited domain
- experiment ID

The search box should not behave like a chatbot.

---

# 7. Primary Tab 1 — Incidents

Route:

```text
/incidents
```

Purpose:

> Show the user only the meaningful AI-discovery changes that require attention or investigation.

This is the most important page in the product.

---

# 8. Incidents Page Structure

Use a two-column workspace.

```text
┌────────────────────────┬─────────────────────────────────────────┐
│ Incident queue         │ Selected incident preview/detail       │
│                        │                                         │
│ filters                │ incident header                         │
│ incident list          │ metrics                                 │
│                        │ diagnosis                               │
│                        │ evidence                                │
│                        │ recommendation                          │
└────────────────────────┴─────────────────────────────────────────┘
```

Desktop split recommendation:

```text
34% / 66%
```

The selected incident should update without a full route transition when possible.

The canonical incident URL must still be:

```text
/incidents/:id
```

so the state is shareable.

---

# 9. Incident Queue

The left queue should feel like a high-signal operations inbox.

## Queue filters

Use only:

```text
All
Critical
High
Medium
```

Optional secondary filters can live behind one compact filter control:

- status
- topic
- engine
- source type
- competitor
- organization

Do not expose 12 filter pills permanently.

---

# 10. Incident Card

Each incident card must communicate the full reason to click it in under two seconds.

Required fields:

```text
Incident title
Severity
Primary delta
Context label
Age / detected time
Tiny trend indicator
```

Example:

```text
Enterprise SSO visibility drop       Critical
-24pp across high-intent prompts
Enterprise · Security
Detected 2h ago
```

Other example incident types:

```text
Competitor gained citations
Incorrect product capability
Lost authoritative citation source
High-intent prompt volume spike
New competitor content displaced us
AI engines citing stale pricing
```

Cards should not contain every metric.

The queue is for triage.

---

# 11. Incident Severity Semantics

Use exactly four severity levels:

```text
Critical
High
Medium
Low
```

Meaning:

### Critical

High commercial intent + large AI-discovery degradation or factual risk + strong evidence.

### High

Material degradation or competitive displacement on meaningful prompts.

### Medium

Actionable issue, but lower demand, confidence, or impact.

### Low

Informational event that may become actionable.

Severity must come from backend logic.

Frontend must not infer severity from colors or metric thresholds locally.

---

# 12. Incident Detail Header

The incident detail begins with:

```text
Enterprise SSO visibility drop    Critical   #1042
Detected 2 hours ago · Investigating
```

Right-side primary action depends on state:

```text
Investigate
Approve
Mark as executed
Awaiting Measurement
Resolve
```

Never show multiple primary CTAs simultaneously.

---

# 13. Incident Metric Strip

Show only the metrics directly relevant to the incident.

Maximum **four metric cards**.

Recommended examples:

```text
Visibility
61% → 37%
-24pp

Citation Share
32% → 14%
-18pp

Prompt Volume
1.2K
+22%

Competitor Share
21% → 54%
+33pp
```

Do not always render the same four metrics.

Examples of alternate metrics:

- factual accuracy
- sentiment
- average position
- affected prompt count
- new source count
- lost source count
- evidence confidence

Backend returns the metrics relevant to the incident.

Frontend renders a generic metric-card schema.

---

# 14. Incident Detail Internal Navigation

Inside an incident, use a compact local tab bar.

Maximum four local tabs:

```text
Analysis
Affected Prompts
Evidence
Action
```

Do not turn these into global navigation.

Default:

```text
Analysis
```

---

# 15. Analysis Tab

This is the core explanation surface.

Use a three-step vertical narrative:

```text
1. What happened?
2. Why it happened?
3. Recommended action
```

This narrative should read like an incident report, not an AI essay.

---

# 16. “What Happened?” Block

Required contents:

- plain-language summary
- metric change
- affected prompt cluster
- incident start time
- detection confidence

Example:

```text
What happened?

Visibility dropped 24 percentage points for high-intent enterprise
prompts related to SSO and authentication.

14 prompts affected
Visibility: 61% → 37%
Competitor share: 21% → 54%
```

If the system does not know the start time exactly:

```text
First observed: 2026-10-02 09:00 PT
```

Do not fabricate an exact cause time.

---

# 17. “Why It Happened?” Block

Show ranked hypotheses, not one absolute claim.

Example:

```text
Why it happened

1. Competitor published a dedicated SAML SSO page
   confidence 0.84

2. Three newly cited sources now reference that page
   confidence 0.71

3. Our SSO information exists only in a nested support page
   confidence 0.63
```

Each hypothesis must be expandable.

Expanded state shows:

- evidence IDs
- source URLs/domains
- observed timestamps
- supporting snippets
- contradiction/support score
- model or heuristic responsible

Do not hide uncertainty.

---

# 18. Evidence Graph

The evidence graph is the one intentionally technical visual in the product.

It must remain small, readable, and directly tied to the selected incident.

It is **not** a global knowledge graph.

Suggested layout:

```text
              [Visibility Loss]
                    │
        ┌───────────┼────────────┐
        │           │            │
[Competitor]   [Citations]   [Our Content]
     │             │              │
[new page]     [source A]      [nested page]
               [source B]
```

Node classes:

```text
Profound signal
Owned content
Competitor content
External source
Inference / hypothesis
Prompt cluster
Experiment
```

Edges should encode relationships like:

```text
cites
supports
contradicts
changed_before
associated_with
contains_claim
competes_with
triggered
```

### Graph interaction

Hover/click node → small inspector.

Inspector shows:

```text
Title
Type
Source
Observed at
Confidence
Relevant extract
Open source
```

Do not implement graph editing.

Do not make the user manually connect nodes.

---

# 19. Affected Prompts Tab

This view shows exactly which queries are affected.

Use a compact table.

Columns:

```text
Prompt
Intent
Volume
Our visibility
Competitor
Engine(s)
Change
```

Example:

```text
best analytics platform with enterprise SSO
High intent
1.2K
37%
Acme 54%
ChatGPT, Gemini, Perplexity
-24pp
```

Prompt rows may expand to show:

- recent answers
- citations
- historical measurements
- persona
- model / region metadata

Do not expose raw API payloads here.

---

# 20. Evidence Tab

This is the evidence ledger for the incident.

Use a source-oriented layout.

Sections:

```text
Owned Sources
Competitor Sources
External Sources
Profound Signals
```

Each evidence row:

```text
Source title
Domain
Evidence type
Observed timestamp
Support / contradiction score
Freshness
Confidence
```

Expand row → passage/snippet + metadata.

Required source integrity states:

```text
Live
Changed
Stale
Unavailable
Fetch failed
```

The UI must never silently treat fetch failure as “no evidence.”

---

# 21. Action Tab

This is where AEO SRE differs from ordinary analytics tools.

The action view must answer:

```text
What should we do?
Why this action?
What else could we do?
What is the risk?
What happens after approval?
```

---

# 22. Recommended Intervention Card

Example:

```text
Recommended action

Update existing enterprise security page

Why
Our SAML capability exists, but authoritative owned content is weak
relative to newly cited competitor content.

Policy confidence
0.71

Risk
Low

Executor
Manual

Target
/enterprise/security (the page to change)
```

The target is the page or resource the change applies to. The UI is executor-agnostic: it shows
`Executor: Manual` by default and names GitHub (or any other adapter) only when an operator explicitly
selected that executor. No PR / branch language appears in default flows.

Primary CTA:

```text
Approve
```

Secondary:

```text
Modify
```

Tertiary:

```text
Reject
```

---

# 23. Alternative Actions

Show alternatives below the primary recommendation.

Example:

```text
Create dedicated canonical page       0.62
Add FAQ section                       0.48
Create comparison content             0.31
Publisher outreach                    0.28
Structured data update                0.22
Observe                               0.14
```

Do not call these “AI suggestions.”

Call them **candidate interventions**.

If the policy is cold-start:

```text
Selection basis: cold-start prior
```

If learned:

```text
Selection basis: policy v0.4.2
Based on 38 related experiments
```

---

# 24. Approval Flow

Approval must be explicit.

Clicking `Approve` opens a confirmation drawer/modal. **Approving activates an experiment**; the action is then applied by the human (Manual executor, the default) or by an adapter an operator selected. The modal contains:

```text
Intervention
Affected resource
Exact proposed change
Evidence summary
Risk level
Rollback mechanism
Executor
Expected observation window
```

Buttons:

```text
Approve
Cancel
```

If the intervention changes code/content, show the exact diff before approval when possible.

Never make approval a one-click invisible mutation.

### After approval: the intervention package

The Action tab shows the **Intervention Package** issued by the executor: target page/URL, the exact change
(diff + copyable text), steps checklist, evidence summary, risk, rollback note and expected observation window.
The primary CTA becomes `Mark as executed`, which opens a form:

```text
When did you apply it?   (not in the future)
Reference URL            (optional)
Note                     (optional)
[ ] I changed something different  -> paste exactly what you applied (stored verbatim, flagged as a deviation)
```

Submitting calls `POST /api/interventions/{id}/executed`. Nothing is optimistic: the UI re-fetches and then shows
`Awaiting Measurement`. A manual execution is a real execution and is never labeled "Never Measured".
`observe` has no human step: approval starts observation immediately.

---

# 25. Live Investigation Stream

During investigation, expose the backend execution stream.

This should feel like a calm incident timeline, not a fake terminal.

Example:

```text
11:24:03  Fetching affected prompts
11:24:04  14 prompts loaded
11:24:05  Retrieving citation changes
11:24:07  21 sources analyzed
11:24:09  Crawling competitor source
11:24:11  Evidence model scored 18 passages
11:24:13  Generated 4 root-cause hypotheses
11:24:15  Intervention policy selected update_page
```

Use Server-Sent Events.

Status types:

```text
pending
running
success
warning
failed
waiting
```

Failed steps must remain visible.

---

# 26. Incident Lifecycle States

Frontend must support these states:

```text
Detected
Investigating
Needs Review
Ready for Action
Executing
Awaiting Measurement
Verified
Resolved
Dismissed
Failed
```

State controls UI behavior.

Example:

### Investigating

Primary CTA disabled or becomes:

```text
Investigation running
```

### Ready for Action

```text
Approve  →  (after approval) Mark as executed
```

### Awaiting Measurement

```text
Awaiting post-intervention observation
```

### Verified

Show actual before/after values and experiment reward.

---

# 27. Primary Tab 2 — Experiments

Route:

```text
/experiments
```

Purpose:

> Show what the system changed, what happened afterward, and what it learned.

This is not an analytics warehouse.

It is the system's **intervention memory**.

---

# 28. Experiments Page Structure

Use one primary table with a compact status summary above it.

Top summary may contain only:

```text
Running
Awaiting Measurement
Verified
```

No more than three summary cards.

---

# 29. Experiments Table

Columns:

```text
Experiment
Incident
Action
Started
Status
Before
After
Reward
Policy
```

Example:

```text
EXP-1042
Enterprise SSO visibility drop
Update page
Oct 2, 11:32
Awaiting Measurement
37%
—
—
v0.4.2
```

Verified example:

```text
EXP-0998
Incorrect pricing answer
FAQ + canonical update
Sep 27
Verified
44%
59%
+0.63
v0.4.1
```

---

# 30. Experiment Detail

Route:

```text
/experiments/:id
```

The page should read like a scientific experiment record.

Sections:

```text
Experiment summary
Why this intervention was selected
Context at decision time
Evidence snapshot
Exact action executed
Approval record
Before metrics
After metrics
Reward decomposition
Policy information
Timeline
```

---

# 31. Experiment Outcome Visualization

Do not use massive charts.

Use a compact before/after comparison.

Example:

```text
Visibility        37% → 48%     +11pp
Citation share    14% → 21%     +7pp
Accuracy          92% → 92%      0pp
Competitor share  54% → 50%     -4pp
```

Then show reward:

```text
Total reward: +0.63
```

Expand:

```text
Visibility component     +0.31
Citation component       +0.18
Accuracy component        0.00
Competition component    +0.09
Action cost              -0.03
Risk penalty             -0.02
```

This is crucial for transparency.

---

# 32. Learned Policy Surface

Do not create a separate Learning tab.

Policy information belongs inside Experiments.

Show:

```text
Policy version
Selection basis
Related historical experiments
Cold-start / learned state
Action probability or score
```

Example:

```text
Policy v0.4.2

Selected action: update_page
Selection score: 0.71
Related experiments: 38
Cold start: No
```

Optional compact expandable section:

```text
Alternative scores
```

---

# 33. Primary Tab 3 — Settings

Route:

```text
/settings
```

Settings should be functional, not decorative.

Use four internal sections:

```text
Organization
Integrations
Policy
System
```

These can be local tabs inside Settings.

---

# 34. Settings — Organization

Fields:

```text
Organization name
Primary domain
Competitor domains
Canonical documentation domains
Priority personas
Priority topic groups
```

Do not let the user configure 50 scoring parameters here.

Advanced configuration can live in backend config files initially.

---

# 35. Settings — Integrations

Cards:

```text
Profound
LLM Provider
Executors: Manual (always available), GitHub (optional), Profound agent (unavailable), CMS (optional, not built)
```

Each integration shows:

```text
Connected / Not connected
Last successful request
Last error
Reconnect / Configure
```

Never display secret values after save.

---

# 36. Settings — Policy

Keep this understandable.

Show:

```text
Current policy version
Learning mode
Cold-start status
Allowed actions
Human approval requirement
Minimum confidence for automatic recommendation
```

Allowed action toggles:

```text
Observe
Update page
Create canonical page
Add FAQ
Comparison content
Publisher outreach
Structured data
```

Execution remains approval-gated.

---

# 37. Settings — System

Show operational health:

```text
API
Database
Redis
Workers
Profound connector
Crawler
Evidence model
Policy model
```

Each:

```text
Healthy
Degraded
Unavailable
```

Also expose:

```text
Last ingestion
Last investigation
Last policy update
Model artifact version
```

This is enough.

---

# 38. Visual Design Direction

The final visual system should remain close to the approved mockup:

- deep charcoal / near-black background
- restrained neon indigo / electric blue primary accent
- teal for positive/healthy states
- warm amber for warning
- red only for incident severity and destructive/error states
- very subtle glass surfaces
- thin borders
- low-radius or medium-radius cards
- dense but breathable information spacing
- high-contrast typography
- light glow only around selected/high-priority states

The application should feel like:

```text
incident response
+ observability
+ scientific experiment console
```

not:

```text
crypto dashboard
cyberpunk game UI
AI art generator
```

---

# 39. Styling Phase Rule

For the current build phase:

> **Implement structure and state first. Do not spend time polishing CSS.**

Frontend implementation priority:

```text
1. routing
2. data fetching
3. loading states
4. error states
5. SSE updates
6. forms/actions
7. approval flow
8. empty states
9. responsive layout
10. visual polish
```

Plain semantic HTML is acceptable before styling.

Do not block backend integration on visual polish.

---

# 40. Typography

Later styling recommendation:

Use one clean sans-serif family.

Suggested classes:

```text
Display        28–32px
Page title     24–28px
Section title  16–18px
Card title     14–16px
Body           13–15px
Meta           11–13px
Metric         22–30px
```

Avoid huge hero text.

This is an operational application.

---

# 41. Spacing

Use a compact system.

Suggested spacing scale:

```text
4
8
12
16
20
24
32
```

Incidents and evidence should remain information-dense without becoming cramped.

Do not use marketing-site spacing such as 80–120px vertical gaps inside the app.

---

# 42. Cards

Cards should have one of four functions:

```text
Metric
Incident
Evidence
Action
```

Never create a card merely to box text.

Nested cards should be used sparingly.

Maximum nested depth:

```text
2
```

---

# 43. Color Semantics

Color must have stable meaning.

```text
Blue / Indigo    selected / primary / system
Teal / Green     positive / verified / healthy
Amber            warning / medium confidence / pending
Red              critical / negative / failure
Muted gray       neutral / unavailable / historic
Purple           inference / model / policy-related data
```

Do not randomly assign colors to every chart.

---

# 44. Charts

Only use charts when trend matters.

Allowed:

- tiny sparkline
- small time-series line chart
- before/after visualization
- evidence DAG

Avoid:

- pie charts
- donut charts
- radar charts
- stacked area walls
- 3D charts

Every chart must have textual values beside it.

---

# 45. Motion

Motion should communicate state, not decorate.

Allowed:

- selected incident transition
- loading skeleton
- subtle live pulse on healthy connection indicator
- graph node highlight
- drawer/modal transition
- streaming timeline insertion

Avoid:

- floating cards
- parallax
- giant glowing loops
- continuously moving background gradients
- excessive number animations

---

# 46. Loading States

Every async surface needs an explicit loading state.

Examples:

### Incident queue

```text
Loading incidents…
```

### Investigation

Show live steps as they arrive.

### Evidence

```text
Fetching sources…
```

### Policy

```text
Computing intervention policy…
```

Never leave blank cards during loading.

---

# 47. Empty States

## No incidents

```text
No actionable incidents detected.
Monitoring is active.
Last sync: 3 minutes ago.
```

Do not show confetti.

## No experiments

```text
No interventions have been executed yet.
Experiments appear here as soon as an intervention is approved.
```

## No evidence

```text
No supporting evidence was retrieved for this hypothesis.
```

Not:

```text
Nothing here yet!
```

---

# 48. Error States

Be exact.

Bad:

```text
Something went wrong.
```

Good:

```text
Profound API request failed.
Last successful sync: 11:02 PT.
Incident detection is running on previously ingested data.
```

For failed crawl:

```text
Could not retrieve competitor page.
HTTP 403 returned at 11:24 PT.
```

Always distinguish:

```text
Unavailable data
No data
Failed data
```

---

# 49. Data Freshness

Every major data surface should expose freshness.

Examples:

```text
Last updated 4m ago
Observed 2h ago
Fetched 11:24 PT
Profound sync 8m ago
```

Avoid displaying values without temporal context.

---

# 50. Confidence

Confidence should never be encoded only through color.

Use number + label where useful:

```text
0.84 · High confidence
0.63 · Medium confidence
0.31 · Low confidence
```

Do not turn all confidence values into percentages if backend uses a 0–1 score.

Stay faithful to backend semantics.

---

# 51. Frontend Data Contracts

The UI should consume typed API responses.

Do not shape business logic inside Vue components.

Suggested TypeScript interfaces:

```ts
interface IncidentSummary {
  id: string
  title: string
  severity: 'critical' | 'high' | 'medium' | 'low'
  status: IncidentStatus
  detectedAt: string
  topic?: string
  primaryDelta?: MetricDelta
  trend?: number[]
}

interface MetricDelta {
  label: string
  before?: number
  after?: number
  value?: number
  delta?: number
  unit?: string
}

interface Hypothesis {
  id: string
  title: string
  summary: string
  confidence: number
  evidenceIds: string[]
}

interface EvidenceItem {
  id: string
  type: 'profound' | 'owned' | 'competitor' | 'external' | 'inference'
  title: string
  source?: string
  url?: string
  observedAt?: string
  supportScore?: number
  contradictionScore?: number
  freshnessRisk?: number
  status: 'live' | 'changed' | 'stale' | 'unavailable' | 'failed'
}

interface InterventionCandidate {
  action: string
  title: string
  score: number
  risk: 'low' | 'medium' | 'high'
  reason: string
  selected: boolean
  policyVersion?: string
  coldStart: boolean
}
```

---

# 52. State Management

Use Pinia only where global state is justified.

Suggested stores:

```text
useOrganizationStore
useLiveSystemStore
useIncidentSelectionStore
```

Do not put every API response into Pinia.

Page-local server state should remain page-local.

Prefer Nuxt `useFetch` / `useAsyncData` plus typed composables.

---

# 53. Suggested Composables

```text
useIncidents()
useIncident(id)
useIncidentEvents(id)
useIncidentEvidence(id)
useIncidentPrompts(id)
useInterventions(id)
useApproveIntervention(id)
useExperiments()
useExperiment(id)
useSystemHealth()
```

Each composable should have one responsibility.

---

# 54. Server-Sent Events Contract

Endpoint:

```text
GET /api/incidents/:id/events
```

Example event:

```json
{
  "id": "evt_123",
  "incident_id": "1042",
  "timestamp": "2026-10-02T11:24:09-07:00",
  "stage": "crawl_competitor",
  "status": "running",
  "message": "Crawling competitor source",
  "metadata": {
    "url": "https://example.com/security/saml"
  }
}
```

Frontend renders events chronologically.

Do not rewrite backend messages into invented status language.

---

# 55. Optimistic UI Rules

Do **not** optimistically mark actions as executed.

Examples:

### Approval

After submit:

```text
Approval submitted
```

Then wait for backend execution state.

### Execution

Do not show:

```text
Successfully deployed
```

until backend confirms it.

### Experiment result

Do not calculate reward in frontend.

---

# 56. Responsive Behavior

Desktop is primary.

Tablet and mobile still need functional access.

## Desktop

Two-column incident workspace.

## Tablet

Incident queue becomes narrower and detail remains dominant.

## Mobile

Use drill-down navigation:

```text
Incident list
→ incident detail
→ evidence/action
```

Do not attempt to preserve the two-column desktop layout on mobile.

The nav rail becomes a compact drawer.

---

# 57. Keyboard Behavior

Useful shortcuts can be added later, but structure should support:

```text
j / k          next / previous incident
Enter          open selected incident
Esc            close drawer/modal
/              focus search
```

Do not make core features keyboard-only.

---

# 58. Accessibility

Required:

- semantic headings
- accessible buttons
- focus states
- labels for severity icons
- color-independent status indicators
- table headers
- modal focus trapping
- reduced motion support
- screen-reader text for sparklines/trends

Every graph must have a textual evidence alternative.

---

# 59. Component Architecture

Suggested Nuxt component tree:

```text
components/
├── app/
│   ├── AppNavRail.vue
│   ├── AppTopBar.vue
│   ├── SystemStatus.vue
│   └── OrganizationSwitcher.vue
│
├── incidents/
│   ├── IncidentQueue.vue
│   ├── IncidentCard.vue
│   ├── IncidentHeader.vue
│   ├── IncidentMetricStrip.vue
│   ├── IncidentAnalysis.vue
│   ├── IncidentHypothesis.vue
│   ├── IncidentEvidenceGraph.vue
│   ├── IncidentPromptTable.vue
│   ├── IncidentEvidenceList.vue
│   ├── IncidentActionPanel.vue
│   ├── InterventionCandidate.vue
│   ├── ApprovalDialog.vue
│   └── InvestigationTimeline.vue
│
├── experiments/
│   ├── ExperimentTable.vue
│   ├── ExperimentSummary.vue
│   ├── ExperimentOutcome.vue
│   ├── RewardBreakdown.vue
│   └── PolicySnapshot.vue
│
├── settings/
│   ├── IntegrationCard.vue
│   ├── PolicySettings.vue
│   └── SystemHealthPanel.vue
│
└── shared/
    ├── MetricCard.vue
    ├── StatusBadge.vue
    ├── SeverityBadge.vue
    ├── ConfidenceBadge.vue
    ├── DataFreshness.vue
    ├── EmptyState.vue
    ├── ErrorState.vue
    └── LoadingState.vue
```

Avoid one giant `IncidentPage.vue` with thousands of lines.

---

# 60. Page Architecture

Suggested pages:

```text
pages/
├── index.vue
├── incidents/
│   ├── index.vue
│   └── [id].vue
├── experiments/
│   ├── index.vue
│   └── [id].vue
└── settings.vue
```

`index.vue` redirects to `/incidents`.

---

# 61. API Error Boundary Strategy

Each major surface should fail independently.

Example:

If evidence fetch fails:

```text
Incident summary still renders.
Metrics still render.
Action recommendation remains hidden if evidence is incomplete.
Evidence panel shows explicit error.
```

Do not make the entire page crash because one secondary endpoint failed.

---

# 62. Evidence Integrity Rules

The UI must preserve provenance.

Every evidence item shown should be traceable to:

```text
source URL / Profound source
retrieved timestamp
content hash / evidence ID
model score if applicable
```

Never show model-generated paraphrase as if it were quoted source text.

Use different styling for:

```text
Source extract
System interpretation
```

---

# 63. Recommendation Integrity Rules

Every recommendation must show whether it was based on:

```text
Cold-start prior
Learned policy
Rule fallback
Manual override
```

Example:

```text
Selection basis
Policy v0.4.2 · learned from 38 related experiments
```

or:

```text
Selection basis
Cold-start prior · no related verified experiments yet
```

This prevents fake intelligence.

---

# 64. Human Override

The user must be able to:

```text
Approve recommended action
Select an alternative action
Modify proposed change
Reject all actions
Choose Observe
```

Human choice must be written into the experiment log.

The UI should not hide this behind an advanced menu.

---

# 65. “Observe” Is a First-Class Action

Sometimes the correct intervention is no intervention.

Show:

```text
Observe
```

alongside other candidate actions when appropriate.

If selected:

```text
Incident remains open
System monitors next measurement window
No external mutation occurs
```

This is important for credibility.

---

# 66. Expected Outcome UI

Do not display fake deterministic predictions like:

```text
Visibility will recover +12pp in 4 days.
```

Allowed:

```text
Historical range from similar verified experiments
+8 to +15pp
n = 12
```

or:

```text
Insufficient experiment history to estimate outcome.
```

Every expected-outcome estimate needs sample size or confidence context.

---

# 67. Incident Priority UI

Priority score may exist, but must be decomposable.

Example:

```text
Priority 91 / 100
```

Expand:

```text
Prompt demand              94
Commercial intent          96
Severity                   88
Competitive displacement   92
Evidence confidence        84
Fix feasibility            91
```

Never imply this is revenue.

Do not show `$2.7M opportunity` unless real revenue data supports it.

---

# 68. Date and Time Rules

Display local user time, but preserve exact timestamps in API data.

Use:

```text
Detected 2h ago
```

with hover/detail:

```text
Oct 2, 2026 · 09:12:31 PT
```

For historical experiment comparisons, always show absolute dates.

---

# 69. Product Language

Preferred terminology:

```text
Incident
Signal
Evidence
Hypothesis
Intervention
Experiment
Observation
Reward
Policy
Verification
```

Avoid:

```text
Magic
Brain
Autopilot
Copilot
Super Agent
AI Employee
Growth Hacker
```

The product should feel serious.

---

# 70. Copy Style

Use short, operational language.

Bad:

```text
Our advanced AI-powered system has intelligently determined that your
brand may potentially be experiencing a visibility degradation event.
```

Good:

```text
Visibility dropped 24pp across 14 high-intent SSO prompts.
```

Bad:

```text
AI recommends optimizing your content.
```

Good:

```text
Update the enterprise security page to make existing SAML support explicit.
```

---

# 71. Do Not Expose Internal Agent Chatter

Do not render:

```text
Research Agent says…
Investigator Agent says…
Planner Agent says…
```

Users care about evidence and actions, not agent personalities.

The live timeline may show stages:

```text
Collecting citations
Ranking evidence
Generating hypotheses
Computing intervention policy
```

That is enough.

---

# 72. No Demo-Only UI

Do not implement:

```text
/demo
/demo-mode
sample incident toggle
fake live switch
hardcoded outcome animation
```

A seed fixture may exist only for local development/testing.

Development fixtures must be clearly marked and never mixed into live production data.

---

# 73. Development Sequence for the Frontend

Build in this order:

## Phase 1 — Skeleton

- Nuxt routing
- App shell
- three top-level tabs
- incident queue
- incident detail route
- experiments table
- settings form shell

## Phase 2 — Backend wiring

- typed composables
- loading states
- errors
- empty states
- live system health

## Phase 3 — Incident lifecycle

- investigation trigger
- SSE timeline
- hypothesis rendering
- evidence rendering
- intervention candidates
- approval flow

## Phase 4 — Experiment lifecycle

- experiment record
- waiting state
- verified result
- reward breakdown
- policy snapshot

## Phase 5 — Visual polish

Only after end-to-end functionality works:

- spacing
- typography
- dark theme
- severity colors
- graph styling
- subtle motion
- responsive refinement

---

# 74. Acceptance Criteria — Incidents

The Incidents UI is complete when a user can:

1. see unresolved incidents
2. filter by severity
3. select an incident
4. understand what changed
5. inspect affected prompts
6. inspect evidence
7. understand ranked root-cause hypotheses
8. view the recommended intervention
9. view alternative interventions
10. approve, modify, reject, or observe
11. watch investigation progress live
12. see the incident transition into experiment state

---

# 75. Acceptance Criteria — Experiments

Experiments UI is complete when a user can:

1. see all executed interventions
2. distinguish running vs waiting vs verified
3. open an experiment
4. see why the action was selected
5. see the exact intervention
6. see approval history
7. compare before and after metrics
8. inspect reward decomposition
9. inspect policy version and decision basis
10. navigate back to the originating incident

---

# 76. Acceptance Criteria — Settings

Settings UI is complete when a user can:

1. configure organization domain
2. define competitor domains
3. see Profound integration state
4. see executor state (Manual always available; GitHub optional)
5. see model/provider state
6. see current policy version
7. control allowed intervention categories
8. verify approval is required
9. inspect system health

---

# 77. Final Screen Composition

The primary incident screen should visually read in this order:

```text
PAGE TITLE
↓
INCIDENT QUEUE  |  SELECTED INCIDENT
                |
                |  Incident header + severity
                |
                |  3–4 key metrics
                |
                |  Analysis / Prompts / Evidence / Action
                |
                |  What happened?
                |  Why it happened?
                |  Evidence graph
                |  Recommended action
                |  Alternatives
                |  Approval
```

That is the product.

Everything else is secondary.

---

# 78. Final Design Standard

Before adding any UI element, ask:

> Does this help the user understand an incident, decide what to do, execute safely, or learn from the result?

If the answer is no, remove it.

The finished application should feel unusually focused for an AI product.

The backend can be complex.

The interface should make that complexity disappear into a simple operational loop:

```text
DETECT
→ UNDERSTAND
→ ACT
→ VERIFY
→ LEARN
```

That is the complete UI direction for AEO SRE.

---

# 79. Shipped 3D-Blended Operational Interface Implementation

In October 2026, the frontend was elevated to the authoritative high-fidelity 3D-blended operational standard matching reference mockups:

1. **Global Shell & Ambient Field:**
   - Dark-navy / graphite environment token system (`#060911` base, `#0a0e1a` canvas, `#0f1526` panels).
   - Low-overhead ambient 3D topological flow background (`AmbientField.vue`) in Three.js with slow, faint wave ribbons, zero click interception, tab-visibility pausing, and `prefers-reduced-motion` compliance.
   - High-fidelity left navigation rail (`AppNavRail.vue`) with custom geometric glowing emblem, 3-item navigation (Incidents, Experiments, Settings), real-time monitoring card (`SystemStatus.vue`) with expandable capability health drawer, and organization switcher modal (`OrganizationSwitcher.vue`).
   - Clean top bar (`AppTopBar.vue`) with contextual titles and subtitles, keyboard-focusable search with shortcut `/`, live SSE heartbeat pill, and date range dropdown.

2. **Incidents Center:**
   - Severity-filtered queue with counts (All, Critical, High, Medium) and interactive cards with category icons, delta tags, time, and trendlines. Selected cards lift with 3D spatial depth (`translateZ(4px)` and electric indigo glow).
   - Incident Header with rounded square category badge, title, status, ID pill, truthful provenance tag (`Live Profound` vs `Test fixture`), and primary CTA button.
   - 4 Dimensional Metric cards: Visibility, Citation Share, Prompt Volume, Competitor Share.
   - 3-Step Numbered Analysis flow:
     1. What happened? Narrative + affected prompt callout card with visibility shift.
     2. Why it happened? Root-cause hypotheses with confidence badges + linked evidence card.
     3. Recommended action: Policy-selected intervention with score, basis, and Approve & Execute / Modify / Reject actions.
   - **Signature 3D Evidence Graph (`EvidenceScene.vue`)**:
     - Three.js WebGL spatial DAG with deterministic depth encoding: Incident root (z=0), Signals/Prompts (z=-3), External/Competitor sources (z=-7), Hypotheses (z=+4), Intervention (z=+8).
     - Color-coded node families with custom readable sprite textures, directional bezier spline curves, camera orbit/pan/zoom with clamp, Reset View button, and raycasting hover/click syncing with the DOM-side Node Inspector.
     - Accessible 2D/DOM fallback toggle and screen reader text alternative.
   - Gated Expected Outcome panel with strict cold-start notice when historical calibrating data is absent.

3. **Experiments Ledger:**
   - 4 Summary cards: Active Experiments, Awaiting Verification, Rewarded, Policy Version.
   - 2-Column Ledger workspace: Recent Experiments queue on the left (code, provenance tag, title, action, status badge, date) and Selected Experiment detail on the right.
   - **9-Step Causal Spine (`ExperimentSpine.vue`)**:
     - 1. Hypothesis → 2. Intervention (with View changes diff dialog) → 3. Approval → 4. Baseline Before metrics → 5. Verification Window (highlighting explicit `Eligible after <UTC timestamp>`) → 6. After Metrics (truthful pending placeholders) → 7. Outcome → 8. Reward → 9. Learning.
     - Spatial CSS 3D styling: settled past steps, active pulsing glowing current step, dim future steps.
   - Before → After primary metric grouped bars with color-coded comparison.
   - Policy Learning contextual bandit panel with candidate action score bars and selected action highlight.
   - Potential Confounders card and Evidence & Provenance artifact pills.

# 80. CURRENT UI SCOPE — CHANGE GUARD (Binding Authority)

This section supersedes older UI sections where they conflict. It defines the authoritative specification for the **Profound Change Guard** operational interface.

---

## 1. Product Mission & Architectural Model

Profound Change Guard is a cross-agent change-control and experiment-protection layer for Profound.

Autonomous marketing agents (Profound Agents, AI Marketers, human contributors) operate concurrently. Change Guard acts as an **air-traffic control system for marketing changes**, evaluating proposals against:
1. **Other active changes** (detecting collisions, duplicate intent, and merge opportunities).
2. **Active experiments** (protecting targets currently under causal measurement).
3. **Canonical brand truth** (preventing hallucinated or contradictory product/plan claims).
4. **Target and claim overlap** across prompt clusters and verification timing.

Decisions returned by the backend engine:
- `ALLOW` (mint / teal): No conflicts detected; safe for immediate execution.
- `MERGE` (blue): Compatible duplicate; merged ChangeSet suggested.
- `DELAY` (amber): Target locked by active experiment; held until `eligible_after` timestamp.
- `REQUIRE REVIEW` (violet): Intent or claim difference; human operator must review and supply reason.
- `BLOCK` (controlled red): Direct contradiction of registered canonical brand truth.

---

## 2. Information Architecture & Navigation

The primary navigation rail (`AppNavRail.vue`) is intentionally restrained to keep operational content dominant:
- **Change Guard** (`/incidents`): Real-time change queue, 3D Change Topology DAG, impact strip, and decision panel.
- **Experiments** (`/experiments`): Protected experiments ledger, 9-step causal spine, and incoming change conflicts.
- **Settings** (`/settings`): Integration status, Canonical Truth editor, and Profound Agent API endpoint integration.
- **Bottom Status Rail**: Live capability health drawer (`SystemStatus.vue`), last ingestion timestamp, and Organization switcher.

---

## 3. Tab 1 — Change Guard Workspace

Answers: **"What is trying to change right now, and is it safe?"**

### Change Queue
- Cards show: Agent / Source, proposed action type, target URL, guard decision chip, risk level, relative time, conflict count, and provenance (`Live Profound` vs `Test fixture`).
- Decision styling: Restrained semantic edge borders and status chips; never full-card oversaturation.
- Elevation on selection: Card subtly scales to `1.01`, gains depth shadow and accent left edge border.
- Filters: `All`, `Conflicts`, `Waiting`, `Safe`.
- Empty state: *"No change conflicts. Monitoring Profound Agent activity."* or *"Profound is not connected. Existing experiments remain available."*

### Change Header
- Title: Target surface (e.g. `UPDATE /enterprise/security`).
- Source: Submitting agent name (e.g. `Citation Recovery Agent`).
- Decision chip: Evaluated verdict (`ALLOW`, `MERGE`, `DELAY`, `REQUIRE_REVIEW`, `BLOCK`).
- Secondary metadata: Agent run ID, project, provenance, and Action Digest badge.

### Action Digest UI
- Displays `Action locked` pill with expandable popover showing the SHA-256 cryptographic digest.
- If an approved action is mutated downstream, an **APPROVAL INVALIDATED** alert triggers: *"The approved action changed. Review is required again."*

### Impact Strip
Maximum 4 dimensional cards:
1. **Targets**: Target URLs touched.
2. **Claims**: Count of proposed claims asserted.
3. **Prompt Clusters**: Impacted search queries and search visibility topics.
4. **Conflicts**: Detected experiment collisions or canonical truth contradictions.

### Decision Panel
- Clear decision typography and explanation.
- For `DELAY`: Displays overlapping experiment code (`EXP-0001`), target overlap (100%), prompt cluster overlap (82%), and exact protected until timestamp (`2026-10-04 · 20:29 UTC`).
- For `REQUIRE_REVIEW`: Provides required human review reason input bound to the approval payload.
- For `MERGE`: Renders suggested merged ChangeSet diff.
- Actions: Queue Change, Request Review, Dismiss, and Approve Intervention.

### Claim Conflict View (`ClaimConflictView.vue`)
- Renders side-by-side comparison when canonical brand truth is contradicted:
  - **Proposed Claim**: Assertion from the agent proposal.
  - **VS Divider**.
  - **Canonical Truth**: Registered organizational statement and source.
  - **Contradiction Detected** callout badge with verified underlying evidence excerpt.

---

## 4. Signature 3D Visualization — Change Topology (`EvidenceScene.vue`)

Evolved from the 3D evidence DAG into a real-time semantic change topology:

### Node Types
- `AGENT`: Profound Agent / source actor (`#6366f1` Indigo).
- `CHANGESET`: Proposed change unit (`#38bdf8` Sky).
- `TARGET`: URL / page surface (`#10b981` Emerald).
- `CLAIM`: Asserted claim (`#2dd4bf` Teal).
- `PROMPT CLUSTER`: Impacted query topic (`#8b5cf6` Purple).
- `EXPERIMENT`: Active experiment (`#3b82f6` Blue with protected ring).
- `CANONICAL TRUTH`: Registered brand truth (`#06b6d4` Cyan).
- `CONFLICT`: Detected collision / contradiction (`#f43f5e` Rose).

### Deterministic Semantic Depth
- `z = -8`: Profound Agents / Sources.
- `z = -4`: ChangeSets / Proposals.
- `z =  0`: Targets / Claims / Root context.
- `z = +4`: Conflicts / Dependencies.
- `z = +8`: Decisions / Protected Experiments.

### Visual State Behaviors
- **Protected Orbit**: Active experiments render a rotating glowing torus boundary around the protected target node.
- **Collision Edge**: Connecting edges to conflicts or protected zones render in high-visibility amber/rose with pulse animation.
- **Node Inspector**: Raycaster click opens the DOM-side Node Inspector panel displaying full metadata, timestamps, excerpt, and external URL.
- **Assistive Fallback**: Screen-reader textual topology region and toggleable 2D SVG schema view.

---

## 5. Tab 2 — Protected Experiments Ledger

Answers: **"What marketing changes are currently under protected measurement?"**

### Experiments Summary
Four primary cards:
1. **Active**: Experiments currently executing or in verification.
2. **Protected Targets**: Target URLs guarded against change contamination.
3. **Awaiting Verification**: Experiments in their temporal measurement window.
4. **Completed**: Finished experiments with measured causal rewards.

### Refined 9-Step Causal Spine (`ExperimentSpine.vue`)
1. **Intervention**: Action title and View Changes diff drawer.
2. **Approval**: Decided by operator and timestamp.
3. **Protected Targets**: Guarded URL (`/enterprise/security`), prompt cluster, claims count, and `Guarded until` timestamp.
4. **Baseline (Before)**: 7-day pre-intervention metrics (Visibility, Citation Share, Competitor Share, Volume).
5. **Measurement Window**: Delay hours and exact `Eligible after` timestamp.
6. **Incoming Change Conflicts**: List of delayed incoming agent changes with direct links to Change Guard.
7. **After Metrics**: Measured post-window results (or truthful `—` pending placeholders).
8. **Outcome**: Conclusive / inconclusive causal verdict.
9. **Learning**: Policy bandit version and update status.

---

## 6. Tab 3 — Settings & Profound Integration

- **Integration Cards**: Live connectivity status for Profound (`CONNECTED`), Model Runtime (`READY`), Crawler, and Policy.
- **Canonical Truth Editor (`CanonicalClaimsEditor.vue`)**: Admin-managed statements of brand truth per organization. Writes enforce human operator `X-Actor` audit trails. Soft retirement (`status=retired`) preserves historical verification integrity.
- **Profound Agent Integration (`ProfoundAgentIntegration.vue`)**:
  - Exposes Change Guard endpoint: `POST /api/change-checks` with click-to-copy.
  - Interactive workflow diagram: `Profound Agent` → `Call API Node` → `Change Guard` → `Decision Returned`.
  - Sample structured ChangeSet JSON payload clearly marked `SIMULATED AGENT`.

---

## 7. Performance, Motion & Accessibility Standards

- **Ambient Background (`AmbientField.vue`)**: Low-power Three.js flow field with faint opacity (0.12), paused when document hidden or when `prefers-reduced-motion` is enabled.
- **WebGL Lifecycle**: Clean disposal of geometries, textures, materials, and requestAnimationFrame loops on unmount. Graceful fallback to 2D view if WebGL is unavailable.
- **Accessibility**: All status indicators use text labels and glyphs in addition to color; full keyboard navigation and screen-reader textual topology regions provided.
- **Responsive Layout**: Designed for 1280–1600px desktop monitors with collapsible queue on smaller displays.


---

# 81. CURRENT UI — AGENTMATCH

## 1. Vision & Architecture

**AgentMatch** is an agent-native AI discovery command center that reconciles:
- **Personal Agent Demand (Muse Intent Envelope)**: Real-time structured requirements and constraints captured from buyer personal agents.
- **Product Truth (Neo4j / Canonical Knowledge Graph)**: Ground reality verified from official domain documentation and capability specifications.
- **AI Perception (Profound AI Engine)**: Synthesized answers and citations across generative answer engines (ChatGPT, Claude, Perplexity, Gemini).

When product truth satisfies personal agent constraints but AI engines underrepresent or misrepresent the brand, AgentMatch surfaces a **Discovery Gap** with measured percentage-point discrepancies and recommended causal actions.

## 2. Information Architecture: Exactly 3 Primary Nav Pages

1. **Matches (`/matches`)**:
   - Queue: Intent envelopes with personal agent context badges (`MUSE`, `MANUAL`, `TEST`), countdowns (`Expires in 27m`), and gap counts.
   - Fit Comparison Card: Actual Product Truth Fit (e.g. 94%) vs AI Perceived Engine Fit (e.g. 61%) and the resulting Discovery Gap callout (`+33pp Discovery Gap`).
   - 3D AgentMatch Graph (`AgentMatchScene.vue`) with strict semantic z-depth layers (`z=-8` Intent, `z=-4` Constraints, `z=0` Product, `z=+4` Claims, `z=+7` AI Perception, `z=+10` Discovery Gap) plus toggle to Accessible Semantic DOM Hierarchy Tree (`AgentMatchGraphTree.vue`).
   - Detailed Analysis: "Why It Matches", "Why AI Misses It", "Marketing Gap & Action", and "Verified Constraints" with expandable evidence proof drawer.

2. **Discovery Gaps (`/discovery-gaps`)**:
   - Queue: High-signal discovery gap issues filtered by type (`WRONG_TIER_PRICING`, `MISSING_CAPABILITY`, `STALE_INFORMATION`, `MISSING_CITATION`).
   - Side-by-side Truth Comparison: Ground truth canonical claim & source proof vs AI engine perception & root stale citation.
   - Compact Profound Panel: Brand Visibility, Citation Share, Prompt Coverage, Competitor Share, and dominant sources.
   - Action Approval: Approve, Modify, and Reject controls linked directly to the Experiments engine.

3. **Experiments (`/experiments`)**:
   - Closed-loop causal measurement engine with 9-step causal spine (`ExperimentSpine.vue`), temporal protection windows, before/after Profound perception metrics, and contextual bandit policy learning.

## 3. Bottom Controls & Integrations

- **Muse Connector**: Live personal agent demand status and intent envelope sync.
- **Profound Engine**: AI answer engine perception sync and visibility tracking.
- **System**: Live system health, capability breakdown, and organization switcher.


---

# 82. CAMPAIGNGRAPH ROI — CURRENT UI

## 1. Core Purpose

CampaignGraph ROI answers:
> "Where did the money go, what work did it produce, and what came back?"

It connects marketing investment all the way to observed and attributed business outcomes:
```text
Investment
→ People & Agents
→ Assets & Video
→ Distribution
→ Profound & Muse Signals
→ Business Outcomes & Attributed Return
```

## 2. Navigation Architecture

Primary navigation is constrained to:
- **AgentMatch** (`/matches`): Intent envelopes, verified constraints vs AI perception, and discovery gaps.
- **Campaigns** (`/campaigns`): Financial control center, cost lineage, human/agent models, asset provenance, Profound/Muse telemetry, and 3D Campaign Graph.
- **Experiments** (`/experiments`): Protected experiments, causal spine, before/after metrics, and policy learning.
- (Sub-surfaces for Costs, Agents, People, Assets, Videos, Profound, Muse, and Inefficiency are housed inside Campaigns).

## 3. Financial Summary & Top Strip

Maximum 5 high-value executive metrics:
1. **Total Cost** (e.g. `$42,780` across 7 cost categories)
2. **Attributed Return** (e.g. `$118,000` direct contracts & pipeline)
3. **ROI** (e.g. `1.76x`)
4. **Cost Completeness** (e.g. `92%` verified invoices & logged hours)
5. **Outcome Coverage** (e.g. `74%` Profound & Muse telemetry)

## 4. Explicit ROI Confidence & Provenance

ROI figures are never presented in isolation. Every return dollar is categorized by evidentiary source:
- **DIRECT**: Signed contracts and closed won revenue.
- **ATTRIBUTED**: CRM pipeline with touchpoint attribution.
- **MODELED**: Estimated downstream lifetime value.
- **PROXY**: Visibility and citation share shifts.
- Measurement Confidence badges: `HIGH`, `MEDIUM`, or `LOW`.

## 5. Cost Lineage & Breakdown

- **Composition**: Horizontal allocation bar across Paid Media, People, Video, Creators, Agents, Tools, and Model APIs.
- **Interactive Lineage Tree**: Clicking any cost branch unfolds exact children down to individual roles, contractor rates, studio editing fees, agent run counts, and token costs.
- **Cost Item Inspection Drawer**: Click any node to view provenance, contributor hours/rate, and trigger 3D path highlighting.

## 6. Contributor Models (People & Autonomous Agents)

- **Human Contributor Cost Model**: Tracks team members and contractors with hours, rate, total, source quality (`MANUAL` / `ESTIMATED` / `ACTUAL`), and delivered outputs.
- **Agent Cost & Efficiency**: Tracks autonomous agent runs, success rates, token usage, model costs, tool costs, approved outputs, and cost per approved output.
- **Video & Media Lineage**: Detailed breakdown of studio production, creator fees, editing, AI generation, revision iterations, and observed viewer outcomes.
- **Inefficiency Analysis**: Categorizes non-productive spend as `REWORK`, `DUPLICATE`, `ABANDONED`, or `UNKNOWN`.

## 7. 3D Campaign Lineage Graph (`CampaignGraphScene.vue`)

Centers on deterministic spatial z-levels:
- `z = -12`: Cost & Financial Inputs (amber `#f59e0b`)
- `z = -8`: People & Agents (cyan `#38bdf8` / indigo `#818cf8`)
- `z = -3`: Assets & Video Production (teal `#22d3ee`)
- `z = 0`: Campaign Nexus (white / indigo `#6366f1`)
- `z = +5`: Distribution Channels (blue `#60a5fa`)
- `z = +9`: Profound & Muse Signals (purple `#a855f7` / teal `#2dd4bf`)
- `z = +13`: Business Outcomes & Revenue (green `#10b981`)

Edge thickness reflects cost weight. Outcome edges differ by confidence (solid for DIRECT, dashed for ATTRIBUTED, muted for MODELED). Clicking any cost item or return metric highlights the active causal lineage path and dims all other nodes. Includes an **Accessible Semantic DOM Tree** (`CampaignGraphTree.vue`) and **Chronological Event Timeline**.
