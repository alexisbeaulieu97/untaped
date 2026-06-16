"""Profile management as a first-class SDK capability.

Absorbed from the retired untaped-profile plugin. ``build_profile_app``
returns the ``<tool> profile …`` command group that ``run_tool`` mounts on
every tool; profile resolution itself lives in
:mod:`untaped.profile_resolver` and :class:`untaped.settings_layout.ProfilesSettingsLayout`.
"""

from __future__ import annotations

from untaped.profile.app import build_profile_app
from untaped.profile.models import Profile, ProfileDeletePreview

__all__ = [
    "Profile",
    "ProfileDeletePreview",
    "build_profile_app",
]
