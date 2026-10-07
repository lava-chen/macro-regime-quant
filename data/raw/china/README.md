# China official snapshots

Each canonical China macro series is stored as a frozen CSV plus a metadata sidecar.

Example:

```text
industrial_production_yoy.csv
industrial_production_yoy.meta.yaml
```

CSV:

```csv
observation_date,available_date,availability_basis,availability_evidence_url,value
2026-08-31,2026-09-15,official_release,https://www.stats.gov.cn/sj/zxfb/...,5.2
2026-07-31,,unknown,,5.0
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

Never fill an unknown date with a guessed release day. `build-china-baseline` uses only `official_release` rows by default. Broader availability policies are explicit CLI options and should be reported with results.
