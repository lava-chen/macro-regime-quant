from pathlib import Path

import pandas as pd
from mrq_data import china_harvest
from mrq_data.china_harvest import (
    NBS_LIST_URL,
    PBOC_LIST_URL,
    Article,
    Link,
    _money_value,
    _nbs_annual_december_period,
    _nbs_annual_retail_december_value,
    _parse_page,
    _pboc_period,
    _period_from_title,
    collect_nbs_snapshots,
    collect_pboc_snapshots,
)
from mrq_data.snapshots import validate_snapshot


def test_original_release_dates_and_periods_are_parsed_without_url_inference():
    page = _parse_page(
        """<meta name="ArticleTitle" content="2020年11月份规模以上工业增加值增长7.0%">
        <meta name="PubDate" content="2020-12-15 10:00:00">
        <div id="zoom"><p>规模以上工业增加值同比增长7.0%。</p></div>"""
    )

    assert page.title == "2020年11月份规模以上工业增加值增长7.0%"
    assert page.publication_date == "2020-12-15"
    assert "同比增长7.0%" in page.body_text
    assert _period_from_title("2020年1—2月份国民经济运行情况") == (
        2020,
        2,
        "2020-02-29",
        "2020-01/2020-02",
    )
    assert _period_from_title("2011年5月金融统计数据报告")[2] == "2011-05-31"
    assert _pboc_period("2024年社会融资规模存量统计数据报告")[2] == "2024-12-31"


def test_nbs_annual_release_keeps_december_retail_value_and_full_year_investment():
    retail_title = "2021年社会消费品零售总额增长12.5%"
    retail_body = (
        "2021年，社会消费品零售总额440823亿元，比上年增长12.5%。"
        "2021年12月份，社会消费品零售总额41269亿元，同比增长1.7%。"
    )
    investment_title = "2021年全国固定资产投资（不含农户）增长4.9%"

    assert _nbs_annual_december_period("retail_sales", retail_title, retail_body) == (
        2021,
        12,
        "2021-12-31",
        "2021-12",
    )
    assert _nbs_annual_retail_december_value(retail_body) == 1.7
    assert _nbs_annual_december_period(
        "fixed_asset_investment", investment_title, "2021年1—12月份，全国固定资产投资"
    ) == (2021, 12, "2021-12-31", "2021-12")


def test_nbs_collector_writes_original_value_and_release_evidence(tmp_path: Path):
    listing = """<script>createPageHTML(1, 0, 'index', 'html');</script>
    <a href="2020/1215/1812345.html">2020年11月份规模以上工业增加值增长7.0%</a>"""
    article_url = "https://www.stats.gov.cn/sj/zxfb/2020/1215/1812345.html"
    article = """<meta name="ArticleTitle" content="2020年11月份规模以上工业增加值增长7.0%">
    <meta name="PubDate" content="2020-12-15 10:00:00">
    <div id="zoom"><p>规模以上工业增加值同比增长7.0%。</p></div>"""

    def fetch(url: str) -> str:
        if url == NBS_LIST_URL:
            return listing
        if url == article_url:
            return article
        raise AssertionError(f"Unexpected URL: {url}")

    audit = collect_nbs_snapshots(
        tmp_path,
        start_year=2020,
        end_year=2020,
        fetch_text=fetch,
        max_workers=1,
    )
    path = tmp_path / "industrial_production_yoy.csv"
    frame = pd.read_csv(path)
    validated = validate_snapshot(path)

    assert audit["candidate_articles"] == 1
    assert frame.loc[0, "observation_date"] == "2020-11-30"
    assert frame.loc[0, "available_date"] == "2020-12-15"
    assert frame.loc[0, "availability_basis"] == "official_release"
    assert frame.loc[0, "value"] == 7.0
    assert frame.loc[0, "source_value_url"] == article_url
    assert validated.rows == 1


def test_nbs_collector_captures_annual_december_retail_and_investment_rows(tmp_path: Path):
    listing = """<script>createPageHTML(1, 0, 'index', 'html');</script>
    <a href="202601/retail.html">2025年12月份社会消费品零售总额增长0.9%</a>
    <a href="202601/investment.html">2025年全国固定资产投资基本情况</a>"""
    retail_url = "https://www.stats.gov.cn/sj/zxfb/202601/retail.html"
    investment_url = "https://www.stats.gov.cn/sj/zxfb/202601/investment.html"
    retail = """<meta name="ArticleTitle" content="2025年12月份社会消费品零售总额增长0.9%">
    <meta name="PubDate" content="2026-01-19">
    <div id="zoom"><p>12月份，社会消费品零售总额45136亿元，同比增长0.9%。</p></div>"""
    investment = """<meta name="ArticleTitle" content="2025年全国固定资产投资基本情况">
    <meta name="PubDate" content="2026-01-19">
    <div id="zoom"><p>2025年1—12月份，全国固定资产投资比上年下降3.8%。</p></div>"""

    def fetch(url: str) -> str:
        if url == NBS_LIST_URL:
            return listing
        if url == retail_url:
            return retail
        if url == investment_url:
            return investment
        raise AssertionError(f"Unexpected URL: {url}")

    collect_nbs_snapshots(
        tmp_path,
        start_year=2025,
        end_year=2025,
        fetch_text=fetch,
        max_workers=1,
    )
    retail_frame = pd.read_csv(tmp_path / "retail_sales_yoy.csv")
    investment_frame = pd.read_csv(tmp_path / "fixed_asset_investment_ytd_yoy.csv")

    assert retail_frame.loc[0, "observation_period"] == "2025-12"
    assert retail_frame.loc[0, "available_date"] == "2026-01-19"
    assert retail_frame.loc[0, "value"] == 0.9
    assert investment_frame.loc[0, "observation_period"] == "2025-12"
    assert investment_frame.loc[0, "available_date"] == "2026-01-19"
    assert investment_frame.loc[0, "value"] == -3.8


def test_pboc_collector_keeps_monetary_series_yoy_and_true_release_date(tmp_path: Path):
    listing = """<input name="article_paging_list_hidden" totalpage="1">
    <a href="/diaochatongjisi/116219/116225/nov2020/index.html">
      2020年11月金融统计数据报告
    </a>
    <a href="/diaochatongjisi/116219/116225/tsf2020/index.html">
      2020年11月社会融资规模存量统计数据报告
    </a>"""
    money_url = "https://www.pbc.gov.cn/diaochatongjisi/116219/116225/nov2020/index.html"
    tsf_url = "https://www.pbc.gov.cn/diaochatongjisi/116219/116225/tsf2020/index.html"
    money = """<meta name="ArticleTitle" content="2020年11月金融统计数据报告">
    <meta name="PubDate" content="2020-12-09 09:00:00">
    <div id="zoom"><p>M1余额同比增长10.0%，M2余额同比增长10.7%。</p></div>"""
    tsf = """<meta name="ArticleTitle" content="2020年11月社会融资规模存量统计数据报告">
    <meta name="PubDate" content="2020-12-11 09:00:00">
    <div id="zoom"><p>社会融资规模存量为280.07万亿元，同比增长13.6%。</p></div>"""

    def fetch(url: str) -> str:
        if url == PBOC_LIST_URL:
            return listing
        if url == money_url:
            return money
        if url == tsf_url:
            return tsf
        raise AssertionError(f"Unexpected URL: {url}")

    audit = collect_pboc_snapshots(
        tmp_path,
        start_year=2020,
        end_year=2020,
        fetch_text=fetch,
        fetch_bytes=lambda url: (_ for _ in ()).throw(AssertionError(url)),
        max_workers=1,
    )
    m1 = pd.read_csv(tmp_path / "m1_yoy.csv")
    m2 = pd.read_csv(tmp_path / "m2_yoy.csv")
    tsf_frame = pd.read_csv(tmp_path / "tsf_stock_yoy.csv")

    assert audit["candidate_articles"] == 2
    assert m1.loc[0, "value"] == 10.0
    assert m2.loc[0, "value"] == 10.7
    assert tsf_frame.loc[0, "value"] == 13.6
    assert m1.loc[0, "available_date"] == "2020-12-09"
    assert tsf_frame.loc[0, "available_date"] == "2020-12-11"
    assert _money_value("M 1 余额同比增长10.0%，M 2余额同比增长10.7%", 1) == 10.0


def test_pboc_pdf_fallback_extracts_m1_m2_and_retains_pdf_url(monkeypatch):
    pdf_url = "https://www.pbc.gov.cn/official/money-report.pdf"
    article = Article(
        title="2011年5月金融统计数据报告",
        publication_date="2011-06-13",
        body_text="",
        all_text="",
        links=(Link("附件：金融统计数据报告.pdf", pdf_url),),
    )
    monkeypatch.setattr(
        china_harvest,
        "_extract_pdf_text",
        lambda _: "M1余额同比增长12.7%。M2余额同比增长15.1%。",
    )

    values, sources, errors = china_harvest._pboc_pdf_fallback(
        article,
        "https://www.pbc.gov.cn/official/release.html",
        lambda _: b"PDF bytes",
    )

    assert values == {"m1": 12.7, "m2": 15.1}
    assert sources == {"m1": pdf_url, "m2": pdf_url}
    assert not errors


def test_snapshot_validation_requires_value_source_for_new_provenance_schema(
    tmp_path: Path,
):
    path = tmp_path / "bad.csv"
    pd.DataFrame(
        {
            "observation_date": ["2020-01-31"],
            "available_date": ["2020-02-15"],
            "availability_basis": ["official_release"],
            "availability_evidence_url": ["https://example.org/release"],
            "source_value_url": [""],
            "value": [1.0],
        }
    ).to_csv(path, index=False)
    path.with_suffix(".meta.yaml").write_text(
        """series_key: example
source_name: Official source
source_url: https://example.org
frequency: monthly
unit: percent
downloaded_at: 2020-03-01T00:00:00Z
reported_as: yoy_percent
revision_policy: frozen_release_snapshot
""",
        encoding="utf-8",
    )

    try:
        validate_snapshot(path)
    except ValueError as exc:
        assert "source_value_url" in str(exc)
    else:
        raise AssertionError("snapshot with empty source_value_url should fail")
