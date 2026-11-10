# Result exchange and verification

The run manifest schema is a structural reference; `scripts/experiments/verify_result.py` enforces additional fixed task/seed, exact configuration and code hashes, file membership, resource record and data constraints. A schema match alone is not scientific acceptance.

Failed and not-started manifests are retained as operational evidence. They do not require fabricated metrics or predictions and cannot receive `REPRODUCED`; the result checker rejects them for successful-run acceptance. Submit them for maintainer review, not as scientific utility labels. CI checks at most four new or changed run directories per PR; unchanged archived runs do not count toward this intake limit. Repeated task/seed runs share one trusted data preparation but each result is verified separately.

`PREDICTIONS.csv` uses columns in this order: `arm,split,id,group,y,prediction,probabilities,classes,loss`. Classification probabilities/classes are JSON arrays encoded as CSV fields; regression leaves both blank. Detailed per-arm JSON remains under `worker_output/`. Do not submit pickle or executable result objects.

Status meanings: `SUBMITTED` is receipt, `SCHEMA_VALID` means basic file/schema checks; `REPRODUCED` means trusted data/config hashes, group isolation, fixed selection rules, saved linear-model predictions and reported metrics were arithmetically replayed. `NEEDS_REVIEW` means required trusted inputs or review are missing; `REJECTED` indicates a failed validation. `SCIENTIFICALLY_ACCEPTED` requires a separate authorized independent protocol review and is never granted by this checker. Published tests and controlled disturbances are reconstructible and are not a blind final evaluation.
