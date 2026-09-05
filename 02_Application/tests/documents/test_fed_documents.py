import tempfile
import unittest
from pathlib import Path

from decision_memory.fed_documents import (
    cache_official_document,
    extract_html_paragraphs,
    extract_html_paragraph_line_blocks,
    is_official_federal_reserve_url,
)


class FederalReserveDocumentTests(unittest.TestCase):
    def test_official_url_allowlist_includes_all_reserve_banks_only(self):
        official_hosts = (
            "atlantafed.org",
            "bostonfed.org",
            "chicagofed.org",
            "clevelandfed.org",
            "dallasfed.org",
            "kansascityfed.org",
            "minneapolisfed.org",
            "newyorkfed.org",
            "philadelphiafed.org",
            "richmondfed.org",
            "frbsf.org",
            "stlouisfed.org",
        )
        for host in official_hosts:
            with self.subTest(host=host):
                self.assertTrue(
                    is_official_federal_reserve_url(f"https://www.{host}/speech")
                )
        self.assertFalse(
            is_official_federal_reserve_url(
                "https://www.dallasfed.org.example.com/speech"
            )
        )
        self.assertFalse(
            is_official_federal_reserve_url("http://www.dallasfed.org/speech")
        )

    def test_html_paragraph_extraction_preserves_vote_blocks(self):
        html = b"""
        <html><body>
          <p>Voting for this action: A. Person and B. Person.</p>
          <p>Voting against this action: None.</p>
        </body></html>
        """

        self.assertEqual(
            extract_html_paragraphs(html),
            [
                "Voting for this action: A. Person and B. Person.",
                "Voting against this action: None.",
            ],
        )

    def test_old_malformed_html_implicitly_closes_the_previous_paragraph(self):
        html = b"""
        <blockquote>
          <P><STRONG>Votes for this action:</STRONG> A. Person and B. Person.
          <P><STRONG>Votes against this action:</STRONG> None.</P>
        </blockquote>
        """

        self.assertEqual(
            extract_html_paragraphs(html),
            [
                "Votes for this action: A. Person and B. Person.",
                "Votes against this action: None.",
            ],
        )

    def test_line_blocks_preserve_attendance_breaks(self):
        html = b"""
        <p><strong>Attendance</strong><br>
        Jerome H. Powell, Chair<br>
        John C. Williams, Vice Chair</p>
        <p>Thomas I. Barkin and Mary C. Daly, Alternate Members of the Committee</p>
        """

        self.assertEqual(
            extract_html_paragraph_line_blocks(html),
            [
                [
                    "Attendance",
                    "Jerome H. Powell, Chair",
                    "John C. Williams, Vice Chair",
                ],
                [
                    "Thomas I. Barkin and Mary C. Daly, Alternate Members of the Committee"
                ],
            ],
        )

    def test_cache_refuses_non_federal_url_and_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "minutes.html"
            with self.assertRaisesRegex(ValueError, "Federal Reserve"):
                cache_official_document(
                    "https://example.com/minutes",
                    output,
                    fetcher=lambda _: b"content",
                )

            report = cache_official_document(
                "https://www.federalreserve.gov/monetarypolicy/example.htm",
                output,
                fetcher=lambda _: b"official content",
            )
            self.assertEqual(report["byte_length"], 16)
            self.assertEqual(len(report["sha256"]), 64)
            with self.assertRaises(FileExistsError):
                cache_official_document(
                    "https://www.federalreserve.gov/monetarypolicy/example.htm",
                    output,
                    fetcher=lambda _: b"changed",
                )


if __name__ == "__main__":
    unittest.main()
