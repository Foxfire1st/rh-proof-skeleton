"""Unit tests for the closed-world Lean 4 declaration extractor and models."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from rh_pipeline.lean.parser import UnsupportedLeanFormError, extract_declarations
from rh_pipeline.lean.stub_emitter import render_skeleton_file
from rh_pipeline.models.lean import ExtractedModule, LeanDeclaration

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "lean_parser"
COMMIT = "0123456789abcdef"
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_admitted_full_fixture_extracts_all_declarations_and_imports() -> None:
    source = (FIXTURE_DIR / "admitted_full.lean").read_text(encoding="utf-8")
    rel_path = "Fixtures/AdmittedFull.lean"
    mod = extract_declarations(source, rel_path=rel_path, commit=COMMIT)

    assert mod.upstream_path == rel_path
    assert mod.upstream_commit == COMMIT
    assert mod.imports == ("Mathlib.Data.Nat.Basic", "Statements")

    summary = [(d.name, d.kind, d.start_line, d.end_line, d.body_lines) for d in mod.declarations]
    assert summary == [
        ("SampleRecord", "structure", 17, 25, 0),
        ("HasWeight", "class", 27, 29, 0),
        ("Sign", "inductive", 31, 35, 0),
        ("baseline_postulate", "axiom", 37, 38, 0),
        ("rawHandle", "opaque", 40, 41, 0),
        ("Outer.tactic_proof_demo", "theorem", 45, 48, 2),
        ("Outer.Inner.aux_step'", "lemma", 52, 55, 2),
        ("Outer.Inner.ζ₀", "abbrev", 57, 59, 1),
        ("Outer.Dotted.helper", "def", 63, 65, 1),
        ("Outer.«weird name»", "def", 67, 69, 1),
        ("instDecidableOdd", "instance", 73, 77, 3),
        ("riemannZeta_eq_zero_re", "theorem", 79, 80, 1),
    ]

    by_name = {d.name: d for d in mod.declarations}

    # No-body declarations keep field defaults, attributes, and column-0 deriving in signature.
    sample_record = by_name["SampleRecord"]
    assert sample_record.body == ""
    assert sample_record.body_sha256 == EMPTY_SHA256
    assert "@[\n  ext,\n  inline\n]" in sample_record.signature
    assert 'label : String := "default"' in sample_record.signature
    assert "deriving Repr, DecidableEq" in sample_record.signature

    sign = by_name["Sign"]
    assert sign.body == ""
    assert sign.body_sha256 == EMPTY_SHA256
    assert sign.signature.endswith("deriving Repr, DecidableEq")

    assert by_name["rawHandle"].body == ""
    assert by_name["rawHandle"].body_sha256 == EMPTY_SHA256

    # Tactic proof preserves doc comment, attributes, and qualified name.
    tactic_demo = by_name["Outer.tactic_proof_demo"]
    assert tactic_demo.signature == (
        "/-- Public theorem with a tactic proof. -/\n"
        "@[simp]\n"
        "public theorem Outer.tactic_proof_demo (n : Nat) : n = n"
    )
    assert tactic_demo.body == "by\n  rfl"
    assert tactic_demo.body_sha256 == _sha256("by\n  rfl")

    # Multiline term-mode proof preserves indentation verbatim for stub emission.
    aux_step = by_name["Outer.Inner.aux_step'"]
    assert (
        "private lemma Outer.Inner.aux_step' (a b : Nat) (h : a = b) : b = a" in aux_step.signature
    )
    assert aux_step.body == "  Eq.symm\n    h"
    assert f"{aux_step.signature} :=\n{aux_step.body}".endswith(" :=\n  Eq.symm\n    h")

    # Dotted and guillemet names inside namespace Outer.
    assert (
        "protected def Outer.Dotted.helper (x : Nat) : Nat"
        in by_name["Outer.Dotted.helper"].signature
    )
    assert by_name["Outer.Dotted.helper"].body == "  x + 1"
    assert (
        "partial def Outer.«weird name» (n : Nat) : Nat" in by_name["Outer.«weird name»"].signature
    )
    assert by_name["Outer.«weird name»"].body == "  «weird name» (n + 1)"

    # Nested `:=` inside (), [], {}, and strings stay in signature.
    inst = by_name["instDecidableOdd"]
    assert 'label := "a := b"' in inst.signature
    assert "Odd (S := S) (m := m)" in inst.signature
    assert inst.body == "by\n  intro x\n  exact isFalse (fun hx => hx)"

    # Boundary single-line term-mode proof.
    rz = by_name["riemannZeta_eq_zero_re"]
    assert rz.signature == (
        "/-- Single-line term-mode proof. -/\n"
        "theorem riemannZeta_eq_zero_re : RiemannZetaZerosOnLine"
    )
    assert rz.body == "riemannHypothesis"
    assert rz.body_lines == 1
    assert rz.body_sha256 == _sha256("riemannHypothesis")


REFUSED_FIXTURE_CASES: list[tuple[str, int, str]] = [
    ("refused_macro.lean", 3, "macro"),
    ("refused_mutual.lean", 3, "mutual"),
    ("refused_set_option.lean", 3, "set_option"),
    ("refused_termination_by.lean", 7, "termination_by"),
    ("refused_unbalanced_namespace.lean", 3, "unbalanced-namespace"),
]


def test_all_refused_fixture_files_are_listed() -> None:
    on_disk = sorted(p.name for p in FIXTURE_DIR.glob("refused_*.lean"))
    listed = sorted(name for name, _, _ in REFUSED_FIXTURE_CASES)
    assert on_disk == listed


@pytest.mark.parametrize(("filename", "expected_line", "expected_form"), REFUSED_FIXTURE_CASES)
def test_refused_fixtures_raise_unsupported_error(
    filename: str, expected_line: int, expected_form: str
) -> None:
    source = (FIXTURE_DIR / filename).read_text(encoding="utf-8")
    rel_path = f"Fixtures/{filename}"
    with pytest.raises(UnsupportedLeanFormError) as exc_info:
        extract_declarations(source, rel_path=rel_path, commit=COMMIT)
    err = exc_info.value
    assert err.rel_path == rel_path
    assert err.line == expected_line
    assert err.form == expected_form
    assert str(err) == f"{rel_path}:{expected_line}:{expected_form}"


ADDITIONAL_REFUSAL_CASES: list[tuple[str, int]] = [
    ('syntax "foo" : term\n', 1),
    ("macro_rules | `(foo) => `(0)\n", 1),
    ('notation "★" => Nat\n', 1),
    ('prefix:50 "!" => Nat.succ\n', 1),
    ('infixl:65 " ⊕ " => Nat.add\n', 1),
    ('infixr:65 " ⊕ " => Nat.add\n', 1),
    ('infix:65 " ⊕ " => Nat.add\n', 1),
    ('postfix:50 "!" => Nat.succ\n', 1),
    ("universes u v\n", 1),
    ("variables (n : Nat)\n", 1),
    ("local def foo : Nat := 1\n", 1),
    ("scoped def foo : Nat := 1\n", 1),
    ("local theorem foo : True := True.intro\n", 1),
    ("scoped instance : Inhabited Nat := ⟨0⟩\n", 1),
    ("scoped lemma foo : True := True.intro\n", 1),
    ("seal foo\ntheorem t : True := True.intro\n", 1),
    ("unseal foo\ntheorem t : True := True.intro\n", 1),
    ("def f (n : Nat) : Nat :=\n  n\n  decreasing_by trivial\n", 3),
    ("def f (n : Nat) : Nat :=\n  n\n  termination_by n\n", 3),
    ("def f (n : Nat) : Nat :=\n  n\ndecreasing_by trivial\n", 3),
    ("end\n", 1),
    ("namespace A\nend B\n", 2),
    ("namespace A.B\nend A\n", 2),
    ("namespace A\nsection\nend A\n", 3),
    ("/- unclosed comment\n", 1),
    ("/- outer /- inner -/\n", 1),
    ('theorem s : String := "unclosed\n', 1),
    ('theorem s : String := r#"unclosed\n', 1),
    ("def «unclosed : Nat := 1\n", 1),
    ("def bad (n : Nat : Nat := n\n", 1),
    ("def bad (n : Nat] : Nat := n\n", 1),
    ("/-- orphan doc -/\nnamespace A\nend A\n", 2),
    ("/-- orphan doc -/\nopen Complex\ntheorem t : True := True.intro\n", 2),
    ("@[simp]\nsection Foo\nend Foo\n", 2),
    ("/-- orphan at eof -/\n", 1),
    ("@[expose]\n", 1),
    ("open Complex in\n", 1),
    ("example : True := True.intro\n", 1),
    ("#check Nat\n", 1),
    ("theorem no_body : True\n", 1),
    ("instance where\n  x := 1\n", 1),
    ("instance (x : Nat) where\n  val := x\n", 1),
    ("module Extra\n", 1),
    ("import A B\n", 1),
]


@pytest.mark.parametrize(("source", "expected_line"), ADDITIONAL_REFUSAL_CASES)
def test_additional_closed_world_refusals(source: str, expected_line: int) -> None:
    with pytest.raises(UnsupportedLeanFormError) as exc_info:
        extract_declarations(source, rel_path="Test.lean", commit=COMMIT)
    assert exc_info.value.rel_path == "Test.lean"
    assert exc_info.value.line == expected_line


def test_admitted_edge_cases_lexical_and_scope() -> None:
    source = (
        "module\r\n"
        "import B.Mod\r\n"
        "public import A.Mod\r\n"
        "import B.Mod\r\n"
        "/--- regular block comment, not a doc comment -/\r"
        "/-! module doc with /- nested -/ comment -/\n"
        "section IgnoredScope\n"
        "namespace Alpha.Beta\n"
        "def foo ⦃x : Nat := 1⦄ (foo : String := r#\"a := )\"#) (c : Char := '\\'') : Nat :=\n"
        "  let d := '\\n'\n"
        "  let e := '\\x29'\n"
        "  let f' := '\\u0029'\n"
        "  x\n"
        "opaque withBody : Nat := 42\n"
        "inductive Color\n"
        "| red\n"
        "| blue\n"
        "deriving Repr\n"
        "end Alpha.Beta\n"
        "end IgnoredScope\n"
    )
    mod = extract_declarations(source, rel_path="Edge.lean", commit=COMMIT)
    assert mod.imports == ("A.Mod", "B.Mod")
    assert [d.name for d in mod.declarations] == [
        "Alpha.Beta.foo",
        "Alpha.Beta.withBody",
        "Alpha.Beta.Color",
    ]

    foo = mod.declarations[0]
    # Only the declaration name is qualified; the parameter named `foo` is untouched.
    assert foo.signature.startswith("def Alpha.Beta.foo ⦃x : Nat := 1⦄ (foo : String")
    assert "let f' := '\\u0029'" in foo.body
    assert foo.body_lines == 4

    with_body = mod.declarations[1]
    assert with_body.kind == "opaque"
    assert with_body.signature == "opaque Alpha.Beta.withBody : Nat"
    assert with_body.body == "42"
    assert with_body.body_lines == 1
    assert with_body.body_sha256 == _sha256("42")

    color = mod.declarations[2]
    assert color.kind == "inductive"
    assert color.body == ""
    assert color.body_lines == 0
    assert "| red" in color.signature
    assert color.signature.endswith("deriving Repr")


def test_empty_and_comment_only_module() -> None:
    mod = extract_declarations(
        "-- only comments\n/- nested /- ok -/ -/\n", rel_path="Empty.lean", commit=COMMIT
    )
    assert mod.imports == ()
    assert mod.declarations == ()


def test_model_serialization_round_trip_and_strict_validation() -> None:
    source = (FIXTURE_DIR / "admitted_full.lean").read_text(encoding="utf-8")
    mod = extract_declarations(source, rel_path="Fixtures/AdmittedFull.lean", commit=COMMIT)
    payload = json.loads(json.dumps(mod.to_dict()))
    restored = ExtractedModule.from_dict(payload)
    assert restored == mod

    decl_payload = mod.declarations[0].to_dict()
    assert LeanDeclaration.from_dict(decl_payload) == mod.declarations[0]

    with pytest.raises(ValueError, match="key mismatch"):
        LeanDeclaration.from_dict({**decl_payload, "extra": 1})

    with pytest.raises(ValueError, match="kind must be one of"):
        LeanDeclaration.from_dict({**decl_payload, "kind": "macro"})

    with pytest.raises(ValueError, match="body_lines must be 0"):
        LeanDeclaration.from_dict({**decl_payload, "body": "", "body_lines": 1})

    with pytest.raises(ValueError, match="end_line must be >="):
        LeanDeclaration.from_dict({**decl_payload, "start_line": 10, "end_line": 9})

    with pytest.raises(ValueError, match="must be an integer"):
        LeanDeclaration.from_dict({**decl_payload, "start_line": True})


def test_refuses_col0_where_after_def() -> None:
    src = "def f : Nat := 1\nwhere helper := 2\n"
    with pytest.raises(UnsupportedLeanFormError) as exc_info:
        extract_declarations(src, rel_path="BadWhere.lean", commit=COMMIT)
    assert exc_info.value.line == 2
    assert exc_info.value.form == "where"


def test_admitted_commands_sections_and_local_instances() -> None:
    src = (
        "module\n"
        "universe u v\n"
        "open Complex\n"
        "open scoped BigOperators\n"
        "variable (n : Nat)\n"
        "  (h : n = n)\n"
        "include h\n"
        "omit h\n"
        "export Nat (succ)\n"
        "attribute [simp]\n"
        "  foo\n"
        "  bar\n"
        "grind_pattern foo_eq => foo x\n"
        "local grind_pattern bar_eq =>\n"
        "  bar x where\n"
        "  x = x\n"
        "noncomputable section NamedSec\n"
        "@[local simp]\n"
        "theorem t1 : True := True.intro\n"
        "@[simp, scoped ext]\n"
        "theorem t2 : True := True.intro\n"
        "local instance instLocal (j : Nat) : Foo j := bar\n"
        "noncomputable local instance instNoncompLocal : Foo Nat := bar\n"
        "end NamedSec\n"
        "noncomputable section\n"
        "def inAnonNoncomp : Nat := 1\n"
        "end\n"
    )
    mod = extract_declarations(src, rel_path="Commands.lean", commit=COMMIT)
    assert [(d.name, d.kind) for d in mod.declarations] == [
        ("t1", "theorem"),
        ("t2", "theorem"),
        ("instLocal", "instance"),
        ("instNoncompLocal", "instance"),
        ("inAnonNoncomp", "def"),
    ]
    assert mod.declarations[0].signature == "@[local simp]\ntheorem t1 : True"
    assert mod.declarations[1].signature == "@[simp, scoped ext]\ntheorem t2 : True"
    assert mod.declarations[2].signature == "local instance instLocal (j : Nat) : Foo j"
    assert (
        mod.declarations[3].signature == "noncomputable local instance instNoncompLocal : Foo Nat"
    )


def test_scoped_in_prefixes_stripped_before_and_after_docs() -> None:
    src = (
        "open Complex in\n"
        "omit h in\n"
        "theorem t1 : True := True.intro\n"
        "/-- Doc before scoped prefix. -/\n"
        "open Real\n"
        "  Topology in\n"
        "seal helper in\n"
        "unseal helper in\n"
        "@[local simp]\n"
        "theorem t2 : True := True.intro\n"
        "variable (x : Nat) in\n"
        "include x in\n"
        "/-- Doc after scoped prefix. -/\n"
        "def d1 : Nat := x\n"
    )
    mod = extract_declarations(src, rel_path="Scoped.lean", commit=COMMIT)
    assert [d.name for d in mod.declarations] == ["t1", "t2", "d1"]
    assert mod.declarations[0].signature == "theorem t1 : True"
    assert mod.declarations[1].signature == (
        "/-- Doc before scoped prefix. -/\n@[local simp]\ntheorem t2 : True"
    )
    assert mod.declarations[2].signature == "/-- Doc after scoped prefix. -/\ndef d1 : Nat"
    for decl in mod.declarations:
        assert " in" not in decl.signature


def test_root_prefix_overrides_open_namespace() -> None:
    src = (
        "namespace New.Selmer\n"
        "theorem _root_.New.Data.foo (x : Nat) : x = x := rfl\n"
        "def localHelper : Nat := 1\n"
        "end New.Selmer\n"
    )
    mod = extract_declarations(src, rel_path="Root.lean", commit=COMMIT)
    assert [d.name for d in mod.declarations] == ["New.Data.foo", "New.Selmer.localHelper"]
    assert mod.declarations[0].signature == "theorem New.Data.foo (x : Nat) : x = x"
    assert mod.declarations[1].signature == "def New.Selmer.localHelper : Nat"


def test_where_bodies_and_anonymous_instances_round_trip() -> None:
    src = (
        "namespace New.Rows\n"
        "instance instWhere : Foo Nat where\n"
        "  x := 1\n"
        "instance (x : Nat) : Foo Nat := bar\n"
        "instance [Inhabited T] : Inhabited (Option T) := ⟨none⟩\n"
        "instance {A} [DecidableEq A] : Foo A where\n"
        "  val := 0\n"
        "def mkRecord (n : Nat) : Rec where\n"
        "  field := n\n"
        "end New.Rows\n"
        "instance : Inhabited Nat := ⟨0⟩\n"
    )
    mod = extract_declarations(src, rel_path="Gluing.lean", commit=COMMIT)
    assert [(d.name, d.kind) for d in mod.declarations] == [
        ("New.Rows.instWhere", "instance"),
        ("New.Rows._instance_1", "instance"),
        ("New.Rows._instance_2", "instance"),
        ("New.Rows._instance_3", "instance"),
        ("New.Rows.mkRecord", "def"),
        ("_instance_4", "instance"),
    ]
    assert mod.declarations[0].signature == "instance New.Rows.instWhere : Foo Nat"
    assert mod.declarations[0].body == "where\n  x := 1"
    assert mod.declarations[1].signature == "instance New.Rows._instance_1 (x : Nat) : Foo Nat"
    assert (
        mod.declarations[2].signature
        == "instance New.Rows._instance_2 [Inhabited T] : Inhabited (Option T)"
    )
    assert (
        mod.declarations[3].signature == "instance New.Rows._instance_3 {A} [DecidableEq A] : Foo A"
    )
    assert mod.declarations[3].body == "where\n  val := 0"
    assert mod.declarations[4].signature == "def New.Rows.mkRecord (n : Nat) : Rec"
    assert mod.declarations[4].body == "where\n  field := n"
    assert mod.declarations[5].signature == "instance _instance_4 : Inhabited Nat"

    for preserve_defs in (False, True):
        rendered, _ = render_skeleton_file(mod, preserve_defs=preserve_defs)
        reparsed = extract_declarations(rendered, rel_path="skeleton/Gluing.lean", commit=COMMIT)
        assert [d.name for d in reparsed.declarations] == [d.name for d in mod.declarations]
        assert [d.signature for d in reparsed.declarations] == [
            d.signature for d in mod.declarations
        ]


def test_equation_compiler_bars_vs_norm_bars_and_round_trip() -> None:
    src = (
        "theorem norm_inline (x : Real) : |x| = |x| := rfl\n"
        "theorem norm_line_start (x : Real) :\n"
        "    |x| = |x| := by\n"
        "  rfl\n"
        "def fib : Nat → Nat\n"
        "  | 0 =>\n"
        "    let base := 0\n"
        "    base\n"
        "  | 1 => 1\n"
        "  | n + 2 => fib n + fib (n + 1)\n"
        "def isZero : Nat → Bool\n"
        "| 0 => true\n"
        "| _ => false\n"
    )
    mod = extract_declarations(src, rel_path="Bars.lean", commit=COMMIT)
    assert [d.name for d in mod.declarations] == ["norm_inline", "norm_line_start", "fib", "isZero"]
    assert mod.declarations[0].signature == "theorem norm_inline (x : Real) : |x| = |x|"
    assert mod.declarations[0].body == "rfl"
    assert mod.declarations[1].signature == "theorem norm_line_start (x : Real) :\n    |x| = |x|"
    assert mod.declarations[1].body == "by\n  rfl"
    assert mod.declarations[2].signature == "def fib : Nat → Nat"
    assert mod.declarations[2].body == (
        "  | 0 =>\n    let base := 0\n    base\n  | 1 => 1\n  | n + 2 => fib n + fib (n + 1)"
    )
    assert mod.declarations[3].signature == "def isZero : Nat → Bool"
    assert mod.declarations[3].body == "| 0 => true\n| _ => false"

    for preserve_defs in (False, True):
        rendered, _ = render_skeleton_file(mod, preserve_defs=preserve_defs)
        reparsed = extract_declarations(rendered, rel_path="skeleton/Bars.lean", commit=COMMIT)
        assert [d.name for d in reparsed.declarations] == [d.name for d in mod.declarations]
        assert [d.signature for d in reparsed.declarations] == [
            d.signature for d in mod.declarations
        ]
        if preserve_defs:
            assert reparsed.declarations[2].body == mod.declarations[2].body
            assert reparsed.declarations[3].body == mod.declarations[3].body
