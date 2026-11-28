<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'

type ZoomLevel = 'first-principles' | 'undergrad' | 'lean'

interface ZoomTabDef {
  id: ZoomLevel
  panelId: string
  slotName: 'level1' | 'level2' | 'level3'
  label: string
}

const TABS: readonly ZoomTabDef[] = [
  {
    id: 'first-principles',
    panelId: 'level-1',
    slotName: 'level1',
    label: 'Level 1 · First-Principles',
  },
  {
    id: 'undergrad',
    panelId: 'level-2',
    slotName: 'level2',
    label: 'Level 2 · Undergrad Bridge',
  },
  {
    id: 'lean',
    panelId: 'level-3',
    slotName: 'level3',
    label: 'Level 3 · Formal Lean Contract',
  },
] as const

const LEVEL_ALIAS_HASHES: Readonly<Record<string, ZoomLevel>> = {
  '#level-first-principles': 'first-principles',
  '#level-abitur': 'first-principles',
  '#level-1': 'first-principles',
  '#level-undergrad': 'undergrad',
  '#level-2': 'undergrad',
  '#level-lean': 'lean',
  '#level-3': 'lean',
}

const props = defineProps<{
  pageId: string
}>()

const rootRef = ref<HTMLElement | null>(null)
const hydrated = ref(false)
const activeLevel = ref<ZoomLevel>('first-principles')
const tabRefs = ref<Array<HTMLButtonElement | null>>([])
let pendingClickTimer: number | null = null
let pendingScrollRaf1: number | null = null
let pendingScrollRaf2: number | null = null

function parseQueryLevel(search: string): ZoomLevel | null {
  const params = new URLSearchParams(search)
  const queryLevel = params.get('level')?.toLowerCase()
  if (
    queryLevel === 'first-principles' ||
    queryLevel === 'abitur' ||
    queryLevel === '1'
  ) {
    return 'first-principles'
  }
  if (queryLevel === 'undergrad' || queryLevel === '2') {
    return 'undergrad'
  }
  if (queryLevel === 'lean' || queryLevel === '3') {
    return 'lean'
  }
  return null
}

function parseLevelAliasHash(rawHash: string): ZoomLevel | null {
  return LEVEL_ALIAS_HASHES[rawHash.toLowerCase()] ?? null
}

function decodeHashId(rawHash: string): string | null {
  if (!rawHash || rawHash === '#') {
    return null
  }
  const stripped = rawHash.startsWith('#') ? rawHash.slice(1) : rawHash
  if (!stripped) {
    return null
  }
  try {
    return decodeURIComponent(stripped)
  } catch {
    return stripped
  }
}

function findPanelLevelForElement(el: HTMLElement): ZoomLevel | null {
  const panel = el.closest('.rh-zoom-panel')
  if (!panel || !rootRef.value?.contains(panel)) {
    return null
  }
  const match = TABS.find((tab) => tab.panelId === panel.id)
  return match ? match.id : null
}

function resolveHashTarget(rawHash: string): {
  level: ZoomLevel | null
  element: HTMLElement | null
  isLevelAlias: boolean
} {
  const aliasLevel = parseLevelAliasHash(rawHash)
  if (aliasLevel) {
    return { level: aliasLevel, element: null, isLevelAlias: true }
  }
  const id = decodeHashId(rawHash)
  if (!id || typeof document === 'undefined') {
    return { level: null, element: null, isLevelAlias: false }
  }
  const el = document.getElementById(id)
  if (!el) {
    return { level: null, element: null, isLevelAlias: false }
  }
  return {
    level: findPanelLevelForElement(el),
    element: el,
    isLevelAlias: false,
  }
}

function syncUrl(level: ZoomLevel) {
  if (typeof window === 'undefined') {
    return
  }
  const url = new URL(window.location.href)
  url.searchParams.set('level', level)
  if (parseLevelAliasHash(url.hash) !== null) {
    url.hash = ''
  }
  window.history.replaceState(window.history.state, '', url.toString())
}

function cancelPendingScroll() {
  if (typeof window === 'undefined') {
    return
  }
  if (pendingScrollRaf1 !== null) {
    window.cancelAnimationFrame(pendingScrollRaf1)
    pendingScrollRaf1 = null
  }
  if (pendingScrollRaf2 !== null) {
    window.cancelAnimationFrame(pendingScrollRaf2)
    pendingScrollRaf2 = null
  }
}

function scrollToTarget(el: HTMLElement) {
  el.scrollIntoView({ block: 'start', behavior: 'auto' })
}

function scheduleScroll(targetEl: HTMLElement) {
  cancelPendingScroll()
  void nextTick(() => {
    scrollToTarget(targetEl)
    if (typeof window !== 'undefined' && typeof window.requestAnimationFrame === 'function') {
      // Run after VitePress's single-rAF router scrollTo, which may have measured
      // the heading while its tab panel was hidden or before hydration collapsed Level 1.
      pendingScrollRaf1 = window.requestAnimationFrame(() => {
        pendingScrollRaf1 = null
        pendingScrollRaf2 = window.requestAnimationFrame(() => {
          pendingScrollRaf2 = null
          scrollToTarget(targetEl)
        })
      })
    }
  })
}

function applyLocation(options: {
  scroll: boolean
  updateUrl: boolean
  fallbackToDefault: boolean
}) {
  if (typeof window === 'undefined') {
    return
  }
  const queryLevel = parseQueryLevel(window.location.search)
  const hashTarget = resolveHashTarget(window.location.hash)
  const resolvedLevel =
    hashTarget.level ??
    queryLevel ??
    (options.fallbackToDefault ? 'first-principles' : null)

  if (resolvedLevel !== null) {
    activeLevel.value = resolvedLevel
    if (
      options.updateUrl &&
      (hashTarget.isLevelAlias ||
        (hashTarget.level !== null && queryLevel !== resolvedLevel))
    ) {
      syncUrl(resolvedLevel)
    }
  }

  if (options.scroll && hashTarget.element && hashTarget.level !== null) {
    scheduleScroll(hashTarget.element)
  }
}

function onHashChange() {
  applyLocation({
    scroll: true,
    updateUrl: true,
    fallbackToDefault: false,
  })
}

function onPopState() {
  applyLocation({
    scroll: true,
    updateUrl: false,
    fallbackToDefault: true,
  })
}

function onDocumentClick(event: MouseEvent) {
  // Do not bail on event.defaultPrevented: VitePress's capture-phase router
  // prevents default on same-origin anchor clicks before bubble listeners run.
  if (
    event.button !== 0 ||
    event.metaKey ||
    event.ctrlKey ||
    event.shiftKey ||
    event.altKey
  ) {
    return
  }
  const target = event.target
  if (!(target instanceof Element)) {
    return
  }
  const anchor = target.closest('a[href*="#"]')
  if (!(anchor instanceof HTMLAnchorElement) || !anchor.hash) {
    return
  }
  let anchorUrl: URL
  try {
    anchorUrl = new URL(anchor.href, window.location.href)
  } catch {
    return
  }
  if (
    anchorUrl.origin !== window.location.origin ||
    anchorUrl.pathname !== window.location.pathname
  ) {
    return
  }
  const resolved = resolveHashTarget(anchorUrl.hash)
  if (resolved.level === null) {
    return
  }
  const nextLevel = resolved.level
  const targetEl = resolved.element
  activeLevel.value = nextLevel
  // Defer URL sync until after the browser/router updates window.location.hash
  // so browser Back/Forward keeps the pre-click history entry intact.
  if (pendingClickTimer !== null) {
    window.clearTimeout(pendingClickTimer)
  }
  pendingClickTimer = window.setTimeout(() => {
    pendingClickTimer = null
    if (
      parseLevelAliasHash(window.location.hash) !== null ||
      parseQueryLevel(window.location.search) !== nextLevel
    ) {
      syncUrl(nextLevel)
    }
    if (targetEl) {
      scheduleScroll(targetEl)
    }
  }, 0)
}

function setTabRef(el: unknown, index: number) {
  tabRefs.value[index] = el instanceof HTMLButtonElement ? el : null
}

function selectTab(level: ZoomLevel, moveFocus = false) {
  activeLevel.value = level
  syncUrl(level)
  if (moveFocus) {
    const idx = TABS.findIndex((tab) => tab.id === level)
    if (idx >= 0) {
      tabRefs.value[idx]?.focus()
    }
  }
}

function onTabKeydown(event: KeyboardEvent, currentIndex: number) {
  let nextIndex: number | null = null
  if (event.key === 'ArrowRight') {
    nextIndex = (currentIndex + 1) % TABS.length
  } else if (event.key === 'ArrowLeft') {
    nextIndex = (currentIndex - 1 + TABS.length) % TABS.length
  } else if (event.key === 'Home') {
    nextIndex = 0
  } else if (event.key === 'End') {
    nextIndex = TABS.length - 1
  }
  if (nextIndex !== null) {
    event.preventDefault()
    selectTab(TABS[nextIndex].id, true)
  }
}

onMounted(() => {
  hydrated.value = true
  applyLocation({
    scroll: true,
    updateUrl: true,
    fallbackToDefault: false,
  })
  window.addEventListener('hashchange', onHashChange)
  window.addEventListener('popstate', onPopState)
  document.addEventListener('click', onDocumentClick)
})

onBeforeUnmount(() => {
  cancelPendingScroll()
  if (pendingClickTimer !== null) {
    window.clearTimeout(pendingClickTimer)
    pendingClickTimer = null
  }
  window.removeEventListener('hashchange', onHashChange)
  window.removeEventListener('popstate', onPopState)
  document.removeEventListener('click', onDocumentClick)
})
</script>

<template>
  <div ref="rootRef" class="rh-zoom-tabs" :data-page-id="props.pageId">
    <nav
      v-if="!hydrated"
      class="rh-zoom-static-nav"
      aria-label="Jump to explanation level"
    >
      <span class="rh-zoom-static-nav__label">Explanation levels:</span>
      <a
        v-for="tab in TABS"
        :key="tab.id"
        :href="`#${tab.panelId}`"
        class="rh-zoom-static-nav__link"
      >
        {{ tab.label }}
      </a>
    </nav>

    <div
      v-else
      class="rh-zoom-tablist"
      role="tablist"
      aria-label="Explanation depth level"
    >
      <button
        v-for="(tab, index) in TABS"
        :key="tab.id"
        :id="`${props.pageId}-tab-${tab.id}`"
        :ref="(el) => setTabRef(el, index)"
        type="button"
        role="tab"
        class="rh-zoom-tab"
        :class="{ 'rh-zoom-tab--active': activeLevel === tab.id }"
        :aria-selected="activeLevel === tab.id ? 'true' : 'false'"
        :aria-controls="tab.panelId"
        :tabindex="activeLevel === tab.id ? 0 : -1"
        @click="selectTab(tab.id)"
        @keydown="onTabKeydown($event, index)"
      >
        {{ tab.label }}
      </button>
    </div>

    <section
      v-for="tab in TABS"
      :key="tab.id"
      :id="tab.panelId"
      class="rh-zoom-panel"
      :class="{ 'rh-zoom-panel--hidden': hydrated && activeLevel !== tab.id }"
      :role="hydrated ? 'tabpanel' : undefined"
      :aria-labelledby="
        hydrated
          ? `${props.pageId}-tab-${tab.id}`
          : `${props.pageId}-heading-${tab.id}`
      "
      :tabindex="hydrated && activeLevel === tab.id ? 0 : undefined"
    >
      <div
        :id="`${props.pageId}-heading-${tab.id}`"
        class="rh-zoom-panel__heading"
        :class="{ 'rh-zoom-panel__heading--sr': hydrated }"
      >
        {{ tab.label }}
      </div>
      <slot :name="tab.slotName" />
    </section>
  </div>
</template>
