"""Repository contract for the public orchestration-v1 adoption."""

from __future__ import annotations

import hashlib
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[2]
STORE = ROOT / ".untaped/orchestration"
MIGRATION = ROOT / "docs/orchestration-migration"
STORE_ID = "sto_019f68b6af9e721e970126ca31dbfde1"
SOURCE_OID = "80bb8411cd0017f3e0cde818656aaf6fd0233368"
SOURCE_SHA = "597d74559b5447942468b7fe321ab40dccbed32e4055d9fca71830702c55831e"
SOURCE_REF = f"git:{SOURCE_OID}:docs/decisions.md#sha256:{SOURCE_SHA}"
DECISION_IDS = (
    "dec_019f68b6b2cb75a9a7cb908963b4b59c",
    "dec_019f68b6b3da73acb58a68dffdc85adc",
    "dec_019f68b6b4e475179664a514f3771211",
    "dec_019f68b6b5ea758190f84ea79ed1cfbf",
    "dec_019f68b6b6f0748b8a01f0cbe11b7f12",
    "dec_019f68b6b7f87484ae84f7b788f38138",
    "dec_019f68b6b8fe72f49640813dd3240a0c",
)
TITLES = (
    "`untaped` is an SDK, not an app — plugins retired",
    "Profiles and themes are absorbed into the SDK",
    "Pipe envelope v1 — versioned independently of the SDK",
    "Config format — v1 (SDK 1.x) → v2 (SDK 2.x)",
    "The SDK ships no core agent skill",
    "`emit` detail-routing and safe HTTP retries (SDK 2.1)",
    "Tool versions come from installed distribution metadata (SDK 3.1)",
)
RANGES = (
    "1-5",
    "6-33",
    "34-34",
    "35-41",
    "42-42",
    "43-67",
    "68-68",
    "69-105",
    "106-106",
    "107-114",
    "115-115",
    "116-159",
    "160-160",
    "161-174",
)
BYTE_COUNTS = (155, 1229, 1, 401, 1, 1618, 1, 2245, 1, 446, 1, 2755, 1, 893)
BLOCK_HASHES = (
    "678151ac26af0d46d952921728fb966744002bc4b6bb559e25014318948112a5",
    "e6a6cf30d79cca55aff4228c9b80462a3cb3b2df95adf44f49affd3e05aab619",
    "01ba4719c80b6fe911b091a7c05124b64eeece964e09c058ef8f9805daca546b",
    "d622f0234e32a3d448631e4fdda18b41a3d39b6decc7cd19055ad495e3a0b35e",
    "01ba4719c80b6fe911b091a7c05124b64eeece964e09c058ef8f9805daca546b",
    "783974364b14b1db77a346e16a982a10451c026e36b9696114faf277f7750c9f",
    "01ba4719c80b6fe911b091a7c05124b64eeece964e09c058ef8f9805daca546b",
    "ddcd1fa8cf3708bf2654c10dce0dd0be6639c9e4d0e5432160924e07097c485a",
    "01ba4719c80b6fe911b091a7c05124b64eeece964e09c058ef8f9805daca546b",
    "e81081bf56586374deaf707d081d49e32d236a5495041eeb4a5126e1b991f047",
    "01ba4719c80b6fe911b091a7c05124b64eeece964e09c058ef8f9805daca546b",
    "c0e4664748b35199c428c903bba1a7683c7fa095a0b56ed3b4bdfee72c0ef957",
    "01ba4719c80b6fe911b091a7c05124b64eeece964e09c058ef8f9805daca546b",
    "442773a5e7a9141903dd7467c258a7603f2a853d0795f7ea94a5cede47cfbcea",
)
BODY_HASHES = (
    "d531d2cefd2c49b9972ffb27f81c9a41618e176ae776d05c3b60660778aea84e",
    "c3830594ab3e39007cf5a26d02c0872c80cb80f299ab76e366d642d054b1c12c",
    "7d309f7e3653e630991496c1fc8445e1666d79022299a0df17371abd2740d1ce",
    "ca54dd98c1c14b94a10605c87024a635ce2878097d2be3276d38259e0a063162",
    "e0dd408a2e19aed192f4cdb0056ee1471c82af538252d2f9b2f1c361cd528232",
    "b0058b85949a719d9475fce384606694150a191e74cfd39373ae3765233cd696",
    "091881b8688b2e4ed8cb3cf0c3e64a0a62f8bd4726d17f9439114d9ac9e1533d",
)


def load_toml(path: Path) -> dict[str, object]:
    return tomllib.loads(path.read_text())


def parse_item(path: Path) -> tuple[dict[str, object], bytes]:
    raw = path.read_bytes()
    assert raw.startswith(b"+++\n")
    _, frontmatter, body = raw.split(b"+++\n", 2)
    return tomllib.loads(frontmatter.decode()), body


def test_store_is_public_decision_only_and_childless() -> None:
    store = load_toml(STORE / "store.toml")
    assert store["schema"] == "untaped.orchestration.store/v1"
    assert store["id"] == STORE_ID
    assert store["name"] == "untaped"
    assert store["visibility"] == "public"
    assert store["timezone"] == "UTC"
    assert store["capabilities"] == {"active_tasks": False}
    assert load_toml(STORE / "registry.toml") == {
        "schema": "untaped.orchestration.registry/v1",
        "store_id": STORE_ID,
    }
    assert not list((STORE / "tasks").glob("*.md")) if (STORE / "tasks").exists() else True


def test_exact_decisions_preserve_bodies_and_evidence() -> None:
    paths = sorted((STORE / "decisions").glob("*.md"))
    assert len(paths) == 7
    parsed = [parse_item(path) for path in paths]
    by_id = {frontmatter["id"]: (frontmatter, body) for frontmatter, body in parsed}
    assert tuple(by_id) == DECISION_IDS
    for decision_id, title, body_hash in zip(DECISION_IDS, TITLES, BODY_HASHES, strict=True):
        frontmatter, body = by_id[decision_id]
        assert frontmatter["schema"] == "untaped.orchestration.decision/v1"
        assert frontmatter["kind"] == "decision"
        assert frontmatter["title"] == title
        assert frontmatter["created_at"] == "2026-07-10T00:30:02.000Z"
        assert frontmatter["evidence"] == [{"relation": "tracked-by", "reference": SOURCE_REF}]
        assert hashlib.sha256(body).hexdigest() == body_hash
    view = (STORE / "views/decisions.md").read_text()
    assert all(decision_id in view for decision_id in DECISION_IDS)
    assert "batteries-included CLI *framework*" not in view


def test_migration_coverage_is_exact_gapless_and_pending_review() -> None:
    coverage = load_toml(MIGRATION / "coverage.toml")
    assert coverage["schema"] == "untaped.orchestration.coverage/v1"
    assert coverage["source_repository"] == "untaped"
    assert coverage["source_oid"] == SOURCE_OID
    assert coverage["original_path"] == "docs/decisions.md"
    assert coverage["source_sha256"] == SOURCE_SHA
    assert coverage["source_bytes"] == 9748
    assert coverage["source_lines"] == 174
    blocks = coverage["blocks"]
    assert [block["line_range"] for block in blocks] == list(RANGES)
    assert [block["source_bytes"] for block in blocks] == list(BYTE_COUNTS)
    assert [block["block_sha256"] for block in blocks] == list(BLOCK_HASHES)
    assert {block["review_status"] for block in blocks} == {"pending-review"}
    assert all(block["disposition"] and block["destination"] for block in blocks)
    lines = [line for block in blocks for line in range(*_inclusive(block["line_range"]))]
    assert lines == list(range(1, 175))
    assert not (MIGRATION / "review.md").exists()


def _inclusive(value: str) -> tuple[int, int]:
    start, end = map(int, value.split("-"))
    return start, end + 1


def test_import_manifest_has_guarded_unique_records() -> None:
    manifest = load_toml(MIGRATION / "import.toml")
    assert manifest["schema"] == "untaped.orchestration.import/v1"
    assert manifest["target_store_id"] == STORE_ID
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", manifest["expected_store_revision"])
    assert manifest["require_empty_items"] is True
    records = manifest["records"]
    assert len(records) == 7
    assert len({record["frontmatter_file"] for record in records}) == 7
    assert len({record["body_file"] for record in records}) == 7
    assert [record["source_ref"] for record in records] == [SOURCE_REF] * 7
    assert [record["destination"] for record in records] == ["decisions"] * 7
    record_ids = [load_toml(MIGRATION / record["frontmatter_file"])["id"] for record in records]
    assert record_ids == list(DECISION_IDS)


def test_pointer_agent_rules_ignore_rules_and_workflow() -> None:
    pointer = (ROOT / "docs/decisions.md").read_text()
    assert "../.untaped/orchestration/views/decisions.md" in pointer
    assert "untaped-orchestration brief --format json" in pointer
    assert "canonical" in pointer and "generated" in pointer
    assert "orchestration-migration" in pointer
    agents = (ROOT / "AGENTS.md").read_text()
    for phrase in (
        "public decision-only",
        "revision guard",
        "--force-current",
        "human-only",
        "no tasks",
        "check --local",
        "render --check",
    ):
        assert phrase in agents
    ignores = set((ROOT / ".gitignore").read_text().splitlines())
    assert {
        ".untaped/orchestration/**/.lock",
        ".untaped/orchestration/**/.DS_Store",
        ".untaped/orchestration/**/.*.untaped-tmp-*",
        ".untaped/orchestration/**/*~",
        ".untaped/orchestration/**/*.swp",
        ".untaped/orchestration/**/*.swo",
        ".untaped/orchestration/**/*.tmp",
        ".untaped/orchestration/**/.#*",
        ".untaped/orchestration/**/#*",
    } <= ignores
    workflow = (ROOT / ".github/workflows/orchestration.yml").read_text()
    assert "permissions:\n  contents: read" in workflow
    assert "persist-credentials: false" in workflow
    assert "actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0" in workflow
    assert "astral-sh/setup-uv@fac544c07dec837d0ccb6301d7b5580bf5edae39" in workflow
    assert 'version: "0.11.26"' in workflow
    commands = re.findall(r"^\s+run: (uvx .+)$", workflow, re.MULTILINE)
    prefix = "uvx --python 3.14 --from 'untaped-orchestration==0.1.0' "
    assert commands == [
        f"{prefix}untaped-orchestration check --local",
        f"{prefix}untaped-orchestration fmt --check --local",
        f"{prefix}untaped-orchestration render --check",
    ]
    assert all(
        path in workflow
        for path in (
            ".untaped/orchestration/**",
            ".github/workflows/orchestration.yml",
            ".gitignore",
            "AGENTS.md",
            "CLAUDE.md",
            "docs/decisions.md",
            "docs/orchestration-migration/**",
        )
    )
    assert "uv sync" not in workflow
    assert "PYTHONPATH" not in workflow
    assert "render --check --local" not in workflow
