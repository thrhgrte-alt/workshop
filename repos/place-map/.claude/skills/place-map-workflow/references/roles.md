# Roles and scores

score(r, x) = clamp( sum_i w_i * f_i(x) / sum_i |w_i| , 0, 1 ), each f_i in [0, 1]; a negative weight counts against the role. Bands per role: at or above `confident` = selected (auto-labelled), between
`candidate` and `confident` = listed as not selected for the user to confirm, below `candidate` = ignored. A user label overrides the score (confirm = selected, reject = never selected).

Generic features (implemented once, `placemap/domain/roles.py`): token match (name and attribute keys against synonym lists), class presence (the instance or a descendant), remote and string references
(scripts inside or mentioning the instance), repetition (n_similar / (n_similar + c) among siblings, similar = same class signature and name-token Jaccard above 0.6), reference density (mentions normalised by the
place's median), proximity cluster (near a confident landmark of another role). Roles live in `rules/roles.yaml`; synonyms in `rules/synonyms.yaml` plus `projects/<project_id>/synonyms.yaml`; all are placeholders.

A container (a folder of vendors) is not itself a vendor: it inherits evidence from what is inside, so it is dropped when a descendant scores higher for the role (or when it is a plain Folder with a candidate inside).

Learning: `label_landmark` applies w_i <- w_i + eta * (label - score) * f_i(x) (eta 0.1 by default), clamped to the parameter range and one step, stored as a new version of `weight.<role>.<feature>` for the project
(or globally if the user marks it), undoable. Precedence: explicit label, then learned project weights, then promoted global weights, then the default weights.
