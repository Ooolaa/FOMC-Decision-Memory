import sqlite3
import tempfile
import unittest
from pathlib import Path

from decision_memory.app_db import create_schema
from decision_memory.regional_public_communications import (
    materialize_boston_collins_speeches,
    materialize_chicago_goolsbee_speeches,
    materialize_dallas_logan_speeches,
    materialize_richmond_barkin_speeches,
    materialize_san_francisco_daly_speeches,
    parse_boston_communication,
    parse_boston_speech_links,
    parse_chicago_communication,
    parse_chicago_speech_links,
    parse_dallas_communication,
    parse_dallas_speech_links,
    parse_richmond_barkin_communication,
    parse_richmond_barkin_speech_links,
    parse_san_francisco_communication,
    parse_san_francisco_speech_links,
)


DALLAS_INDEX_HTML = b"""
<html><body>
  <a href="/news/speeches/logan/2024/lkl240405">Policy speech</a>
  <a href="/news/speeches/logan/2024/lkl240405">Duplicate</a>
  <a href="/news/speeches/other/2024/example">Other speaker</a>
  <a href="https://example.com/news/speeches/logan/2024/fake">External</a>
</body></html>
"""


DALLAS_SPEECH_HTML = b"""
<html><body>
  <nav><p>Navigation noise</p></nav>
  <div id="content" class="dal-main__container">
    <div class="row dal-main-content__container">
      <div class="dal-main-content">
        <div class="dal-crouton">Speech by President Lorie K. Logan</div>
        <h1 class="dal-headline">Economic Outlook and Monetary Policy</h1>
        <div class="dal-inline-list">
          <p class="list-item--bar dal-content-date">April 05, 2024</p>
        </div>
        <div class="dal-abstract">Remarks at an official event.</div>
        <p>Inflation remains above the Committee's objective.</p>
        <p>Policy should remain restrictive while employment is strong.</p>
      </div>
    </div>
  </div>
  <footer><p>Footer noise</p></footer>
</body></html>
"""


DALLAS_OLD_SPEECH_HTML = b"""
<html><body>
  <div class="dal-main-content">
    <div class="dal-crouton">Speech by President Lorie K. Logan</div>
    <h1 class="dal-headline">Opening Remarks</h1>
    <p><span class="date">October 4, 2022</span></p>
    <p>Monetary policy supports maximum employment and price stability.</p>
  </div>
</body></html>
"""


DALLAS_DATELESS_SPEECH_HTML = b"""
<html><body>
  <div class="dal-main-content">
    <div class="dal-crouton">Speech by President Lorie K. Logan</div>
    <h1 class="dal-headline">Opening Remarks</h1>
    <p>Inflation and monetary policy remain central to the outlook.</p>
  </div>
</body></html>
"""


CHICAGO_PROFILE_HTML = b"""
<html><body>
  <a href="/publications/speeches/2024/feb-14-policy">Policy speech</a>
  <a href="/publications/speeches/2024/feb-14-policy?ref=profile">Duplicate</a>
  <a href="/publications/articles/2024/not-a-speech">Other</a>
  <a href="https://example.com/publications/speeches/2024/fake">External</a>
</body></html>
"""


CHICAGO_SPEECH_HTML = b"""
<html><head>
  <meta name="description" content="Austan D. Goolsbee discussed monetary policy.">
</head><body>
  <div class="cfedDetail__content">
    <div class="cfedDetail__lastUpdated">Last Updated: 02/14/24</div>
    <div class="cfedDetail__title"><h1>Economic Outlook</h1></div>
  </div>
  <div class="cfedContent">
    <div class="cfedContent__body">
      <div class="cfedContent__text">
        <p>Inflation is moving toward the Federal Reserve's target.</p>
        <ul><li>Policy remains restrictive.</li></ul>
      </div>
    </div>
  </div>
  <footer><p>Footer noise</p></footer>
</body></html>
"""


BOSTON_INDEX_HTML = b"""
<html><body>
  <a href="/news-and-events/speeches/2024/policy-speech.aspx">Speech</a>
  <a href="/news-and-events/speeches/2024/policy-speech.aspx">Duplicate</a>
  <a href="/news-and-events/events/2024/not-a-speech.aspx">Other</a>
  <a href="https://example.com/news-and-events/speeches/2024/fake.aspx">External</a>
</body></html>
"""


BOSTON_SPEECH_HTML = b"""
<html><head>
  <meta property="article:published_time" content="2024-05-08T15:45:00.000Z">
  <meta itemprop="name" content="Reflections on Monetary Policymaking">
</head><body>
  <span>By <a class="byline-link">Susan M. Collins</a></span>
  <a href="/-/media/Documents/Speeches/PDF/collins/2024/20240508-text.pdf">
    Full-text speech <span>(pdf)</span>
  </a>
</body></html>
"""


BOSTON_CHART_HTML = b"""
<html><head>
  <meta property="article:published_time" content="2023-10-17T15:00:00.000Z">
  <meta itemprop="name" content="Charts: Monetary Policy Challenges">
</head><body>
  <span>By <a class="byline-link">Susan M. Collins</a></span>
  <a href="/-/media/Documents/Speeches/PDF/collins/2023/charts.pdf">
    Download charts (pdf)
  </a>
</body></html>
"""


SAN_FRANCISCO_INDEX_HTML = b"""
<html><body>
  <h2 class="wp-block-post-title">
    <a href="https://www.frbsf.org/news-and-media/speeches/mary-c-daly/2024/02/policy-speech/">Speech</a>
  </h2>
  <a href="/news-and-media/speeches/mary-c-daly/2024/02/policy-speech/">Duplicate</a>
  <a href="/news-and-media/speeches/other/2024/02/not-daly/">Other</a>
  <a href="https://example.com/news-and-media/speeches/mary-c-daly/2024/02/fake/">External</a>
</body></html>
"""


SAN_FRANCISCO_SPEECH_HTML = b"""
<html><head>
  <meta property="article:published_time" content="2024-02-16T09:49:16-08:00" />
</head><body>
  <h1 class="wp-block-post-title">Price Stability Built to Last</h1>
  <div class="speech-info">For delivery on February 16, 2024</div>
  <div class="sffed-main-content wp-block-column">
    <div class="entry-content wp-block-post-content">
      <p><em>Remarks as prepared for delivery.</em></p>
      <h2>Significant Progress</h2>
      <p>Inflation is heading down and the labor market is rebalancing.</p>
      <ul><li>Policy must finish the job.</li></ul>
    </div>
    <div class="sffed-associated-people__heading--in-content-flow">About the Speaker</div>
    <div><p>Biography noise that must not be included.</p></div>
  </div>
</body></html>
"""


SAN_FRANCISCO_SLIDES_HTML = b"""
<html><head>
  <meta property="article:published_time" content="2021-05-21T09:00:00-07:00" />
</head><body>
  <h1 class="wp-block-post-title">Wage Dynamics: Theory, Data, and Policy</h1>
  <div class="speech-info">Slides presented by Mary C. Daly</div>
  <div class="sffed-main-content"><div class="entry-content wp-block-post-content">
    <iframe src="https://slides.example"></iframe>
  </div></div>
</body></html>
"""


RICHMOND_INDEX_HTML = b"""
<html><body>
  <a href="/press_room/speeches/thomas_i_barkin/2024/barkin_speech_20240506">Speech</a>
  <a href="/press_room/speeches/thomas_i_barkin/2024/barkin_speech_20240506?ref=archive">Duplicate</a>
  <a href="/press_room/speeches/other/2024/not-barkin">Other</a>
  <a href="https://example.com/press_room/speeches/thomas_i_barkin/2024/fake">External</a>
</body></html>
"""


RICHMOND_RSS_XML = b"""<?xml version="1.0" encoding="utf-8"?>
<rss><channel>
  <link>https://www.richmondfed.org/press_room/speeches/thomas_i_barkin/2021</link>
  <item><link>https://www.richmondfed.org/press_room/speeches/thomas_i_barkin/2021/barkin_20210224</link></item>
  <item><link>https://www.richmondfed.org/press_room/speeches/thomas_i_barkin/2021/barkin_speech_20210630_en</link></item>
  <item><link>https://www.richmondfed.org/press_room/speeches/thomas_i_barkin/2021/barkin_speech_20210630_es</link></item>
</channel></rss>
"""


RICHMOND_SPEECH_HTML = b"""
<html><head>
  <meta name="citation_title" content="Navigating Data Whiplash" />
  <meta name="citation_author" content="Barkin, Tom" />
  <meta name="citation_publication_date" content="2024/05/06" />
</head><body>
  <div class="comp-highlights"><p>Summary noise outside the speech.</p></div>
  <div class="tmplt__content">
    <p>Inflation data have moved unevenly in recent months.</p>
    <h2>What do I see?</h2>
    <p>Monetary policy should remain deliberate while employment is strong.</p>
    <ul><li>The outlook remains uncertain.</li></ul>
  </div>
  <footer><p>Footer noise.</p></footer>
</body></html>
"""


class RegionalPublicCommunicationTests(unittest.TestCase):
    def test_richmond_archive_keeps_unique_official_barkin_speeches(self):
        self.assertEqual(
            parse_richmond_barkin_speech_links(
                RICHMOND_INDEX_HTML,
                "https://www.richmondfed.org/press_room/speeches/"
                "thomas_i_barkin/2024",
                years={2024},
            ),
            [
                "https://www.richmondfed.org/press_room/speeches/"
                "thomas_i_barkin/2024/barkin_speech_20240506"
            ],
        )

    def test_richmond_rss_keeps_english_barkin_speeches_only(self):
        self.assertEqual(
            parse_richmond_barkin_speech_links(
                RICHMOND_RSS_XML,
                "https://www.richmondfed.org/press_room/speeches/"
                "thomas_i_barkin/2021?cc_view=rss",
                years={2021},
            ),
            [
                "https://www.richmondfed.org/press_room/speeches/"
                "thomas_i_barkin/2021/barkin_20210224",
                "https://www.richmondfed.org/press_room/speeches/"
                "thomas_i_barkin/2021/barkin_speech_20210630_en",
            ],
        )

    def test_richmond_parser_extracts_citation_metadata_and_body(self):
        parsed = parse_richmond_barkin_communication(RICHMOND_SPEECH_HTML)

        self.assertEqual(parsed["publication_date"], "2024-05-06")
        self.assertEqual(parsed["speaker_label"], "Tom Barkin")
        self.assertEqual(parsed["title"], "Navigating Data Whiplash")
        self.assertIn(
            "Monetary policy should remain deliberate while employment is strong.",
            parsed["paragraphs"],
        )
        self.assertNotIn("Footer noise.", parsed["paragraphs"])

    def test_richmond_materialization_is_resumable_and_persona_only(self):
        index_url = (
            "https://www.richmondfed.org/press_room/speeches/"
            "thomas_i_barkin/2024?cc_view=rss"
        )
        page_url = (
            "https://www.richmondfed.org/press_room/speeches/"
            "thomas_i_barkin/2024/barkin_speech_20240506"
        )

        def fetcher(url):
            return {
                index_url: (
                    b"<?xml version='1.0'?><rss><channel><item><link>"
                    + page_url.encode()
                    + b"</link></item></channel></rss>"
                ),
                page_url: RICHMOND_SPEECH_HTML,
            }[url]

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_path = root / "app.sqlite"
            app = sqlite3.connect(app_path)
            create_schema(app)
            app.execute(
                "INSERT INTO participant VALUES "
                "('thomas-i-barkin','Thomas I. Barkin','policymaker',NULL,NULL)"
            )
            app.commit()
            app.close()

            kwargs = {
                "years": [2024],
                "cache_root": root / "cache",
                "fetcher": fetcher,
            }
            first = materialize_richmond_barkin_speeches(app_path, **kwargs)
            second = materialize_richmond_barkin_speeches(app_path, **kwargs)
            app = sqlite3.connect(app_path)
            source = app.execute(
                "SELECT meeting_id, document_type, publication_at, usage_class "
                "FROM document_source"
            ).fetchone()
            communication = app.execute(
                "SELECT participant_id, title, text FROM public_communication"
            ).fetchone()
            app.close()

        self.assertEqual(first["ingested_document_count"], 1)
        self.assertEqual(second["new_cache_file_count"], 0)
        self.assertEqual(
            source,
            (None, "speech", "2024-05-06T23:59:59Z", "persona_evidence"),
        )
        self.assertEqual(communication[0], "thomas-i-barkin")
        self.assertIn("Monetary policy should remain deliberate", communication[2])

    def test_san_francisco_archive_keeps_unique_official_daly_speeches(self):
        self.assertEqual(
            parse_san_francisco_speech_links(
                SAN_FRANCISCO_INDEX_HTML,
                "https://www.frbsf.org/news-and-media/speeches/mary-c-daly/",
                years={2024},
            ),
            [
                "https://www.frbsf.org/news-and-media/speeches/mary-c-daly/"
                "2024/02/policy-speech/"
            ],
        )

    def test_san_francisco_parser_extracts_dated_daly_speech_body(self):
        parsed = parse_san_francisco_communication(
            SAN_FRANCISCO_SPEECH_HTML,
            source_url=(
                "https://www.frbsf.org/news-and-media/speeches/mary-c-daly/"
                "2024/02/policy-speech/"
            ),
        )

        self.assertEqual(parsed["publication_date"], "2024-02-16")
        self.assertEqual(parsed["speaker_label"], "Mary C. Daly")
        self.assertEqual(parsed["title"], "Price Stability Built to Last")
        self.assertIn("Policy must finish the job.", parsed["paragraphs"])
        self.assertNotIn(
            "Biography noise that must not be included.", parsed["paragraphs"]
        )

    def test_san_francisco_materialization_is_resumable_and_persona_only(self):
        index_url = "https://www.frbsf.org/news-and-media/speeches/mary-c-daly/"
        page_url = (
            "https://www.frbsf.org/news-and-media/speeches/mary-c-daly/"
            "2024/02/policy-speech/"
        )

        def fetcher(url):
            if url == index_url:
                return SAN_FRANCISCO_INDEX_HTML
            if "/page/" in url:
                return b"<html></html>"
            if url == page_url:
                return SAN_FRANCISCO_SPEECH_HTML
            raise AssertionError(url)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_path = root / "app.sqlite"
            app = sqlite3.connect(app_path)
            create_schema(app)
            app.execute(
                "INSERT INTO participant VALUES "
                "('mary-c-daly','Mary C. Daly','policymaker',NULL,NULL)"
            )
            app.commit()
            app.close()

            kwargs = {
                "years": [2024],
                "cache_root": root / "cache",
                "fetcher": fetcher,
            }
            first = materialize_san_francisco_daly_speeches(app_path, **kwargs)
            second = materialize_san_francisco_daly_speeches(app_path, **kwargs)
            app = sqlite3.connect(app_path)
            source = app.execute(
                "SELECT meeting_id, document_type, publication_at, usage_class "
                "FROM document_source"
            ).fetchone()
            communication = app.execute(
                "SELECT participant_id, title, text FROM public_communication"
            ).fetchone()
            app.close()

        self.assertEqual(first["ingested_document_count"], 1)
        self.assertEqual(second["new_cache_file_count"], 0)
        self.assertEqual(
            source,
            (None, "speech", "2024-02-16T23:59:59Z", "persona_evidence"),
        )
        self.assertEqual(communication[0], "mary-c-daly")
        self.assertIn("Policy must finish the job.", communication[2])

    def test_san_francisco_materialization_reports_slides_only_as_skipped(self):
        index_url = "https://www.frbsf.org/news-and-media/speeches/mary-c-daly/"
        page_url = (
            "https://www.frbsf.org/news-and-media/speeches/mary-c-daly/"
            "2021/05/wage-dynamics/"
        )
        index_html = f'<a href="{page_url}">Slides</a>'.encode()

        def fetcher(url):
            if url == index_url:
                return index_html
            if "/page/" in url:
                return b"<html></html>"
            if url == page_url:
                return SAN_FRANCISCO_SLIDES_HTML
            raise AssertionError(url)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_path = root / "app.sqlite"
            app = sqlite3.connect(app_path)
            create_schema(app)
            app.commit()
            app.close()

            report = materialize_san_francisco_daly_speeches(
                app_path,
                years=[2021],
                cache_root=root / "cache",
                fetcher=fetcher,
            )

        self.assertEqual(report["ingested_document_count"], 0)
        self.assertEqual(report["skipped_document_count"], 1)
        self.assertEqual(report["skipped_documents"][0]["reason"], "missing_speech_body")

    def test_boston_archive_keeps_unique_official_speech_pages(self):
        self.assertEqual(
            parse_boston_speech_links(
                BOSTON_INDEX_HTML,
                "https://www.bostonfed.org/news-and-events/speeches.aspx",
                years={2024},
            ),
            [
                "https://www.bostonfed.org/news-and-events/speeches/2024/"
                "policy-speech.aspx"
            ],
        )

    def test_boston_parser_requires_published_time_byline_and_full_text_pdf(self):
        parsed = parse_boston_communication(
            BOSTON_SPEECH_HTML,
            source_url=(
                "https://www.bostonfed.org/news-and-events/speeches/2024/"
                "policy-speech.aspx"
            ),
        )

        self.assertEqual(parsed["publication_date"], "2024-05-08")
        self.assertEqual(parsed["speaker_label"], "Susan M. Collins")
        self.assertEqual(parsed["title"], "Reflections on Monetary Policymaking")
        self.assertEqual(
            parsed["pdf_url"],
            "https://www.bostonfed.org/-/media/Documents/Speeches/PDF/"
            "collins/2024/20240508-text.pdf",
        )

    def test_boston_materialization_uses_full_text_pdf_and_is_resumable(self):
        index_url = "https://www.bostonfed.org/news-and-events/speeches.aspx"
        page_url = (
            "https://www.bostonfed.org/news-and-events/speeches/2024/"
            "policy-speech.aspx"
        )
        pdf_url = (
            "https://www.bostonfed.org/-/media/Documents/Speeches/PDF/"
            "collins/2024/20240508-text.pdf"
        )

        def fetcher(url):
            return {
                index_url: BOSTON_INDEX_HTML,
                page_url: BOSTON_SPEECH_HTML,
                pdf_url: b"fake-pdf-content",
            }[url]

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_path = root / "app.sqlite"
            app = sqlite3.connect(app_path)
            create_schema(app)
            app.execute(
                "INSERT INTO participant VALUES "
                "('susan-m-collins','Susan M. Collins','policymaker',NULL,NULL)"
            )
            app.commit()
            app.close()

            kwargs = {
                "years": [2024],
                "cache_root": root / "cache",
                "fetcher": fetcher,
                "text_extractor": lambda _: (
                    "Inflation and employment guide monetary policy."
                ),
            }
            first = materialize_boston_collins_speeches(app_path, **kwargs)
            second = materialize_boston_collins_speeches(app_path, **kwargs)
            app = sqlite3.connect(app_path)
            source = app.execute(
                "SELECT meeting_id, document_type, publication_at, usage_class "
                "FROM document_source"
            ).fetchone()
            communication = app.execute(
                "SELECT participant_id, title, text FROM public_communication"
            ).fetchone()
            app.close()

        self.assertEqual(first["ingested_document_count"], 1)
        self.assertEqual(second["new_cache_file_count"], 0)
        self.assertEqual(
            source,
            (None, "speech", "2024-05-08T23:59:59Z", "persona_evidence"),
        )
        self.assertEqual(
            communication,
            (
                "susan-m-collins",
                "Reflections on Monetary Policymaking",
                "Inflation and employment guide monetary policy.",
            ),
        )

    def test_boston_materialization_reports_non_full_text_pages_as_skipped(self):
        index_url = "https://www.bostonfed.org/news-and-events/speeches.aspx"
        page_url = (
            "https://www.bostonfed.org/news-and-events/speeches/2023/"
            "charts-only.aspx"
        )
        index_html = f'<a href="{page_url}">Charts</a>'.encode()

        def fetcher(url):
            return {
                index_url: index_html,
                page_url: BOSTON_CHART_HTML,
            }[url]

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_path = root / "app.sqlite"
            app = sqlite3.connect(app_path)
            create_schema(app)
            app.commit()
            app.close()

            report = materialize_boston_collins_speeches(
                app_path,
                years=[2023],
                cache_root=root / "cache",
                fetcher=fetcher,
            )
            app = sqlite3.connect(app_path)
            document_count = app.execute(
                "SELECT COUNT(*) FROM document_source"
            ).fetchone()[0]
            app.close()

        self.assertEqual(report["ingested_document_count"], 0)
        self.assertEqual(report["skipped_document_count"], 1)
        self.assertEqual(report["skipped_documents"][0]["reason"], "missing_full_text_pdf")
        self.assertEqual(document_count, 0)

    def test_chicago_profile_keeps_unique_official_goolsbee_speeches(self):
        self.assertEqual(
            parse_chicago_speech_links(
                CHICAGO_PROFILE_HTML,
                "https://www.chicagofed.org/people/g/austan-goolsbee",
                years={2024},
            ),
            [
                "https://www.chicagofed.org/publications/speeches/2024/"
                "feb-14-policy"
            ],
        )

    def test_chicago_parser_extracts_updated_date_title_and_body(self):
        parsed = parse_chicago_communication(CHICAGO_SPEECH_HTML)

        self.assertEqual(parsed["publication_date"], "2024-02-14")
        self.assertEqual(parsed["speaker_label"], "Austan D. Goolsbee")
        self.assertEqual(parsed["title"], "Economic Outlook")
        self.assertEqual(
            parsed["paragraphs"],
            [
                "Inflation is moving toward the Federal Reserve's target.",
                "Policy remains restrictive.",
            ],
        )

    def test_chicago_materialization_is_resumable_and_persona_only(self):
        index_url = "https://www.chicagofed.org/people/g/austan-goolsbee"
        speech_url = (
            "https://www.chicagofed.org/publications/speeches/2024/"
            "feb-14-policy"
        )

        def fetcher(url):
            return {
                index_url: CHICAGO_PROFILE_HTML,
                speech_url: CHICAGO_SPEECH_HTML,
            }[url]

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_path = root / "app.sqlite"
            app = sqlite3.connect(app_path)
            create_schema(app)
            app.execute(
                "INSERT INTO participant VALUES "
                "('austan-d-goolsbee','Austan D. Goolsbee','policymaker',NULL,NULL)"
            )
            app.commit()
            app.close()

            first = materialize_chicago_goolsbee_speeches(
                app_path,
                years=[2024],
                cache_root=root / "cache",
                fetcher=fetcher,
            )
            second = materialize_chicago_goolsbee_speeches(
                app_path,
                years=[2024],
                cache_root=root / "cache",
                fetcher=fetcher,
            )
            app = sqlite3.connect(app_path)
            source = app.execute(
                "SELECT meeting_id, document_type, publication_at, usage_class "
                "FROM document_source"
            ).fetchone()
            communication = app.execute(
                "SELECT participant_id, title FROM public_communication"
            ).fetchone()
            app.close()

        self.assertEqual(first["ingested_document_count"], 1)
        self.assertEqual(second["new_cache_file_count"], 0)
        self.assertEqual(
            source,
            (None, "speech", "2024-02-14T23:59:59Z", "persona_evidence"),
        )
        self.assertEqual(
            communication,
            ("austan-d-goolsbee", "Economic Outlook"),
        )

    def test_dallas_archive_keeps_unique_official_logan_speeches(self):
        self.assertEqual(
            parse_dallas_speech_links(
                DALLAS_INDEX_HTML,
                "https://www.dallasfed.org/news/speeches/logan",
                years={2024},
            ),
            [
                "https://www.dallasfed.org/news/speeches/logan/2024/lkl240405"
            ],
        )

    def test_dallas_parser_extracts_named_dated_body_only(self):
        parsed = parse_dallas_communication(DALLAS_SPEECH_HTML)

        self.assertEqual(parsed["publication_date"], "2024-04-05")
        self.assertEqual(parsed["speaker_label"], "President Lorie K. Logan")
        self.assertEqual(parsed["title"], "Economic Outlook and Monetary Policy")
        self.assertEqual(
            parsed["paragraphs"],
            [
                "Inflation remains above the Committee's objective.",
                "Policy should remain restrictive while employment is strong.",
            ],
        )

    def test_dallas_parser_supports_legacy_nested_date_span(self):
        parsed = parse_dallas_communication(
            DALLAS_OLD_SPEECH_HTML,
            source_url=(
                "https://www.dallasfed.org/news/speeches/logan/2022/lkl221004"
            ),
        )

        self.assertEqual(parsed["publication_date"], "2022-10-04")
        self.assertEqual(
            parsed["paragraphs"],
            ["Monetary policy supports maximum employment and price stability."],
        )

    def test_dallas_parser_uses_official_dated_slug_when_page_omits_date(self):
        parsed = parse_dallas_communication(
            DALLAS_DATELESS_SPEECH_HTML,
            source_url=(
                "https://www.dallasfed.org/news/speeches/logan/2022/lkl221110"
            ),
        )

        self.assertEqual(parsed["publication_date"], "2022-11-10")
        with self.assertRaisesRegex(ValueError, "publication date"):
            parse_dallas_communication(DALLAS_DATELESS_SPEECH_HTML)

    def test_dallas_materialization_is_resumable_and_persona_only(self):
        index_url = "https://www.dallasfed.org/news/speeches/logan"
        speech_url = (
            "https://www.dallasfed.org/news/speeches/logan/2024/lkl240405"
        )

        def fetcher(url):
            return {
                index_url: DALLAS_INDEX_HTML,
                speech_url: DALLAS_SPEECH_HTML,
            }[url]

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_path = root / "app.sqlite"
            app = sqlite3.connect(app_path)
            create_schema(app)
            app.execute(
                "INSERT INTO participant VALUES "
                "('lorie-k-logan','Lorie K. Logan','policymaker',NULL,NULL)"
            )
            app.commit()
            app.close()

            first = materialize_dallas_logan_speeches(
                app_path,
                years=[2024],
                cache_root=root / "cache",
                fetcher=fetcher,
            )
            second = materialize_dallas_logan_speeches(
                app_path,
                years=[2024],
                cache_root=root / "cache",
                fetcher=fetcher,
            )
            app = sqlite3.connect(app_path)
            source = app.execute(
                "SELECT meeting_id, document_type, publication_at, usage_class "
                "FROM document_source"
            ).fetchone()
            communication = app.execute(
                "SELECT participant_id, title FROM public_communication"
            ).fetchone()
            app.close()

        self.assertEqual(first["ingested_document_count"], 1)
        self.assertEqual(second["new_cache_file_count"], 0)
        self.assertEqual(
            source,
            (None, "speech", "2024-04-05T23:59:59Z", "persona_evidence"),
        )
        self.assertEqual(
            communication,
            ("lorie-k-logan", "Economic Outlook and Monetary Policy"),
        )


if __name__ == "__main__":
    unittest.main()
