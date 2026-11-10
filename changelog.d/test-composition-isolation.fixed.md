The testing kit's pytest plugin puts back the composition each test began
with, so a plugin one test composed no longer comes back in a later test's
settings registry when that test leaves a `kept_composition` block.
