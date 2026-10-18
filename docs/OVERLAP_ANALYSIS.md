# Exact URL and domain overlap audit

`mlops.dvc.validate_splits` remains the single validator for local splits. It now supports per-split exact and canonical URL counts, label conflicts, hostname overlap, and PSL-aware registrable-domain overlap. The external overlap helper in the same module compares an evaluation TSV against any list of named training files using a disk-backed SQLite index.

The validator accepts an operator-supplied PSL file only with its exact SHA-256 and a release or immutable retrieval identifier. It implements exact, wildcard and exception PSL rules and includes the pinned list hash/version in the report. The repository/environment did not contain a PSL snapshot; the full production overlap scan therefore remains unrun. Do not substitute hostname suffix splitting.

```powershell
python -m mlops.dvc.validate_splits `
  --dataset dataset/big_dataset `
  --report mlops/dvc/full_validation.json `
  --psl-file C:\provenance\public_suffix_list.dat `
  --psl-sha256 <reviewed-file-sha256> `
  --psl-version <upstream-release-or-commit>
```

An `*.partial.json` marker is written while this CLI runs and retained with `status: incomplete` after failure. A completed report has `status: completed`, `completed_at_utc`, the input fingerprint, and a deterministic hash for each split. Restarting from the beginning is safe; SQLite temporary state is not treated as a resume checkpoint. For the inventory wrapper, run:

```powershell
python -m mlops.dvc.inventory --mode full --pointer dataset/big_dataset.dvc `
  --psl-file C:\provenance\public_suffix_list.dat `
  --psl-sha256 <reviewed-file-sha256> --psl-version <upstream-release-or-commit>
```

For prospective evaluation use `python -m mlops.evaluation.prospective` as documented in [the protocol](PROSPECTIVE_EVALUATION.md). Its overlap report binds each supplied input checksum and PSL checksum/version. Include every known historical and prospective training file; unavailable incumbent history still prevents a claim of complete historical independence.
