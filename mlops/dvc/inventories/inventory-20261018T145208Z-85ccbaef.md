# Dataset inventory

- Status: **completed**
- Mode: `fast`
- Source: `dvc-tracked-cache-snapshot`
- Generated UTC: `2026-10-18T14:52:36.643953+00:00`
- Provenance: **pointer_cache_match**
- Inventory fingerprint: `cd36bc2de23f763bbfae85458fb7d07fefc501e7c5fd2475f77b7b8ae2f3267c`

| Path | Bytes | Lines | Parsed | Phishing | Legitimate | Malformed | Invalid URLs | SHA-256 |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| `meta.txt` | 314 | 7 | 0 | 0 | 0 | 5 | 0 | `bfe60c84f29ac9f0d7aff2689a3043932b72facb180b0c5384fd366c2318b178` |
| `test.txt` | 46773412 | 515081 | 515080 | 229766 | 285314 | 0 | 152 | `809293088c671858c7f3545ca8e4f54c933bf519c71e9751d313bad13182e1c2` |
| `train.txt` | 326952092 | 1 | 0 | 0 | 0 | 1 | 0 | `97060d913bbb512d7339a4c4607b91c55d1cae0ddcf77c9cf44783dbcaec2369` |
| `val.txt` | 94967448 | 1045776 | 1045772 | 466501 | 579271 | 3 | 287 | `93fa3619479588f758861792ca126af9f807cc8c63a8aeb9c16fb8b6003f3710` |

## DVC pointer comparison

```json
{
  "pointer_file_set_matches": true,
  "files_matching_pointer_md5": [
    "meta.txt",
    "test.txt",
    "train.txt",
    "val.txt"
  ],
  "files_differing_from_pointer_md5": [],
  "untracked_local_files": []
}
```

## Overlap checks

- Exact URL and cross-split overlap scan: **not run in fast mode**
- Registrable-domain overlap: **not run**

## Limitations

- Fast mode does not perform duplicate or cross-split overlap indexing.
- DVC hash agreement establishes a tracked snapshot, not original source or incumbent training history.
- Registrable-domain overlap requires a pinned public suffix list, unavailable in this environment.
