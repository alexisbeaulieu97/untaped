`untaped recipe hooks run --content -` fails with "no content provided" when
nothing is piped, instead of waiting silently on the terminal; piped content
loses one trailing newline, as other stdin inputs do.
