# 宏观量化终端数据导出

终端从公开的 `macro-quant-terminal-data` 分支读取四个有版本记录的 JSON 文件。该分支只保存公开行情与宏观序列，不得包含账户信息、持仓、交易记录、API 密钥或私人实验。

| 文件 | 内容 |
| --- | --- |
| `data/exports/terminal-v0.1/summary.json` | 最近一次运行状态、各数据源最近成功时间、代码提交、因子配置版本、覆盖情况与 PIT 提示。 |
| `data/exports/terminal-v0.1/series.json` | 日频行情与月频宏观序列。每个观测点包含日期、数值和可得性依据；序列元数据记录单位、来源、覆盖区间及限制。 |
| `data/exports/terminal-v0.1/regimes.json` | 按国家导出的增长 × 通胀状态历史。 |
| `data/exports/terminal-v0.1/backtests.json` | 在存在已验证结果文件前明确标记为 `not_exported`，不提供虚构指标。 |

运行 `uv run python scripts/export_terminal_data.py` 可更新公开序列。

行情数据通过一次批量请求更新；后续运行会重取短期重叠区间，再按日期合并，减少重复下载。FRED 历史数据规模较小，每次重建月度面板，并标记为“当前修订值”，不声称具备 PIT 安全性。数据源失败会更新 `summary.json` 的状态，但保留上次有效序列；错误会显示在“数据状态”页面。

`.github/workflows/terminal-data.yml` 按上海时间周一至周五 07:30 运行，也可通过 `workflow_dispatch` 手动触发。国家统计局与中国人民银行官方归档每月扫描一次；手动运行时可选择 `refresh_china`。工作流使用仓库范围的 `GITHUB_TOKEN`，只将公开导出和官方快照写入数据分支。仓库中没有配置或提交数据服务密钥。

网站将该分支作为公开只读数据源；Sites 站点本身仅所有者可访问。站点前端源代码托管在 Sites 提供的应用仓库中；现有 GitHub 仓库作为数据和研究代码来源，并未直接绑定为 Sites 部署仓库。

## 研究限制

- 中国快照来自冻结的官方发布记录，起始日期不一且部分序列缺失；相关因子历史仅供有限观察。
- 美国因子使用 FRED 当前可见修订值和近似发布日期滞后。历史修订可能改变早期观测。
- 第 17 号 Draft PR 包含 ALFRED 历史归档工作，但当前主分支的历史时点面板尚未接入该归档。
- 第 18 号任务仍未完成。终端不声称已有 PIT 安全回测、滚动验证或样本外表现。
