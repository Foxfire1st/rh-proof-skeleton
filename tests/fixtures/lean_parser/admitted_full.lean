module

import Mathlib.Data.Nat.Basic
public import Statements

/-!
# Synthetic Admitted Fixture

Exercises all Lean 4 top-level forms admitted by the closed-world extractor.
-/

-- Top-level line comment before section.
/- outer /- inner -/ outer -/

@[expose] public section

/-- Base record with default field values and multi-line attributes. -/
@[
  ext,
  inline
]
structure SampleRecord where
  label : String := "default"
  count : Nat := 0
  deriving Repr, DecidableEq

/-- Typeclass with a where block. -/
class HasWeight (α : Type) where
  weight : α → Nat

/-- Inductive type with a where block and column-zero deriving clause. -/
inductive Sign where
  | pos : Sign
  | neg : Sign
deriving Repr, DecidableEq

/-- Postulated axiom without a body. -/
axiom baseline_postulate : Prop

/-- Unsafe opaque declaration without a body. -/
unsafe opaque rawHandle : Nat → Nat

namespace Outer

/-- Public theorem with a tactic proof. -/
@[simp]
public theorem tactic_proof_demo (n : Nat) : n = n := by
  rfl

namespace Inner

/-- Private lemma with a prime in its name and a multi-line term-mode proof. -/
private lemma aux_step' (a b : Nat) (h : a = b) : b = a :=
  Eq.symm
    h

/-- Noncomputable abbrev with a Unicode identifier. -/
noncomputable abbrev ζ₀ (n : Nat) : Nat :=
  n + 1

end

/-- Protected def with a dotted name inside Outer. -/
protected def Dotted.helper (x : Nat) : Nat :=
  x + 1

/-- Partial def with a guillemet-escaped name. -/
partial def «weird name» (n : Nat) : Nat :=
  «weird name» (n + 1)

end Outer

/-- Named instance with nested `:=` inside `(...)`, `[...]`, and `{...}`. -/
instance instDecidableOdd (S : Type) (m : Nat) [Fact (Flag (k := m))] {p : SampleRecord}
    {h : p = { label := "a := b", count := m }} : DecidablePred (Odd (S := S) (m := m)) := by
  intro x
  exact isFalse (fun hx => hx)

/-- Single-line term-mode proof. -/
theorem riemannZeta_eq_zero_re : RiemannZetaZerosOnLine := riemannHypothesis
