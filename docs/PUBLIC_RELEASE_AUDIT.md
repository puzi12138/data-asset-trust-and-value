# Public release audit

Status: LOCAL_CANDIDATE_PENDING_APPROVAL.

The proposed release adds contributor task definitions, bounded public-data preparation, guarded CPU execution, JSON/CSV result exchange, independent consistency checks and lightweight CI. It does not include private research archives or formal application documents.

Before upload the maintainer must inspect the exact changed-file manifest and its SHA-256 values, the local test receipts and the publication boundary. Publication permission, repository software license and institutional/patent clearance are separate from software tests. No approval is inferred from a passing test.

## Checks required for this candidate

- Use a fresh remote baseline and an independent branch; no force push or main replacement.
- Keep the original frozen scientific records unchanged and retain an internal adaptation comparison.
- Confirm that only approved source, protocol, schema, templates and small software examples are staged.
- Exclude raw datasets, model binaries, private documents, full review archives, credentials and local runtime state.
- Scan new file paths and text for local identities, absolute private paths, internal stage identifiers and credential patterns; inspect matches in context.
- Record all failed or skipped tests, real operating-system coverage and the final result verifier's scope.
- Publish only after explicit approval of the proposed file list. An absent license is not silently filled in.

## CI boundary

Untrusted pull-request code may run only on disposable hosted runners with read-only repository permissions, no secrets and no persisted checkout credentials. Result verification uses the trusted base-branch verifier and registry, not a verifier supplied by the result author. No pull_request_target workflow or automatic self-hosted/GPU runner is used. CI reports consistency and protocol checks, not scientific acceptance or proof that a claimed computation physically occurred.

## Publication status

No new branch, Pull Request or experiment Issue has been published as part of preparing this candidate. Six Issue bodies are supplied locally for review. Remote publication and the first scientific contribution remain separate, explicitly recorded events.
