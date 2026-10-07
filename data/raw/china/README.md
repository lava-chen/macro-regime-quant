# China official snapshots

Each canonical China macro series is stored as a frozen CSV plus a metadata sidecar.

Example:

```text
industrial_production_yoy.csv
industrial_production_yoy.meta.yaml
```

CSV:

```csv
observation_date,available_date,value
2026-08-31,2026-09-15,5.2
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

Use the exact publication/release date in `available_date` whenever it can be reconstructed.
The fallback fixed release lag in `data_catalog.yaml` is only for incomplete historical snapshots.
