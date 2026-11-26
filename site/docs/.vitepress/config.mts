import { defineConfig } from 'vitepress'

function normalizeBase(raw: string | undefined): string {
  const trimmed = (raw ?? '/ar-rh-proof-skeleton/').trim()
  if (!trimmed || trimmed === '/') {
    return '/'
  }
  const withLeading = trimmed.startsWith('/') ? trimmed : `/${trimmed}`
  return withLeading.endsWith('/') ? withLeading : `${withLeading}/`
}

const base = normalizeBase(process.env.VITEPRESS_BASE)

export default defineConfig({
  base,
  title: 'Riemann Hypothesis Proof Explainer',
  description:
    'Concept-first, multi-level explainer grounded in the Lean 4 formal proof of the Riemann Hypothesis.',
  cleanUrls: true,
  appearance: false,
  markdown: {
    math: true,
  },
  themeConfig: {
    siteTitle: 'RH Proof Explainer',
    nav: [
      { text: 'Home', link: '/' },
      { text: 'Proof Journey', link: '/stages/00_statement/proof-journey' },
      {
        text: 'Stages',
        items: [
          {
            text: 'Stage 00 — Formal Statement',
            link: '/stages/00_statement/overview',
          },
          {
            text: 'Stage 01 — The Theorem & Its Reduction',
            link: '/stages/01_reduction/overview',
          },
          {
            text: 'Stage 02 — Derivatives of Theta Functions',
            link: '/stages/02_derivatives/overview',
          },
        ],
      },
      { text: 'Concepts', link: '/concepts/' },
      {
        text: 'Upstream Lean Repo',
        link: 'https://github.com/leanprover-community/riemann-hypothesis',
      },
    ],
    sidebar: [
      {
        text: 'Stage 00 — Formal Statement',
        items: [
          { text: 'Overview', link: '/stages/00_statement/overview' },
          {
            text: 'Full Proof Journey & 20-Step Map',
            link: '/stages/00_statement/proof-journey',
          },
          {
            text: 'Main Theorems & Challenges',
            link: '/stages/00_statement/main-theorems',
          },
          {
            text: 'Statement Definitions',
            link: '/stages/00_statement/statement-definitions',
          },
          {
            text: 'Formal Conjectures Equivalence',
            link: '/stages/00_statement/formal-conjectures',
          },
        ],
      },
      {
        text: 'Stage 01 — The Theorem & Its Reduction',
        items: [
          { text: 'Overview', link: '/stages/01_reduction/overview' },
          {
            text: 'One-Sided Criterion & GRH',
            link: '/stages/01_reduction/one-sided-criterion',
          },
          {
            text: 'Main Inequality & Theorems A/B',
            link: '/stages/01_reduction/main-inequality',
          },
          {
            text: 'Twisted Lattices & Product Formula',
            link: '/stages/01_reduction/twisted-lattices',
          },
        ],
      },
      {
        text: 'Stage 02 — Derivatives of Theta Functions',
        items: [
          { text: 'Overview', link: '/stages/02_derivatives/overview' },
          {
            text: 'Formal Groups & Divisibility',
            link: '/stages/02_derivatives/formal-group-and-divisibility',
          },
          {
            text: 'Tight Frames & Integral Vacuum',
            link: '/stages/02_derivatives/tight-frames-and-integral-vacuum',
          },
          {
            text: 'Sharpness: Bound Attained',
            link: '/stages/02_derivatives/sharpness-bound-attained',
          },
        ],
      },
      {
        text: 'Foundational Concepts',
        items: [
          { text: 'All Concept Cards', link: '/concepts/' },
          { text: 'Prime Number', link: '/concepts/prime-number' },
          { text: 'Infinite Series', link: '/concepts/infinite-series' },
          { text: 'Complex Plane', link: '/concepts/complex-plane' },
          {
            text: 'Riemann Zeta Function',
            link: '/concepts/riemann-zeta-function',
          },
          {
            text: 'Analytic Continuation',
            link: '/concepts/analytic-continuation',
          },
          { text: 'Trivial Zeros', link: '/concepts/trivial-zeros' },
          { text: 'Critical Strip', link: '/concepts/critical-strip' },
          { text: 'Critical Line', link: '/concepts/critical-line' },
          {
            text: 'Dirichlet Character',
            link: '/concepts/dirichlet-character',
          },
          {
            text: 'Dirichlet L-Function',
            link: '/concepts/dirichlet-l-function',
          },
          {
            text: 'Primitive Character',
            link: '/concepts/primitive-character',
          },
          {
            text: 'Formal Verification',
            link: '/concepts/formal-verification',
          },
          {
            text: 'Von Mangoldt Function',
            link: '/concepts/von-mangoldt-function',
          },
          { text: "Landau's Lemma", link: '/concepts/landaus-lemma' },
          { text: 'Quadratic Twist', link: '/concepts/quadratic-twist' },
          { text: 'Twisted Lattice', link: '/concepts/twisted-lattice' },
          { text: 'Gram Determinant', link: '/concepts/gram-determinant' },
          { text: 'Arithmetic Height', link: '/concepts/arithmetic-height' },
          {
            text: 'Complex Multiplication',
            link: '/concepts/complex-multiplication',
          },
          {
            text: 'Elliptic Theta Function',
            link: '/concepts/elliptic-theta-function',
          },
          { text: 'Formal Group Law', link: '/concepts/formal-group-law' },
          { text: 'Theta Normal Form', link: '/concepts/theta-normal-form' },
          { text: 'Tight Frame', link: '/concepts/tight-frame' },
          { text: 'Schur Jet', link: '/concepts/schur-jet' },
        ],
      },
    ],
    outline: {
      level: [2, 3],
      label: 'On this page',
    },
    docFooter: {
      prev: 'Previous',
      next: 'Next',
    },
    socialLinks: [
      {
        icon: 'github',
        link: 'https://github.com/Foxfire1st/rh-proof-skeleton',
        ariaLabel: 'Riemann Hypothesis Proof Explainer repository on GitHub',
      },
      {
        icon: 'github',
        link: 'https://github.com/leanprover-community/riemann-hypothesis',
        ariaLabel: 'Upstream Lean 4 riemann-hypothesis repository on GitHub',
      },
    ],
  },
})
