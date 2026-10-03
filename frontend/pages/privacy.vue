<template>
  <div class="stack prose">
    <h1>Privacy Policy</h1>
    <p>AgentMatch stores workspace data in PostgreSQL and optional Neo4j projections. Muse connector access uses OAuth scopes you approve.</p>
    <h2>Muse connector data</h2>
    <ul>
      <li>Used only to fulfill connector tool requests for your workspace.</li>
      <li>Not used to train global RL, Laya, or LLM models on raw connector payloads.</li>
      <li>Revoke access anytime under Settings → Connections.</li>
    </ul>
    <p>Contact: see <NuxtLink to="/support">Support</NuxtLink>.</p>
  </div>
</template>
