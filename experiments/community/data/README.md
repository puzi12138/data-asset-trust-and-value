# Dataset preparation and provenance

This repository does not redistribute source data. The preparation script downloads a small, immutable UCI archive, verifies its pinned SHA-256, reconstructs a dataset and a frozen group-based split, and writes the resulting files **outside** the repository. The six UCI source pages identify the dataset creators, required attribution, citation, and CC BY 4.0 terms; follow those links when sharing results. `SOURCES.json` records exact URLs, archive hashes, byte limits, target types, and sizes. The prepared records are adapted: groups and controlled modifications are added, and this adaptation must be disclosed.

| Task | Public dataset | Rows | Features | Target / task | Archive bytes |
| --- | --- | ---: | ---: | --- | ---: |
| EXP-01 | [Spambase](https://archive.ics.uci.edu/dataset/94/spambase) | 4,601 | 57 | Class; classification | 125,537 |
| EXP-02 | [Concrete Compressive Strength](https://archive.ics.uci.edu/dataset/165/concrete+compressive+strength) | 1,030 | 8 | Strength; regression | 34,444 |
| EXP-03 | [Wine](https://archive.ics.uci.edu/dataset/109/wine) | 178 | 13 | Cultivar; classification | 6,038 |
| EXP-04 | [Auto MPG](https://archive.ics.uci.edu/dataset/9/auto+mpg) | 398 | 7 | MPG; regression | 14,873 |
| EXP-05 | [Ionosphere](https://archive.ics.uci.edu/dataset/52/ionosphere) | 351 | 34 | Class; classification | 29,983 |
| EXP-06 | [Airfoil Self-Noise](https://archive.ics.uci.edu/dataset/291/airfoil+self+noise) | 1,503 | 5 | Sound pressure; regression | 60,126 |

Example, after installing pinned dependencies, with `DATA_DIR` an absolute directory outside this repository:

```sh
python scripts/experiments/prepare_data.py --task-id EXP-03 --seed 1103 --output /absolute/path/to/data
```

For Windows PowerShell, use a path such as `C:/experiment-data` in place of `/absolute/path/to/data`. Run with `--dry-run` to inspect the source and output path without network access or writes. An existing verified local archive can be used with `--cache /absolute/cache`, where the filename is `<dataset>.zip` as named in `SOURCES.json`. The script refuses an existing task/seed output directory; choose a new output root or preserve and inspect the old directory before continuing. The downloaded archive is not automatically kept as part of the result.

Every archive is limited to 1 MB, every consumed member to 5 MB, and the SHA-256 must match the pin. A mismatch fails closed, including if the upstream publisher updates a file. Do not replace the pinned source or modify the role split: a changed source needs a separately reviewed protocol version.

Concrete requires `xlrd==2.0.1` to read the original `Concrete_Data.xls` in the official ZIP. The UCI convenience CSV rounds target values; using it would change the frozen experiment and is intentionally rejected. Airfoil groups frequency sweeps by quantized operating parameters other than frequency; this is a leakage-reduction proxy, not proof of independence.

The split and all UCI source labels can be reconstructed publicly. These are **public reproduction sets**, not a hidden independent final test. The deterministic controlled perturbations are diagnostic interventions, not verified naturally incorrect labels. An external private evaluator is required for genuine blind testing. Source SHA-256 verifies file identity, not fact accuracy, authorship, or the honesty of a submitted result.
