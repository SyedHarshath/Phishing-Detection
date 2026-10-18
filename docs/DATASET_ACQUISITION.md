# Dataset acquisition and staged import

## Current source assessment (checked 2026-10-18)

No original dataset archive was recovered. The local workspace files are still not linked to a signed or immutable upstream release, and the DVC pointer/cache mismatch is unresolved.

The DEPHIDES [README](https://github.com/ebubekirbbr/dephides/blob/main/README.md) is a direct source description, not a byte-level attestation. It describes access-controlled Google Drive files, says positive URLs came from PhishTank and legitimate URLs from Common Crawl, and names `top100k_cc.txt` as the Common Crawl domain list. It also lists split totals. The README is on mutable `main`; the page reviewed had no pinned commit SHA recorded for this audit.

The associated paper ([DOI 10.1109/ACCESS.2024.3352629](https://doi.org/10.1109/ACCESS.2024.3352629)) describes positives as PhishTank “Valid Phish” entries through August 2018. It describes the negative set as URLs sampled from the top 100,000 domains ranked using Common Crawl, at about 30 URLs per domain and no more than 10 URLs from a domain in the final dataset. This narrows the recovery evidence to seek: the original PhishTank export and verification dates, `top100k_cc.txt`, the exact Common Crawl ranking/crawl version and sampling procedure, and the released archive bytes. The paper and README do not provide hashes for this workspace’s files.

PhishTank’s [official developer information](https://phishtank.org/developer_info.php) says its downloadable online-valid feeds are updated hourly and expose submission and verification timestamps. A current feed cannot recreate the historical snapshot. Common Crawl’s [URL Index documentation](https://commoncrawl.org/url-index) identifies monthly crawl partitions and provides `fetch_time` and registered-domain fields. Common Crawl presence alone does not prove a URL or host was benign. Capture the exact crawl and selection process, then independently review the label meaning.

### Source classification

| Source | Evidence status | Missing evidence |
|---|---|---|
| Current local workspace files | Byte identities and parse counts verified in Phase 5; origin unknown | Authorized source revision and checksummed archive |
| DVC cache snapshot | Matches the four files in the Git DVC pointer; cached train is all NUL bytes | Original upstream provenance; usable training file; configured remote |
| DEPHIDES Drive dataset | Plausible original source, access-controlled link documented upstream | Custodian access, released archive, immutable version and checksums |
| PhishTank | Official verified-online phishing feed is available, dynamic and hourly | Historical 2018 source export and exact selected records |
| Common Crawl | Versioned crawl and URL index are available | Original crawl/ranking file, sampled URLs, deterministic sampling procedure, and independent benign-label evidence |

Access date for external source pages above: **2026-10-18**. No source data or suffix list was downloaded. No URL contained in the dataset was opened.

## Operator-supplied import format

The offline importer accepts a directory, ZIP, or tar archive. It only reads files listed in a provenance manifest. Archive manifests must include the archive SHA-256. A directory manifest must include the SHA-256 of each listed original file. Required top-level fields:

```json
{
  "source_name": "custodian-provided dataset",
  "source_reference": "immutable release or access request reference",
  "version": "release id",
  "acquired_at_utc": "2026-10-18T00:00:00Z",
  "acquisition_method": "authorized custodian transfer",
  "source_attestation_reference": "signed statement, ticket, or verified custodian record",
  "source_container_sha256": "required for archive imports",
  "record_format": "UTF-8 label<TAB>URL, one record per line",
  "labels": {"phishing": 0, "legitimate": 1},
  "files": {
    "big_dataset/train.txt": "64 lowercase or uppercase hex SHA-256 characters",
    "big_dataset/val.txt": "...",
    "big_dataset/test.txt": "..."
  },
  "splits": {
    "train": "big_dataset/train.txt",
    "val": "big_dataset/val.txt",
    "test": "big_dataset/test.txt"
  }
}
```

The example hashes are placeholders, not accepted values. The source attestation must be independently reviewed; checksums only establish byte identity. The importer copies listed originals to a separate read-only staged area, creates normalized LF text snapshots, hashes each output, records transformations/exclusions, and runs the strict validator. It does not modify active dataset paths or DVC state.

```powershell
python -m mlops.dvc.staging_import import C:\data\authorized-dataset.zip --manifest C:\data\provenance.json
python -m mlops.dvc.staging_import import C:\data\dataset-dir --manifest C:\data\provenance.json
```

Review `inventory.md`, `inventory.json`, the original files, and exclusion reports in the generated stage. A passing validator is necessary but does not establish source authenticity. If any row was excluded, approval must explicitly attest that exclusions were reviewed:

```powershell
python -m mlops.dvc.staging_import approve mlops/dvc/staging/stage-<id> --operator <name> --approval-reference <review-ticket> --reviewed-exclusions
```

Registration stays inside the stage; it does not replace `dataset/big_dataset`, edit the DVC pointer, or make an automatic training run. Before training from an approved stage, the operator must preserve the registration manifest, generate a strict validation report for that exact normalized path, and separately resolve DVC tracking/cleanliness requirements. The retraining worker requires `registration.json` as its provenance manifest and remains blocked by current DVC drift/lock absence.
