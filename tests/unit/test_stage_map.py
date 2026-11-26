"""Unit tests for stage-map validation, import-driven DAG assembly, and DAG models."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from rh_pipeline.graph.stage_map import (
    StageMapError,
    build_dag,
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
    assert len(stages[0].files) == 3
    assert all(len(stage.files) == 0 for stage in stages[1:])


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


def test_committed_dag_and_stage00_skeleton_round_trip() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    dag_raw = (repo_root / "dag" / "dag.json").read_text(encoding="utf-8")
    dag = DagGraph.from_dict(json.loads(dag_raw))
    assert dag.to_json() == dag_raw
    assert len(dag.stages) == 8
    assert len(dag.nodes) == 3

    status_raw = json.loads(
        (repo_root / "audit" / "verification-status.json").read_text(encoding="utf-8")
    )
    assert dag.upstream_commit == status_raw["upstream_commit"]

    nodes = {node.id: node for node in dag.nodes}
    assert nodes["00_statement/statements"].depends_on == ()
    assert nodes["00_statement/challenge"].depends_on == ("00_statement/statements",)
    assert nodes["00_statement/main"].depends_on == ()

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
