"""``untaped profile …``: list, show, create, rename and delete profiles.

Profile data is stored in the home's ``config.yml``: the base (``default``)
at its top level, named profiles under ``profiles:``. A run stacks the
profiles ``UNTAPED_PROFILE`` and then ``--profile`` name over the base.
"""

from __future__ import annotations

from collections.abc import Iterator

from pydantic import Field

from untaped.core.profile.models import (
    PickedProfile,
    Profile,
    ProfileDeletePreview,
    ProfileOutcome,
    ProfilePick,
    ProfileView,
)
from untaped.core.profile.repository import ProfileFileRepository
from untaped.core.profile.use_cases import (
    CreateProfile,
    CurrentProfile,
    DeleteProfile,
    ListProfiles,
    RenameProfile,
    ShowProfile,
)
from untaped.core.settings import CoreSettings
from untaped.runtime.settings import get_profile_settings_model, resolve_config_path
from untaped.sdk._schema import redact_secrets, secret_field_paths
from untaped.sdk.fields import Confirm, Output, Positional
from untaped.sdk.plugin import Command, Group
from untaped.sdk.words import q

_DRY_RUN = "Check the change and write nothing."


class List(Command[CoreSettings]):
    """List the base (default) and every profile, marking the ones the run reads.

    Examples:
        $ untaped profile list
        $ untaped profile list -f json
    """

    output: Output[Profile]

    def run(self) -> Iterator[Profile]:
        for profile in ListProfiles(ProfileFileRepository())():
            yield Profile(name=profile.name, selected=profile.selected, keys=profile.key_count)


class Show(Command[CoreSettings]):
    """Print a profile's settings: the base merged with it, or with the run's pick.

    Secrets show as *** unless --show-secrets is passed, as in config list.
    --raw shows only the keys the profile itself sets.

    Examples:
        $ untaped profile show stage
        $ untaped profile show stage --raw -f json
    """

    name: Positional[str | None] = Field(
        None, description="The profile to show; the run's pick when left out."
    )
    raw: bool = Field(False, description="Show only the keys this profile sets (no merge).")
    show_secrets: bool = Field(False, description="Reveal secret values instead of ***.")
    output: Output[ProfileView] = Output(format="yaml")

    def run(self) -> ProfileView:
        profile = ShowProfile(ProfileFileRepository())(self.name, raw=self.raw)
        data = profile.data
        if not self.show_secrets:
            data = redact_secrets(data, secret_field_paths(get_profile_settings_model()))
        return ProfileView(name=profile.name, selected=profile.selected, raw=self.raw, data=data)


class Current(Command[CoreSettings]):
    """Print the picked profiles, comma-joined: one line for a shell prompt or --profile.

    The pick is UNTAPED_PROFILE's profiles, then the root --profile
    option's; default (the base alone) when neither names one. In raw and
    table forms, what picked them (flag, env or fallback) goes to stderr;
    structured forms list each profile with its source.

    Examples:
        $ untaped profile current
        $ untaped profile current -f json
    """

    output: Output[ProfilePick] = Output(format="raw")

    def run(self) -> ProfilePick:
        picks = CurrentProfile(ProfileFileRepository())()
        if self.output.format in ("raw", "table"):
            sources = ", ".join(dict.fromkeys(pick.source for pick in picks)) or "fallback"
            self.ctx.diag.info(f"(source: {sources})")
        return ProfilePick(
            name=",".join(pick.name for pick in picks) or "default",
            profiles=[PickedProfile(name=pick.name, source=pick.source) for pick in picks],
        )


class Create(Command[CoreSettings]):
    """Create a profile: empty, or a copy of another with --copy-from.

    Examples:
        $ untaped profile create stage --copy-from default
        $ untaped profile create lab --dry-run
    """

    name: Positional[str] = Field(description="The new profile's name.")
    copy_from: str | None = Field(
        None, description="An existing profile (or default) to copy as a starting point."
    )
    dry_run: bool = Field(False, description=_DRY_RUN)
    output: Output[ProfileOutcome]

    def run(self) -> ProfileOutcome:
        CreateProfile(ProfileFileRepository())(
            self.name, copy_from=self.copy_from, dry_run=self.dry_run
        )
        if not self.dry_run:
            suffix = f" (copied from {self.copy_from})" if self.copy_from else ""
            self.ctx.diag.success(f"created profile: {self.name}{suffix}")
        return ProfileOutcome(
            name=self.name,
            copied_from=self.copy_from,
            action="planned" if self.dry_run else "created",
        )


class Delete(Command[CoreSettings]):
    """Delete a profile (the base, default, is not one).

    Examples:
        $ untaped profile delete stage --dry-run
        $ untaped profile delete stage --yes
    """

    name: Positional[str] = Field(description="The profile to delete.")
    confirm: Confirm
    output: Output[ProfileOutcome]

    def run(self) -> ProfileOutcome:
        delete = DeleteProfile(ProfileFileRepository())
        preview = delete.preview(self.name)
        if not self.confirm(f"Delete profile {q(preview.name)}?", preview=lambda: _lines(preview)):
            return ProfileOutcome(name=self.name, action="planned")
        delete(self.name)
        self.ctx.diag.success(f"deleted profile: {self.name}")
        return ProfileOutcome(name=self.name, action="deleted")


def _lines(preview: ProfileDeletePreview) -> list[str]:
    return [
        f"config: {resolve_config_path()}",
        f"profile: {preview.name}",
        f"top-level keys: {', '.join(preview.top_level_keys) or '(none)'}",
    ]


class Rename(Command[CoreSettings]):
    """Rename a profile.

    Examples:
        $ untaped profile rename stage staging
    """

    old_name: Positional[str] = Field(description="The profile's name now.")
    new_name: Positional[str] = Field(description="Its new name.")
    dry_run: bool = Field(False, description=_DRY_RUN)
    output: Output[ProfileOutcome]

    def run(self) -> ProfileOutcome:
        RenameProfile(ProfileFileRepository())(self.old_name, self.new_name, dry_run=self.dry_run)
        if not self.dry_run:
            self.ctx.diag.success(f"renamed profile: {self.old_name} → {self.new_name}")
        return ProfileOutcome(
            name=self.new_name,
            previous_name=self.old_name,
            action="planned" if self.dry_run else "renamed",
        )


class ProfileGroup(Group):
    """Manage the profiles in the home's config.yml."""

    commands = (List, Show, Current, Create, Delete, Rename)


__all__ = ["ProfileGroup"]
