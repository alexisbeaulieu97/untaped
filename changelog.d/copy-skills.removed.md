Copy skills, deprecated in 11.1: `SkillAsset(source=...)` is gone, every
skill is a stub written from core's template, and a plugin ships at most one.
untaped no longer converts an 11.0 copy to a stub, writes or reads
`backups/skills/`, or sees a `.untaped-skill.json` directory as its own. The
`untaped.skill` and `untaped.installed_skill` records lose `kind` and
`untaped.skill` loses `source`.
