Prompts (text, secret, select, multiselect, confirm) share the screens' look
and the theme's colors: a labelled box with the keys of every other screen
(esc cancels); the answer stays in the scrollback as one plain
`question: answer` line (a secret as its mask, a multiselect as the chosen
labels), nothing is left when the prompt is cancelled. With stderr redirected
(`2>log`) a prompt draws on the controlling terminal instead of the file, as
`ui.run` does, and refuses (exit 2) when there is no controlling terminal to
draw on; a long list is cut to a short terminal's height.
([#532](https://github.com/alexisbeaulieu97/untaped/pull/532))
