"""``node_ids``: the ids a live workflow's nodes run and prompt with."""

from __future__ import annotations

from typing import Any

from untaped_awx.application.resources.workflow_graph import NodeState, node_ids


def _state(identifier: str, run: tuple[str, int] | None, **fields: Any) -> NodeState:
    return NodeState(
        identifier=identifier, run=run, approval=None, fields=fields, members={}, edges={}
    )


def test_a_node_names_what_it_runs_and_prompts_with() -> None:
    found = node_ids([_state("deploy", ("JobTemplate", 100), inventory=20)])
    assert found == {
        "nodes[deploy].run": (("JobTemplate", 100),),
        "nodes[deploy].prompts.inventory": (("Inventory", 20),),
    }


def test_an_opaque_node_names_nothing_export_left_out() -> None:
    assert node_ids([_state("gone", None, inventory=20)]) == {}
