import sqlite3
import tempfile
import unittest
from pathlib import Path

from decision_memory.app_db import create_schema
from decision_memory.public_communications import (
    materialize_board_speeches,
    parse_board_communication,
    parse_board_speech_links,
    policy_relevance_score,
    persist_public_communication,
    resolve_participant_id,
)
from decision_memory.simulation_variants import _persona_evidence


SAMPLE_HTML = b"""
<html><body>
  <p>Navigation noise</p>
  <div id="article">
    <div class="heading">
      <p class="article__time">December 18, 2020</p>
      <h3 class="title"><em>Economic Outlook and Monetary Policy</em></h3>
      <p class="speaker">Governor Example Person</p>
      <p class="location">At an official event</p>
    </div>
    <div class="col-xs-12 col-sm-8 col-md-8">
      <p><strong>Inflation Risks</strong><br />Inflation remains below the Committee's longer-run objective.</p>
      <p>Policy should remain patient while employment recovers.</p>
      <hr>
      <p>1. Real pages place footnotes after a horizontal rule.</p>
      <div class="footnotes"><p>1. Footnote noise. Return to text</p></div>
    </div>
  </div>
</body></html>
"""


class PublicCommunicationTests(unittest.TestCase):
    def test_board_archive_materialization_is_resumable_and_uses_persona_class(self):
        index_url = "https://www.federalreserve.gov/newsevents/2020-speeches.htm"
        speech_url = (
            "https://www.federalreserve.gov/newsevents/speech/"
            "example20201218a.htm"
        )
        index = (
            f'<a href="{speech_url}">Speech</a>'
        ).encode("utf-8")

        def fetcher(url):
            return {index_url: index, speech_url: SAMPLE_HTML}[url]

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_path = root / "app.sqlite"
            app = sqlite3.connect(app_path)
            create_schema(app)
            app.execute(
                "INSERT INTO participant VALUES ('example-person','Example Person','policymaker',NULL,NULL)"
            )
            app.commit()
            app.close()

            first = materialize_board_speeches(
                app_path,
                years=[2020],
                cache_root=root / "cache",
                fetcher=fetcher,
            )
            second = materialize_board_speeches(
                app_path,
                years=[2020],
                cache_root=root / "cache",
                fetcher=fetcher,
            )
            app = sqlite3.connect(app_path)
            source = app.execute(
                """
                SELECT meeting_id, document_type, publication_at, usage_class
                FROM document_source
                """
            ).fetchone()
            count = app.execute(
                "SELECT COUNT(*) FROM public_communication"
            ).fetchone()[0]
            app.close()

        self.assertEqual(first["ingested_document_count"], 1)
        self.assertEqual(second["ingested_document_count"], 1)
        self.assertEqual(count, 1)
        self.assertEqual(
            source,
            (None, "speech", "2020-12-18T23:59:59Z", "persona_evidence"),
        )

    def test_archive_parser_keeps_unique_official_speech_links_only(self):
        index = b"""
        <a href="/newsevents/speech/brainard20201218a.htm">Speech</a>
        <a href="/newsevents/speech/brainard20201218a.htm">Duplicate</a>
        <a href="/newsevents/pressreleases/other.htm">Other</a>
        <a href="https://example.com/newsevents/speech/fake.htm">External</a>
        """

        self.assertEqual(
            parse_board_speech_links(
                index,
                "https://www.federalreserve.gov/newsevents/2020-speeches.htm",
            ),
            [
                "https://www.federalreserve.gov/newsevents/speech/brainard20201218a.htm"
            ],
        )

    def test_speaker_resolution_and_policy_relevance_are_deterministic(self):
        app = sqlite3.connect(":memory:")
        create_schema(app)
        app.execute(
            "INSERT INTO participant VALUES ('lael-brainard','Lael Brainard','policymaker',NULL,NULL)"
        )

        self.assertEqual(
            resolve_participant_id(app, "Governor Lael Brainard"),
            "lael-brainard",
        )
        self.assertGreater(
            policy_relevance_score(
                "Economic Outlook and Monetary Policy",
                "Inflation and employment guide the federal funds rate.",
            ),
            0,
        )
        self.assertEqual(
            policy_relevance_score(
                "Community Bank Cybersecurity",
                "Operational resilience and password controls.",
            ),
            0,
        )
        app.close()

    def test_board_parser_extracts_named_dated_body_without_navigation_or_footnotes(self):
        parsed = parse_board_communication(SAMPLE_HTML)

        self.assertEqual(parsed["publication_date"], "2020-12-18")
        self.assertEqual(parsed["speaker_label"], "Governor Example Person")
        self.assertEqual(parsed["title"], "Economic Outlook and Monetary Policy")
        self.assertEqual(
            parsed["paragraphs"],
            [
                "Inflation Risks Inflation remains below the Committee's longer-run objective.",
                "Policy should remain patient while employment recovers.",
            ],
        )

    def test_public_persona_evidence_is_hash_checked_and_cutoff_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            app_path = Path(directory) / "app.sqlite"
            app = sqlite3.connect(app_path)
            app.execute("PRAGMA foreign_keys = ON")
            create_schema(app)
            app.execute(
                "INSERT INTO participant VALUES ('example-person','Example Person','policymaker',NULL,NULL)"
            )
            for document_id, publication_at, text in (
                (
                    "doc-visible",
                    "2020-12-18T23:59:59Z",
                    "Inflation remains below target. Policy should remain patient.",
                ),
                (
                    "doc-irrelevant",
                    "2022-02-01T23:59:59Z",
                    "Operational resilience and password controls.",
                ),
                (
                    "doc-future",
                    "2022-04-01T23:59:59Z",
                    "This later communication must not be visible.",
                ),
            ):
                app.execute(
                    """
                    INSERT INTO document_source VALUES (
                        ?, NULL, 'speech', ?, 'persona_evidence', '{}',
                        ?, '2026-08-31T00:00:00Z'
                    )
                    """,
                    (document_id, publication_at, f"file-hash-{document_id}"),
                )
                persist_public_communication(
                    app,
                    document_id=document_id,
                    participant_id="example-person",
                    title=(
                        "Community Bank Cybersecurity"
                        if document_id == "doc-irrelevant"
                        else "Economic Outlook"
                    ),
                    text=text,
                )
            app.commit()
            app.close()

            evidence = _persona_evidence(
                app_path,
                meeting_id="FOMC-2022-03-15",
                cutoff_date="2022-03-14",
                participant_ids=["example-person"],
            )

        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0]["evidence_kind"], "public_communication")
        self.assertEqual(evidence[0]["document_id"], "doc-visible")
        self.assertEqual(
            evidence[0]["text"],
            "Inflation remains below target. Policy should remain patient.",
        )

    def test_irrelevant_recent_pages_do_not_hide_older_policy_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            app_path = Path(directory) / "app.sqlite"
            app = sqlite3.connect(app_path)
            app.execute("PRAGMA foreign_keys = ON")
            create_schema(app)
            app.execute(
                "INSERT INTO participant VALUES "
                "('example-person','Example Person','policymaker',NULL,NULL)"
            )
            documents = [
                (
                    "doc-policy",
                    "2023-01-01T23:59:59Z",
                    "Economic Outlook and Monetary Policy",
                    "Inflation and employment guide the federal funds rate.",
                )
            ] + [
                (
                    f"doc-event-{ordinal}",
                    f"2023-0{ordinal + 1}-01T23:59:59Z",
                    "Community Event",
                    f"Welcome to community event number {ordinal}.",
                )
                for ordinal in range(1, 6)
            ]
            for document_id, publication_at, title, text in documents:
                app.execute(
                    """
                    INSERT INTO document_source VALUES (
                        ?, NULL, 'speech', ?, 'persona_evidence', '{}',
                        ?, '2026-08-31T00:00:00Z'
                    )
                    """,
                    (document_id, publication_at, f"file-hash-{document_id}"),
                )
                persist_public_communication(
                    app,
                    document_id=document_id,
                    participant_id="example-person",
                    title=title,
                    text=text,
                )
            app.commit()
            app.close()

            evidence = _persona_evidence(
                app_path,
                meeting_id="FOMC-2023-09-19",
                cutoff_date="2023-09-18",
                participant_ids=["example-person"],
            )

        self.assertEqual(
            [item["document_id"] for item in evidence],
            ["doc-policy"],
        )


if __name__ == "__main__":
    unittest.main()
