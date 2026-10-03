<template>
  <div class="stack prose">
    <h1>AgentMatch Muse Connector</h1>
    <p>OAuth 2.0 + PKCE. API base: configure <code>API_PUBLIC_URL</code> on the backend.</p>
    <h2>Scopes</h2>
    <table>
      <thead><tr><th>Scope</th><th>Purpose</th></tr></thead>
      <tbody>
        <tr><td><code>matches:read</code></td><td>Find product matches</td></tr>
        <tr><td><code>discovery_gaps:read</code></td><td>AI perception gaps (Profound when configured)</td></tr>
        <tr><td><code>campaigns:read</code></td><td>Campaign summaries</td></tr>
        <tr><td><code>experiments:read</code></td><td>Experiment status</td></tr>
        <tr><td><code>feedback:write</code></td><td>Record explicit feedback (WRITE)</td></tr>
      </tbody>
    </table>
    <h2>Tools (v1)</h2>
    <ul>
      <li><strong>POST /api/muse/v1/find-matches</strong> — READ</li>
      <li><strong>POST /api/muse/v1/check-ai-perception</strong> — READ</li>
      <li><strong>POST /api/muse/v1/explain-match</strong> — READ</li>
      <li><strong>GET /api/muse/v1/campaigns/{id}/summary</strong> — READ</li>
      <li><strong>GET /api/muse/v1/experiments/{id}/status</strong> — READ</li>
      <li><strong>POST /api/muse/v1/feedback</strong> — WRITE (<code>feedback:write</code>)</li>
    </ul>
    <p>OpenAPI: <code>/muse-openapi.json</code> on the API host.</p>
  </div>
</template>
