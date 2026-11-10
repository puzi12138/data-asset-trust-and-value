# Accepted result index

No real scientific contribution has been accepted yet. Do not interpret software fixtures or CI outputs as training-utility labels.

Small result submissions may use `submissions/<run_id>/` in a Pull Request after privacy review. Each directory must contain the complete required JSON/CSV result, all required arms, failed-run state where applicable, cost data and hashes. At most four runs per PR keeps automated checks bounded. Larger evidence should use maintainer-approved storage with immutable hashes and an explicit retention agreement; the verifier does not automatically download arbitrary result URLs.

GitHub consistency-report artifacts are retained for 30 days and are not the permanent scientific archive. Maintainers must copy accepted evidence to an approved durable archive before expiry, register the exact hashes and keep all conditions, including negative results. Do not rely on an expiring artifact as the only source of a paper result.

`REPRODUCED` means that the trusted checker recalculated the submitted quantities. `SCIENTIFICALLY_ACCEPTED` requires a separate maintainer review, recording protocol compliance, source independence, all failure/negative conditions and consent to release. Multiple machines rerunning the same task are reproducibility observations, not new independent datasets.
