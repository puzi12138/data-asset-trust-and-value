# Generate a real software example

The bundled `SMOKE-01` fixture is synthetic software test data. It is not a scientific dataset or a new data-value observation. On a supported resource platform, generate a complete example with:

```sh
python scripts/experiments/run_task.py --task-id SMOKE-01 --seed 1103 --output ../community-smoke --timeout 60 --device cpu
```

The runner emits the unique run ID. Its directory contains the result manifest, metrics, CSV predictions, cost/environment records, README, independent verifier report and detailed JSON evidence under `worker_output/`. The fixture and protocol are bundled; no data/model download occurs. The example fits the specified tiny models under the same GO handshake and resource monitoring. A failed preflight retains a stopped run rather than a fabricated result.

For a separate read-only check, use the emitted directory:

```sh
python scripts/experiments/verify_result.py --result path/to/run-directory --data-root tests/fixtures/community_smoke --output ../smoke-verify.json
```

`REPRODUCED` confirms consistency of this software example only. Do not register its metrics as a real-source training-utility label.
