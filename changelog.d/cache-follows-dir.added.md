`Cache(follows=index)` ties a plugin class's cache to a numbered `Dir` of the
same class: its files live in `cached/<name>.v<layout>/`, move with the
place's layouts, and a complete fill prunes the place. A `Dir(at=)` whose
optional path setting is left unset is the default place,
`plugins/<plugin>/<attribute>/`.
