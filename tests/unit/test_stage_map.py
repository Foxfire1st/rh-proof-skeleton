"""Unit tests for stage-map validation, import-driven DAG assembly, and DAG models."""

from __future__ import annotations

import json
import subprocess
from collections import deque
from pathlib import Path
from typing import Any

import pytest

from rh_pipeline.graph.import_projection import scan_lean_header_imports
from rh_pipeline.graph.stage_map import (
    StageMapError,
    build_dag,
    extract_and_write,
    lean_module_name_for_path,
    load_stage_map,
)
from rh_pipeline.lean.parser import extract_declarations
from rh_pipeline.models.dag import (
    DAG_SCHEMA_VERSION,
    STAGE_MAP_SCHEMA_VERSION,
    DagDeclarationRef,
    DagGraph,
    DagNode,
    StageDefinition,
    StageFileMapping,
    StageMap,
)
from rh_pipeline.models.lean import ExtractedModule, LeanDeclaration

_COMMIT = "a" * 40
_SHA = "b" * 64
_REPO_ROOT = Path(__file__).resolve().parents[2]


def _stage_dict(
    stage_id: str, order: int, files: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    return {
        "stage_id": stage_id,
        "order": order,
        "title": f"Title {stage_id}",
        "summary": f"Summary {stage_id}",
        "files": files or [],
    }


def _file_dict(upstream: str, skeleton: str, preserve_defs: bool = True) -> dict[str, Any]:
    return {"upstream_path": upstream, "skeleton_file": skeleton, "preserve_defs": preserve_defs}


def _make_stage(
    stage_id: str, order: int, files: tuple[StageFileMapping, ...] = ()
) -> StageDefinition:
    return StageDefinition(stage_id, order, f"Title {stage_id}", f"Summary {stage_id}", files)


def _make_module(upstream_path: str, imports: tuple[str, ...]) -> ExtractedModule:
    decl = LeanDeclaration(
        "p", "def", "def p : Prop", "True", 1, 1, upstream_path, _COMMIT, 1, _SHA
    )
    return ExtractedModule(upstream_path, _COMMIT, imports, (decl,))


def _make_ref(name: str, kind: str, upstream_path: str, start_line: int = 4) -> DagDeclarationRef:
    data: dict[str, Any] = {
        "end_line": start_line + 1,
        "kind": kind,
        "name": name,
        "start_line": start_line,
        "upstream_end_line": 2,
        "upstream_path": upstream_path,
        "upstream_start_line": 1,
    }
    return DagDeclarationRef.from_dict(data)


def _write_stage_map(path: Path, stages: list[dict[str, Any]]) -> Path:
    payload = {"schema_version": STAGE_MAP_SCHEMA_VERSION, "stages": stages}
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("upstream_path", "expected"),
    [
        ("src/Statements.lean", "Statements"),
        ("comparator/FormalConjecturesStatement.lean", "FormalConjecturesStatement"),
        ("src/Development/Foo/Bar.lean", "Development.Foo.Bar"),
        ("comparator/Sub/Mod.lean", "Sub.Mod"),
        ("Custom/Alpha.lean", "Custom.Alpha"),
        ("Root.lean", "Root"),
    ],
)
def test_lean_module_name_for_path_valid(upstream_path: str, expected: str) -> None:
    assert lean_module_name_for_path(upstream_path) == expected


@pytest.mark.parametrize(
    "bad_path",
    [
        "",
        "src/Statements.txt",
        "/src/Statements.lean",
        "src\\Statements.lean",
        "src/../Statements.lean",
        ".lean",
        "src/.lean",
        "comparator/.lean",
        "src//Statements.lean",
    ],
)
def test_lean_module_name_for_path_invalid(bad_path: str) -> None:
    with pytest.raises(StageMapError):
        lean_module_name_for_path(bad_path)


def test_load_real_stage_map() -> None:
    stages = load_stage_map(_REPO_ROOT / "dag" / "stage-map.json")
    assert len(stages) == 8
    assert [stage.order for stage in stages] == list(range(8))
    assert [stage.stage_id for stage in stages] == [
        "00_statement",
        "01_reduction",
        "02_derivatives",
        "03_upper_bound",
        "04_spanning",
        "05_determinant",
        "06_one_lift",
        "07_congruences",
    ]
    assert [len(stage.files) for stage in stages] == [3, 6, 5, 5, 5, 6, 5, 6]
    assert sum(len(stage.files) for stage in stages) == 41
    assert all(len(stage.files) > 0 for stage in stages)

    preserve_by_skeleton = {
        mapping.skeleton_file: mapping.preserve_defs for stage in stages for mapping in stage.files
    }
    assert preserve_by_skeleton["00_statement/statements.lean"] is True
    assert preserve_by_skeleton["00_statement/challenge.lean"] is True
    assert preserve_by_skeleton["00_statement/main.lean"] is False
    for stage in stages[1:]:
        assert all(mapping.preserve_defs is False for mapping in stage.files)


def test_load_stage_map_sorts_by_order(tmp_path: Path) -> None:
    path = _write_stage_map(
        tmp_path / "stage-map.json",
        [_stage_dict("01_next", 1), _stage_dict("00_first", 0)],
    )
    assert [s.stage_id for s in load_stage_map(path)] == ["00_first", "01_next"]


@pytest.mark.parametrize(
    ("stages_payload", "match"),
    [
        ([], "at least one stage"),
        ([_stage_dict("dup", 0), _stage_dict("dup", 1)], "Duplicate stage_id"),
        ([_stage_dict("a", 0), _stage_dict("b", 0)], "Duplicate stage order"),
        ([_stage_dict("a", 0), _stage_dict("b", 2)], "consecutive"),
        ([_stage_dict("a", 1)], "consecutive"),
        (
            [
                _stage_dict(
                    "a",
                    0,
                    [_file_dict("src/A.lean", "00/a.lean"), _file_dict("src/A.lean", "00/b.lean")],
                )
            ],
            "Duplicate upstream_path",
        ),
        (
            [
                _stage_dict(
                    "a",
                    0,
                    [_file_dict("src/A.lean", "00/s.lean"), _file_dict("src/B.lean", "00/s.lean")],
                )
            ],
            "Duplicate skeleton_file",
        ),
        ([_stage_dict("a", 0, [_file_dict("src/A.lean", "00/a.txt")])], r"must end with '\.lean'"),
        (
            [
                _stage_dict(
                    "a",
                    0,
                    [
                        _file_dict("src/S.lean", "00/s1.lean"),
                        _file_dict("comparator/S.lean", "00/s2.lean"),
                    ],
                )
            ],
            "Duplicate Lean module name",
        ),
    ],
)
def test_load_stage_map_validation_errors(
    tmp_path: Path, stages_payload: list[dict[str, Any]], match: str
) -> None:
    path = _write_stage_map(tmp_path / "bad.json", stages_payload)
    with pytest.raises(StageMapError, match=match):
        load_stage_map(path)


def test_load_stage_map_io_and_json_errors(tmp_path: Path) -> None:
    with pytest.raises(StageMapError):
        load_stage_map(tmp_path / "missing.json")
    bad_json = tmp_path / "corrupt.json"
    bad_json.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(StageMapError):
        load_stage_map(bad_json)
    bad_utf8 = tmp_path / "binary.json"
    bad_utf8.write_bytes(b"\xff\xfe\x00")
    with pytest.raises(StageMapError):
        load_stage_map(bad_utf8)


def test_build_dag_uses_strict_import_edges_no_false_challenge_edge() -> None:
    files = (
        StageFileMapping("src/Statements.lean", "00_statement/statements.lean", True),
        StageFileMapping("src/Challenge.lean", "00_statement/challenge.lean", True),
        StageFileMapping("src/Main.lean", "00_statement/main.lean", False),
        StageFileMapping(
            "comparator/FormalConjecturesStatement.lean", "00_statement/fc_stmt.lean", True
        ),
        StageFileMapping(
            "comparator/FormalConjecturesChallenge.lean", "00_statement/fc_chal.lean", False
        ),
    )
    stages = (_make_stage("00_statement", 0, files), _make_stage("01_empty", 1))
    modules = {
        "src/Statements.lean": _make_module("src/Statements.lean", ("Mathlib.NumberTheory",)),
        # Challenge does NOT import Statements even though it declares the same symbol name.
        "src/Challenge.lean": _make_module("src/Challenge.lean", ("Mathlib.NumberTheory",)),
        "src/Main.lean": _make_module(
            "src/Main.lean", ("Statements", "Statements", "Main", "Development.Unmapped")
        ),
        "comparator/FormalConjecturesStatement.lean": _make_module(
            "comparator/FormalConjecturesStatement.lean", ("Mathlib.Analysis",)
        ),
        "comparator/FormalConjecturesChallenge.lean": _make_module(
            "comparator/FormalConjecturesChallenge.lean",
            ("Statements", "FormalConjecturesStatement", "Mathlib.Analysis"),
        ),
    }
    decl_refs = {
        "src/Statements.lean": (_make_ref("RH", "def", "src/Statements.lean"),),
        "src/Challenge.lean": (
            _make_ref("RH", "def", "src/Challenge.lean", start_line=4),
            _make_ref("riemann_hypothesis", "theorem", "src/Challenge.lean", start_line=8),
        ),
        "src/Main.lean": (_make_ref("main_rh", "lemma", "src/Main.lean"),),
        "comparator/FormalConjecturesStatement.lean": (
            _make_ref("fc_rh", "abbrev", "comparator/FormalConjecturesStatement.lean"),
        ),
        "comparator/FormalConjecturesChallenge.lean": (
            _make_ref("fc_thm", "theorem", "comparator/FormalConjecturesChallenge.lean"),
        ),
    }

    dag = build_dag(stages, modules, decl_refs, upstream_commit=_COMMIT)
    assert dag.schema_version == DAG_SCHEMA_VERSION
    assert dag.upstream_commit == _COMMIT
    nodes = {node.id: node for node in dag.nodes}

    assert nodes["00_statement/statements"].depends_on == ()
    assert nodes["00_statement/statements"].is_landmark is False
    assert nodes["00_statement/statements"].title == "Statements"
    assert nodes["00_statement/challenge"].depends_on == ()
    assert nodes["00_statement/challenge"].is_landmark is True
    assert nodes["00_statement/main"].depends_on == ("00_statement/statements",)
    assert nodes["00_statement/main"].is_landmark is True
    assert nodes["00_statement/fc_stmt"].is_landmark is False
    assert nodes["00_statement/fc_chal"].depends_on == (
        "00_statement/fc_stmt",
        "00_statement/statements",
    )

    encoded = dag.to_json()
    assert encoded.endswith("\n")
    assert DagGraph.from_dict(json.loads(encoded)) == dag


def test_build_dag_error_cases() -> None:
    mapping = StageFileMapping("src/Statements.lean", "00_statement/statements.lean", True)
    stages = (_make_stage("00_statement", 0, (mapping,)),)
    mod = _make_module("src/Statements.lean", ())
    ref = _make_ref("RH", "def", "src/Statements.lean")
    mods = {"src/Statements.lean": mod}
    refs = {"src/Statements.lean": (ref,)}

    with pytest.raises(StageMapError, match="upstream_commit"):
        build_dag(stages, mods, refs, upstream_commit="  ")
    with pytest.raises(StageMapError, match="Missing extracted module"):
        build_dag(stages, {}, refs, upstream_commit=_COMMIT)
    with pytest.raises(StageMapError, match="Missing declaration refs"):
        build_dag(stages, mods, {}, upstream_commit=_COMMIT)
    with pytest.raises(StageMapError, match="Module path mismatch"):
        other = _make_module("src/Other.lean", ())
        build_dag(stages, {"src/Statements.lean": other}, refs, upstream_commit=_COMMIT)
    dup_refs = (ref, _make_ref("RH", "theorem", "src/Statements.lean", start_line=10))
    with pytest.raises(StageMapError, match="Duplicate declaration"):
        build_dag(stages, mods, {"src/Statements.lean": dup_refs}, upstream_commit=_COMMIT)


def test_dag_models_strict_validation() -> None:
    with pytest.raises(ValueError, match="schema_version"):
        StageMap.from_dict({"schema_version": "wrong/v0", "stages": []})
    with pytest.raises(ValueError, match="key mismatch"):
        StageMap.from_dict({"schema_version": STAGE_MAP_SCHEMA_VERSION, "stages": [], "extra": 1})
    with pytest.raises(ValueError, match="skeleton/"):
        StageFileMapping("src/A.lean", "skeleton/00/a.lean", True)
    with pytest.raises(ValueError, match="invalid path segments"):
        StageFileMapping("src/../A.lean", "00/a.lean", True)
    with pytest.raises(ValueError, match="boolean"):
        StageFileMapping.from_dict(_file_dict("src/A.lean", "00/a.lean") | {"preserve_defs": "yes"})
    with pytest.raises(ValueError, match="end_line"):
        DagDeclarationRef("t", "theorem", 5, 4, "src/A.lean", 1, 2)
    with pytest.raises(ValueError, match="kind"):
        _make_ref("t", "macro", "src/A.lean")
    with pytest.raises(ValueError, match="key mismatch"):
        DagNode.from_dict({"id": "x"})


def _is_acyclic(nodes: tuple[DagNode, ...]) -> bool:
    in_degree = {node.id: 0 for node in nodes}
    dependents: dict[str, list[str]] = {node.id: [] for node in nodes}
    for node in nodes:
        for dep_id in node.depends_on:
            in_degree[node.id] += 1
            dependents[dep_id].append(node.id)
    ready = deque(node_id for node_id, deg in in_degree.items() if deg == 0)
    visited = 0
    while ready:
        current = ready.popleft()
        visited += 1
        for nxt in dependents[current]:
            in_degree[nxt] -= 1
            if in_degree[nxt] == 0:
                ready.append(nxt)
    return visited == len(nodes)


def _init_git_repo(repo_dir: Path, files: dict[str, str]) -> Path:
    repo_dir.mkdir(parents=True, exist_ok=True)
    for rel_path, content in files.items():
        target = repo_dir / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    for cmd in (
        ["git", "init", "-b", "main"],
        ["git", "add", "."],
        ["git", "-c", "user.name=Test", "-c", "user.email=t@example.com", "commit", "-m", "init"],
    ):
        subprocess.run(cmd, cwd=repo_dir, check=True, capture_output=True)
    return repo_dir


def test_scan_lean_header_imports_handles_comments_and_module_syntax() -> None:
    source = (
        "-- import Fake.LineComment\n"
        "module\n"
        "prelude\n"
        "/-! Module header /- nested import Fake.Nested -/ still comment\n"
        "public import Fake.BlockComment\n"
        "-/\n"
        "public import New.Alpha\n"
        "import Mathlib.Data.Real.Basic -- trailing comment\n"
        "meta import New.Beta\n"
        "public meta import all New.Gamma\n"
        "public section\n"
        "import Fake.AfterHeader\n"
        "def x : Nat := 1\n"
    )
    assert scan_lean_header_imports(source) == (
        "Mathlib.Data.Real.Basic",
        "New.Alpha",
        "New.Beta",
        "New.Gamma",
    )


def test_build_dag_with_projected_depends_on_and_validation() -> None:
    files = (
        StageFileMapping("src/A.lean", "00/a.lean", False),
        StageFileMapping("src/B.lean", "00/b.lean", False),
        StageFileMapping("src/C.lean", "00/c.lean", False),
    )
    stages = (_make_stage("00_stage", 0, files),)
    modules = {
        "src/A.lean": _make_module("src/A.lean", ("Unmapped.Bridge",)),
        "src/B.lean": _make_module("src/B.lean", ("C",)),
        "src/C.lean": _make_module("src/C.lean", ()),
    }
    decl_refs = {
        "src/A.lean": (_make_ref("ta", "theorem", "src/A.lean"),),
        "src/B.lean": (_make_ref("tb", "theorem", "src/B.lean"),),
        "src/C.lean": (_make_ref("tc", "theorem", "src/C.lean"),),
    }

    direct_dag = build_dag(stages, modules, decl_refs, upstream_commit=_COMMIT)
    assert {n.id: n.depends_on for n in direct_dag.nodes} == {
        "00/a": (),
        "00/b": ("00/c",),
        "00/c": (),
    }

    projected = {"00/a": ("00/c", "00/a", "00/b", "00/b")}
    proj_dag = build_dag(
        stages, modules, decl_refs, upstream_commit=_COMMIT, projected_depends_on=projected
    )
    assert {n.id: n.depends_on for n in proj_dag.nodes} == {
        "00/a": ("00/b", "00/c"),
        "00/b": ("00/c",),
        "00/c": (),
    }

    with pytest.raises(StageMapError, match="Unknown node ID"):
        build_dag(
            stages, modules, decl_refs, upstream_commit=_COMMIT, projected_depends_on={"00/z": ()}
        )
    with pytest.raises(StageMapError, match="Unknown projected dependency"):
        build_dag(
            stages,
            modules,
            decl_refs,
            upstream_commit=_COMMIT,
            projected_depends_on={"00/a": ("00/missing",)},
        )


def test_extract_and_write_nearest_landmark_import_projection(tmp_path: Path) -> None:
    files = {
        # Multi-hop unmapped chain A -> U1 (src/) -> U2 (root) -> B, mapped boundary A -> M -> C,
        # and an out-of-scope comparator/CompOnly.lean intermediate that must NOT project A -> C.
        "src/A.lean": (
            "import U1\n"
            "import CompOnly\n"
            "public import M\n"
            "import Escaped\n"
            "import Mathlib.Data.Real.Basic\n"
            "import A\n\n"
            "theorem thm_a : True := by\n  trivial\n"
        ),
        "src/U1.lean": (
            "module\n"
            "/- nested /- comment -/ -/\n"
            "public import U2\n"
            "import Init.Core\n\n"
            'macro "ignored_body_syntax" : term => `(0)\n'
        ),
        "U2.lean": "import B\n",
        "comparator/CompOnly.lean": "import C\n",
        # Mapped landmark M stops projection so A does NOT inherit C through M.
        "src/M.lean": "import C\n\ntheorem thm_m : True := by\n  trivial\n",
        # Diamond + cycle in unmapped files (B -> D1, D2 -> D3 -> D1, B, C).
        "src/B.lean": "import D1\nimport D2\n\ntheorem thm_b : True := by\n  trivial\n",
        "src/D1.lean": "import D3\n",
        "src/D2.lean": "public import D3\n",
        "D3.lean": "import D1\nimport B\nimport C\n",
        "src/C.lean": "import Mathlib.Analysis.Basic\n\ntheorem thm_c : True := by\n  trivial\n",
    }
    upstream = _init_git_repo(tmp_path / "upstream", files)
    escaped = tmp_path / "outside.lean"
    escaped.write_text("import C\n", encoding="utf-8")
    (upstream / "src" / "Escaped.lean").symlink_to(escaped)

    stage_map = _write_stage_map(
        tmp_path / "stage-map.json",
        [
            _stage_dict(
                "00_test",
                0,
                [
                    _file_dict("src/A.lean", "00_test/a.lean", False),
                    _file_dict("src/M.lean", "00_test/m.lean", False),
                    _file_dict("src/B.lean", "00_test/b.lean", False),
                    _file_dict("src/C.lean", "00_test/c.lean", False),
                ],
            )
        ],
    )
    dag = extract_and_write(
        upstream_root=upstream,
        stage_map_path=stage_map,
        out_skeleton_dir=tmp_path / "skeleton",
        out_dag_path=tmp_path / "dag.json",
    )
    deps = {node.id: node.depends_on for node in dag.nodes}
    assert deps == {
        "00_test/a": ("00_test/b", "00_test/m"),
        "00_test/m": ("00_test/c",),
        "00_test/b": ("00_test/c",),
        "00_test/c": (),
    }


def test_committed_dag_and_skeleton_round_trip() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    dag_raw = (repo_root / "dag" / "dag.json").read_text(encoding="utf-8")
    dag = DagGraph.from_dict(json.loads(dag_raw))
    assert dag.to_json() == dag_raw
    assert len(dag.stages) == 8
    assert len(dag.nodes) == 41
    assert _is_acyclic(dag.nodes)

    status_raw = json.loads(
        (repo_root / "audit" / "verification-status.json").read_text(encoding="utf-8")
    )
    assert dag.upstream_commit == status_raw["upstream_commit"]

    nodes = {node.id: node for node in dag.nodes}
    assert nodes["00_statement/statements"].depends_on == ()
    assert nodes["00_statement/challenge"].depends_on == ("00_statement/statements",)
    assert "01_reduction/new_main" in nodes["00_statement/main"].depends_on

    in_degree = {node.id: 0 for node in dag.nodes}
    for node in dag.nodes:
        for dep_id in node.depends_on:
            in_degree[dep_id] += 1
    isolated = [node.id for node in dag.nodes if in_degree[node.id] + len(node.depends_on) == 0]
    assert isolated == []

    for node in dag.nodes:
        skel_path = repo_root / "skeleton" / node.skeleton_file
        parsed = extract_declarations(
            skel_path.read_text(encoding="utf-8"),
            rel_path=node.skeleton_file,
            commit=dag.upstream_commit,
        )
        assert [(d.name, d.kind, d.start_line, d.end_line) for d in parsed.declarations] == [
            (r.name, r.kind, r.start_line, r.end_line) for r in node.declarations
        ]
