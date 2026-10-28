"""Direct unit pin for the ``LAUNCH_FLAGS`` table's silent-overwrite invariant.

End-to-end flag dispatch (every flag's payload-field translation) is
covered through the public CLI in ``tests/integration/test_resource_launch_cli.py`` —
that's the right home for those assertions per CONTRIBUTING.md ("Test through public APIs").

What an integration suite *cannot* observe is a collision: if a future
edit makes two ``LaunchFlag`` rows share the same ``accepts_key``,
``_build_launch_payload`` walks the table in order and silently
overwrites the earlier value with the later one. The bug only surfaces
if both colliding flags are exercised with different values in the same
test run — too fragile to rely on. This test pins the uniqueness so a
duplicated row fails CI before integration tests even run.
"""

from __future__ import annotations

from untaped_awx.commands.resource_actions import LAUNCH_FLAGS


def test_launch_flag_rows_are_uniquely_keyed() -> None:
    """No two rows share the same field *or* the same ``accepts_key``.

    Duplicated ``field``: one option would shadow the earlier
    declaration. Duplicated ``accepts_key``: the later row silently
    wins in ``_build_launch_payload`` — integration tests would only
    catch this if both flags are exercised with observably different
    values in the same call.
    """
    fields = [f.field for f in LAUNCH_FLAGS]
    assert len(fields) == len(set(fields)), f"duplicate field in LAUNCH_FLAGS: {fields}"

    accepts_keys = [f.accepts_key for f in LAUNCH_FLAGS]
    assert len(accepts_keys) == len(set(accepts_keys)), (
        f"duplicate accepts_key in LAUNCH_FLAGS — silent overwrite in "
        f"_build_launch_payload: {accepts_keys}"
    )
