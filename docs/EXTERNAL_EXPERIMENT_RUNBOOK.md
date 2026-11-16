# External experiments: shared supervised runner and Letter pilot

This branch adds an isolated runner for the existing EXT tasks. Historical EXP inputs, code and results remain unchanged. The current runnable adapter is **EXT-02 Letter Recognition**. Covertype, Superconductivity and Bike Sharing still require tested adapters and, for regression, target-model/metric extensions. Do not pass unsupported IDs to this command.

Windows10/11 x64, Python3.10 x64, CPU only. No GPU/model downloads. UCI Letter Recognition (Frey & Slate,1991), DOI10.24432/C5ZP40, CC BY4.0. The official archive is approximately0.37MiB; raw CSV approximately0.68MiB. Results contain JSON tree models and full predictions, so reserve1GiB disk. Runtime is subject to actual memory/CPU competition.

## Work ownership

EXT-01 (#14) coordinates shared-runner changes; the maintainer supplies this initial candidate. EXT-02 (#15) owns Letter adapter/independent machine reproduction; EXT-03/#16 and EXT-04/#17 keep their own adapters. One owner per shared file. Existing Issue updates are not new tasks. Claim work and inspect current PR/revision first; do not duplicate an in-progress adapter or edit frozen old results.

## Preparation

Use the commit linked in the maintainer's Issue update. Clone/switch to that exact revision in a new checkout, then run from its root. Keep the same `$DATV_Out` for every seed/model/budget of this task; do not reset the1800-second ledger by changing directories. If a directory already exists, preserve it and inspect its receipts.

```powershell
$ErrorActionPreference = 'Stop'
py -3.10 -m venv .venv
if ($LASTEXITCODE -ne 0) { throw 'Environment creation failed' }
$DATV_Python = Join-Path (Get-Location) '.venv\Scripts\python.exe'
& $DATV_Python -m pip install --disable-pip-version-check -r requirements-external.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
$DATV_Base = Join-Path $env:USERPROFILE 'datv-letter-inputs'
$DATV_Out = Join-Path $env:USERPROFILE 'datv-letter-runs'
if (Test-Path -LiteralPath $DATV_Base) { throw 'Input directory already exists; preserve it' }
New-Item -ItemType Directory -Path $DATV_Base | Out-Null
& $DATV_Python -B -m unittest -v tests.test_external_runner tests.test_external_data tests.test_external_worker tests.test_external_verify
if ($LASTEXITCODE -ne 0) { throw 'Tests failed; stop before training' }
& $DATV_Python -B scripts/external_experiments/fetch_letter.py --out (Join-Path $DATV_Base 'source')
if ($LASTEXITCODE -ne 0) { throw 'Official source/license/hash check failed' }
$DATV_Data = Join-Path $DATV_Base 'seed1103'
& $DATV_Python -B scripts/external_experiments/prepare_letter.py --raw (Join-Path $DATV_Base 'source/letter-recognition.data') --source-record (Join-Path $DATV_Base 'source/source.json') --seed 1103 --out $DATV_Data
if ($LASTEXITCODE -ne 0) { throw 'Data preparation failed' }
$DATV_Config = Join-Path $DATV_Data 'config.json'
$DATV_Freeze = Join-Path $DATV_Base 'CODE_FREEZE_1103.json'
& $DATV_Python -B scripts/external_experiments/run.py --config $DATV_Config --data-dir $DATV_Data --freeze $DATV_Freeze --freeze-only
if ($LASTEXITCODE -ne 0) { throw 'Source/input freeze failed' }
& $DATV_Python -B scripts/external_experiments/run.py --config $DATV_Config --data-dir $DATV_Data --freeze $DATV_Freeze --dry-run
if ($LASTEXITCODE -ne 0) { throw 'Resource or dependency preflight failed' }
```

This CSV-only requirement file deliberately does not require xlrd. It does not amend the legacy full-dependency environment. The data source CLI records the actual source HTML, license attribution, download URL and byte hashes; changed raw version stops for review.

## Run the approved first seed

After the maintainer confirms the fixed shared revision and input preparation, run once:

```powershell
& $DATV_Python -B scripts/external_experiments/run.py --config $DATV_Config --data-dir $DATV_Data --freeze $DATV_Freeze --output $DATV_Out
if ($LASTEXITCODE -ne 0) { throw 'Run stopped or requires review; preserve all outputs, do not tune/retry' }
```

The supervisor starts one worker after code/input checks and a nonce/PID handshake, assigns a Windows Job Object, and monitors its process tree. Free memory must start at>=1.5GiB and remain>=0.75GiB; treeRSS<=1.5GiB; worker<=300s; fixed output-parent cumulative budget1800s. Limits cannot be relaxed by this CLI. This prevents accidental unmonitored work, not adversarial activity by the same OS user.

The first seed1103 has18 target fits (2 models ×[D0 +2 budgets×4 selectors]) and1 small conformity detector fit. The target models and selectors are exactly those in #14/#15. All8 selections freeze before parsing held-out labels. Convergence warnings remain failed arms, with no parameter change. Metadata, CPU/RSS costs, failed/unrecorded arms, full JSON models and predictions are preserved. No pickle/joblib objects are loaded or submitted.

Only after this first seed is accepted should2207/3301 be prepared in new input directories and run with new freeze files against the **same** `$DATV_Out` ledger. Do not change seeds or choose one based on test outcomes. Each task is one source; seeds/arms are not independent sources.

## Replay without training

Each run automatically invokes independent verification. To repeat it, set the actual run ID and choose a new report outside inputs and worker output:

```powershell
$DATV_Run = Join-Path $DATV_Out '<actual-run-id>'
$DATV_Check = Join-Path $DATV_Out '<new-report-name>.json'
& $DATV_Python -B scripts/external_experiments/verify.py --result (Join-Path $DATV_Run 'worker_output') --config $DATV_Config --data-dir $DATV_Data --output $DATV_Check
if ($LASTEXITCODE -ne 0) { throw 'Replay failed or contains failed arms; preserve report' }
```

Verifier uses the standard library and trusted checkout arithmetic. It reconstructs D0 transforms, reference evidence/features, detector scores, all8 choices, saved linear/RF predictions, and all metrics. `REPRODUCED` means numerical consistency. It does not certify identity, natural-label correctness, blind evaluation or universal screening advantage. Probability clipping at1e-12 and renormalization are part of this new protocol; do not silently mix with historical metrics.

## Submit and extend

Submit code revision, source/license record, prepared inputs/member hashes, CODE_FREEZE, all planned arms, every prediction, model JSON, costs, supervisor/manifests and verification. Use relative slash ZIP member names and a full64-character SHA-256. Exclude virtual environments, tokens, account names, private paths and unrelated files. Raw small UCI data may be provided with attribution; avoid nesting duplicate archives.

Covertype needs54-feature streaming/group preparation and7-class validation. Regression tasks need Ridge/RandomForestRegressor serialization and MAE/RMSE/R² independent replay, plus their specific group/time isolation. Extend in separate files/tests and freeze new implementations; this candidate does not yet implement those tasks.
