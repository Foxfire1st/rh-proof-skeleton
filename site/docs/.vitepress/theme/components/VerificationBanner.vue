<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    buildStatus: string
    upstreamCommit: string
    upstreamRepo?: string
    leanToolchain: string
    mathlibRev: string
    scanScope: string
    solutionForbiddenCount: number
    challengeSorryCount: number
    permittedAxioms: string
    theoremCount: number
  }>(),
  {
    upstreamRepo: 'https://github.com/leanprover-community/riemann-hypothesis',
  },
)

const normalizedRepo = computed(() => props.upstreamRepo.replace(/\/+$/, ''))
const shortCommit = computed(() => props.upstreamCommit.slice(0, 12))
const shortMathlibRev = computed(() =>
  props.mathlibRev.length > 12 ? props.mathlibRev.slice(0, 12) : props.mathlibRev,
)

const commitTreeUrl = computed(
  () => `${normalizedRepo.value}/tree/${props.upstreamCommit}`,
)
const comparatorUrl = computed(
  () => `${normalizedRepo.value}/tree/${props.upstreamCommit}/comparator`,
)
const verificationNotesUrl = computed(
  () => `${normalizedRepo.value}/blob/${props.upstreamCommit}/verification/NOTES.md`,
)

const statusMeta = computed(() => {
  if (props.buildStatus === 'unverified-locally') {
    return {
      tone: 'unverified',
      badge: 'UNVERIFIED LOCALLY',
      label: 'Status: Unverified Locally',
    }
  }
  if (props.buildStatus === 'verified-locally' || props.buildStatus === 'verified') {
    return {
      tone: 'verified',
      badge: 'VERIFIED LOCALLY',
      label: 'Status: Verified Locally',
    }
  }
  return {
    tone: 'unverified',
    badge: props.buildStatus.toUpperCase(),
    label: `Status: ${props.buildStatus}`,
  }
})
</script>

<template>
  <aside
    class="rh-verification-banner"
    :class="`rh-verification-banner--${statusMeta.tone}`"
    aria-label="Verification status"
  >
    <div class="rh-verification-banner__header">
      <span class="rh-verification-banner__badge">{{ statusMeta.badge }}</span>
      <strong class="rh-verification-banner__title">{{ statusMeta.label }}</strong>
      <span class="rh-verification-banner__commit">
        Upstream commit:
        <a :href="commitTreeUrl" target="_blank" rel="noopener noreferrer">
          <code>{{ shortCommit }}</code>
        </a>
      </span>
    </div>

    <dl class="rh-verification-banner__facts">
      <div class="rh-verification-banner__fact">
        <dt>Static scan</dt>
        <dd>
          {{ scanScope }} scan: {{ solutionForbiddenCount }} forbidden tokens in
          solution, {{ challengeSorryCount }} expected challenge sorrys
        </dd>
      </div>

      <div class="rh-verification-banner__fact">
        <dt>Toolchain</dt>
        <dd>
          <code>{{ leanToolchain }}</code>
          <span> (Mathlib <code>{{ shortMathlibRev }}</code>)</span>
        </dd>
      </div>

      <div class="rh-verification-banner__fact">
        <dt>Mapped theorems</dt>
        <dd>{{ theoremCount }}</dd>
      </div>

      <div class="rh-verification-banner__fact">
        <dt>Permitted axioms</dt>
        <dd>{{ permittedAxioms }}</dd>
      </div>
    </dl>

    <div class="rh-verification-banner__links">
      <a :href="comparatorUrl" target="_blank" rel="noopener noreferrer">
        Upstream comparator/
      </a>
      <a :href="verificationNotesUrl" target="_blank" rel="noopener noreferrer">
        Upstream verification/NOTES.md
      </a>
    </div>
  </aside>
</template>
