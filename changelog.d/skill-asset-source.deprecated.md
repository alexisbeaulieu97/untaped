`SkillAsset(source=...)`, a plugin's skill shipped as a copied folder, is
deprecated and removed in 12.0: passing it warns. Leave `source` out to ship
a stub, and move the folder's facts into command docstrings, field
descriptions and guides, which `untaped agents` prints
([plugin-skills.md](https://github.com/alexisbeaulieu97/untaped/blob/v11.1.0/docs/plugin-skills.md)).
