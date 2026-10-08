# China official snapshots

Each canonical China macro series is stored as a frozen CSV plus a metadata sidecar.

Example:

```text
industrial_production_yoy.csv
industrial_production_yoy.meta.yaml
```

CSV:

```csv
observation_date,observation_period,available_date,availability_basis,availability_evidence_url,source_value_url,value
2026-08-31,2026-08,2026-09-15,official_release,https://www.stats.gov.cn/sj/zxfb/...,https://www.stats.gov.cn/sj/zxfb/...,5.2
2026-07-31,2026-07,,unknown,,https://www.stats.gov.cn/sj/zxfb/...,5.0
```

Metadata:

```yaml
series_key: cn_industrial_production
source_name: National Bureau of Statistics of China
source_url: https://...
frequency: monthly
unit: percent_yoy
downloaded_at: 2026-10-07T13:30:00+08:00
reported_as: yoy_percent
revision_policy: frozen_release_snapshot
notes: ""
```

`availability_basis` is row-level and must be one of:

- `official_release`: the release document confirms the actual publication date; include its URL in `availability_evidence_url`.
- `official_schedule`: the official calendar announced a planned date; include the calendar URL. This is not proof that the release occurred on that date.
- `fixed_lag`: an estimate generated from `availability_lag_days` in the metadata sidecar.
- `unknown`: the date could not be verified; leave `available_date` and the evidence URL blank.
- `unverified`: a date exists in a legacy snapshot but its evidence has not yet been checked.

`source_value_url` identifies the official document containing the number; it can be a linked PDF. It is required whenever the column is present. `availability_evidence_url` independently records why `available_date` is trusted.

## Collection and current coverage limits

Run the official-source collector with:

```bash
uv sync --all-packages
uv run macro-regime-quant collect-china-snapshots \
  --start-year 2005 --end-year 2026 --output data/raw/china
```

Install `pypdf>=5.0` in the workspace environment to parse official PDF attachments. Install `matplotlib>=3.8` and a Jupyter kernel to execute the audit notebook's coverage figure.

The command writes CSV snapshots, `.meta.yaml` sidecars, and `china_harvest_audit.yaml`. It scans official NBS and PBOC archives and records an actual publication date only when the original release page states it. A planned date, URL timestamp, or release-lag estimate is not treated as observed.

The indexed NBS monthly archive currently reaches to about 2021-09. The PBOC money-supply archive reaches to about 2009-11. TSF stock has year-end reports from 2014-12 and monthly reports through 2025-08, with gaps that are listed in the quality audit. This is partial evidence, not a continuous 2005-2026 panel. Core CPI, official Chinese 10-year yields, Customs and SAFE series are not part of this collector yet. Values before those archive floors require separately located original releases. Do not fill the gaps with later revised databases while labeling them point-in-time.

The stored values keep their original definitions: NBS fixed-asset investment is cumulative YTD YoY, industrial production's January-February release is a joint period, and PBOC money/TSF metrics use their reported YoY rates. No extra `yoy()` is applied. Check each `.meta.yaml` and the audit before interpreting factor history; coverage and methodology changes are material.

For indicators published jointly for January-February, the period is assigned to February. December retail uses the December-specific YoY stated in the annual source page; December fixed-asset investment uses the published January-December cumulative YoY. These are released in January and therefore enter the point-in-time panel only after their actual publication dates.

Never fill an unknown date with a guessed release day. `build-china-baseline` uses only `official_release` rows by default. Broader availability policies are explicit CLI options and should be reported with results.

Use `--allow-partial-sources` when configured China source files are still absent. Factor component minimums remain in force; an under-sourced factor stays missing. The current observed coverage and factor/regime availability are summarized in `docs/research/china_official_snapshot_quality_2026-10.md` and `notebooks/china_snapshot_quality.ipynb`.
