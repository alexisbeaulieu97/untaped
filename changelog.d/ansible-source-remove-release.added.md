`untaped ansible source remove NAME` releases each repo only that source
used from the repo store: the repo goes when nothing else uses it, else
ansible's refs go and the outcome says who kept it. A repo another saved
source still selects is kept.
