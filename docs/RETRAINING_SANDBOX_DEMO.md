# OPTIMUS synthetic retraining lifecycle sandbox

Run from the repository root in PowerShell:

```powershell
python scripts/demo_retraining_sandbox.py
```

All generated databases, synthetic feedback, immutable training batches,
evaluation fixtures, and status reports are written under
`%TEMP%\optimus-retraining-demo-sandbox\runs\<run-id>`. Existing unowned paths
are refused. Each run is unique. To remove only files listed in prior
sandbox-generated manifests before making a new run:

```powershell
python scripts/demo_retraining_sandbox.py --reset
```

Reset preserves unlisted files and incomplete runs for inspection. Do not put
personal files under the reserved sandbox root.

The runner uses the real SQLite lifecycle functions to create synthetic
prediction/feedback rows, record explicitly synthetic review provenance,
evaluate the configured 1,000-record/100-per-class trigger, and export a
registered batch. URLs use reserved `.invalid` names and are never printed.
The synthetic reviewer field is a demonstration marker, not a human review or
label evidence about real URLs.

It calls the real candidate worker only with `execute=False`. The worker
requires a passed strict validation report for the current baseline splits,
matching fingerprints, an independently reviewed authoritative source
registration, and clean DVC status. The sandbox intentionally does not run the
project DVC diagnostic because that command may create a diagnostic cache in
the working tree; it fails that check closed. The unresolved baseline
provenance and validation gates therefore prevent real candidate training.
No candidate artifacts or model metrics are fabricated.

A separate synthetic evaluation TSV and manifest are generated to show the
required handoff. They explicitly mark ground truth and independence as
untrusted, and the accompanying demo-only policy has no thresholds and is
disabled because no approved policy was provided. The evaluator is not run,
there is no approval state, no promotion option, and the production registry
is never opened for writing. Promotion would also require verified incumbent
training history and trusted independent evaluation evidence, which are not
established for this project.

Validate the sandbox's isolation and repeatability helpers with:

```powershell
python -m unittest discover -s test -p 'test_retraining_sandbox_demo.py'
```

This test creates only temporary fixtures and does not train a model. The
operational run is a lifecycle demonstration, not a phishing-detection quality
result.
