import sqlite3
import unittest
from pathlib import Path

from decision_memory.app_db import create_schema
from decision_memory.votes import (
    audit_vote_manifest,
    parse_vote_paragraphs,
    persist_vote_rounds,
)


ROOT = Path(__file__).resolve().parents[2]


class VoteParserTests(unittest.TestCase):
    def test_statement_voting_grammar_is_supported(self):
        rounds = parse_vote_paragraphs(
            [
                "Voting for the FOMC monetary policy action were: Alan Greenspan, Chairman; Timothy F. Geithner, Vice Chairman; and Janet L. Yellen.",
                "Voting against this action was Jeffrey M. Lacker, who preferred a different policy action.",
            ],
            round_meeting_ids=["FOMC-2006-01-31"],
        )

        self.assertEqual(
            rounds[0]["for_names"],
            ["Alan Greenspan", "Timothy F. Geithner", "Janet L. Yellen"],
        )
        self.assertEqual(rounds[0]["against_names"], ["Jeffrey M. Lacker"])

    def test_historical_statement_against_wording_is_supported(self):
        cases = [
            (
                "Voting against was Jeffrey M. Lacker, who preferred an increase.",
                ["Jeffrey M. Lacker"],
            ),
            (
                "Voting against the policy action was Thomas M. Hoenig, who opposed the guidance.",
                ["Thomas M. Hoenig"],
            ),
            (
                "Voting against the policy was Thomas M. Hoenig, who opposed reinvestment.",
                ["Thomas M. Hoenig"],
            ),
        ]

        for against_paragraph, expected_names in cases:
            with self.subTest(against_paragraph=against_paragraph):
                rounds = parse_vote_paragraphs(
                    [
                        "Voting for the FOMC monetary policy action were: Ben S. Bernanke and Janet L. Yellen.",
                        against_paragraph,
                    ],
                    round_meeting_ids=["FOMC-2010-09-21"],
                )

                self.assertEqual(rounds[0]["against_names"], expected_names)
                self.assertTrue(rounds[0]["against_explicit"])

    def test_multiple_dissenters_with_individual_rationales_are_all_parsed(self):
        rounds = parse_vote_paragraphs(
            [
                "Voting for the FOMC monetary policy action were: Janet L. Yellen and William C. Dudley.",
                "Voting against the action were Richard W. Fisher, who preferred earlier normalization; Narayana Kocherlakota, who cited low inflation; and Charles I. Plosser, who opposed time-dependent guidance.",
            ],
            round_meeting_ids=["FOMC-2014-12-16"],
        )

        self.assertEqual(
            rounds[0]["against_names"],
            ["Richard W. Fisher", "Narayana Kocherlakota", "Charles I. Plosser"],
        )

    def test_single_dissenter_rationale_does_not_parse_institution_as_voter(self):
        rounds = parse_vote_paragraphs(
            [
                "Voting for this action: Ben S. Bernanke and William C. Dudley.",
                "Voting against the policy was Thomas M. Hoenig, who judged that the economy was recovering. In addition, he did not believe that keeping constant the size of the Federal Reserve's holdings was required.",
            ],
            round_meeting_ids=["FOMC-2010-08-10"],
        )

        self.assertEqual(rounds[0]["against_names"], ["Thomas M. Hoenig"])

    def test_comma_separated_dissenters_with_individual_rationales_are_all_parsed(self):
        rounds = parse_vote_paragraphs(
            [
                "Voting for this action: Ben S. Bernanke and William C. Dudley.",
                "Voting against the action was James Bullard, who wanted a stronger inflation signal, and Esther L. George, who was concerned about accommodation.",
            ],
            round_meeting_ids=["FOMC-2013-06-18"],
        )

        self.assertEqual(
            rounds[0]["against_names"],
            ["James Bullard", "Esther L. George"],
        )

    def test_rationale_followed_by_and_without_punctuation_preserves_later_voters(self):
        rounds = parse_vote_paragraphs(
            [
                "Voting for this action: Jerome H. Powell and John C. Williams.",
                "Voting against this action: Stephen I. Miran, who preferred a lower target range at this meeting and Beth M. Hammack, Neel Kashkari, and Lorie K. Logan, who opposed the easing bias.",
            ],
            round_meeting_ids=["FOMC-2026-04-28"],
        )

        self.assertEqual(
            rounds[0]["against_names"],
            ["Stephen I. Miran", "Beth M. Hammack", "Neel Kashkari", "Lorie K. Logan"],
        )

    def test_unrecognized_against_clause_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "Unrecognized voting-against clause"):
            parse_vote_paragraphs(
                [
                    "Voting for this action: Jerome H. Powell and John C. Williams.",
                    "Voting against on balance: Michelle W. Bowman.",
                ],
                round_meeting_ids=["FOMC-2024-09-17"],
            )

    def test_same_paragraph_for_and_against_clauses_do_not_parse_trailing_title(self):
        rounds = parse_vote_paragraphs(
            [
                "Voting for the FOMC monetary policy action were: Ben S. Bernanke, Chairman; William C. Dudley, Vice Chairman; Sarah Bloom Raskin; and Janet L. Yellen. Voting against the action was Jeffrey M. Lacker, who opposed continuation of the maturity extension program. Statement Regarding Continuation of the Maturity Extension Program"
            ],
            round_meeting_ids=["FOMC-2012-06-19"],
        )

        self.assertEqual(
            rounds[0]["for_names"],
            [
                "Ben S. Bernanke",
                "William C. Dudley",
                "Sarah Bloom Raskin",
                "Janet L. Yellen",
            ],
        )
        self.assertEqual(rounds[0]["against_names"], ["Jeffrey M. Lacker"])

    def test_legacy_honorific_lists_are_normalized_to_roster_lookup_keys(self):
        rounds = parse_vote_paragraphs(
            [
                "Votes for this action: Messrs. Bernanke, Geithner, Kohn, Kroszner, and Mishkin, Ms. Pianalto, Messrs. Plosser, Stern, and Warsh.",
                "Votes against this action: Mr. Fisher.",
            ],
            round_meeting_ids=["FOMC-2008-01-29"],
        )

        self.assertEqual(
            rounds[0]["for_names"],
            [
                "Bernanke",
                "Geithner",
                "Kohn",
                "Kroszner",
                "Mishkin",
                "Pianalto",
                "Plosser",
                "Stern",
                "Warsh",
            ],
        )
        self.assertEqual(rounds[0]["against_names"], ["Fisher"])

    def test_legacy_plural_mses_honorific_is_not_a_first_name(self):
        rounds = parse_vote_paragraphs(
            [
                "Votes for this action: Messrs. Bernanke and Geithner, Mses. Pianalto and Yellen.",
                "Votes against this action: None.",
            ],
            round_meeting_ids=["FOMC-2006-01-31"],
        )

        self.assertEqual(
            rounds[0]["for_names"],
            ["Bernanke", "Geithner", "Pianalto", "Yellen"],
        )

    def test_against_rationales_do_not_become_voter_names(self):
        rounds = parse_vote_paragraphs(
            [
                "Voting for this action: Jerome H. Powell and John C. Williams.",
                "Voting against this action: Stephen I. Miran, who preferred to lower the target range by 1/4 percentage point; and Beth M. Hammack, Neel Kashkari, and Lorie K. Logan, who supported maintaining the range but opposed the easing bias.",
            ],
            round_meeting_ids=["FOMC-2026-04-28"],
        )

        self.assertEqual(
            rounds[0]["against_names"],
            ["Stephen I. Miran", "Beth M. Hammack", "Neel Kashkari", "Lorie K. Logan"],
        )

    def test_none_followed_by_same_paragraph_board_text_is_still_unanimous(self):
        rounds = parse_vote_paragraphs(
            [
                "Voting for this action: Jerome H. Powell and John C. Williams.",
                "Voting against this action: None. Consistent with the Committee's decision, the Board of Governors of the Federal Reserve System voted unanimously.",
            ],
            round_meeting_ids=["FOMC-2024-11-06"],
        )

        self.assertEqual(rounds[0]["against_names"], [])

    def test_voter_roles_are_not_parsed_as_people(self):
        rounds = parse_vote_paragraphs(
            [
                "Voting for this action: Jerome H. Powell, Chair; John C. Williams, Vice Chair; Michelle W. Bowman.",
                "Voting against this action: None.",
            ],
            round_meeting_ids=["FOMC-2022-01-25"],
        )

        self.assertEqual(
            rounds[0]["for_names"],
            ["Jerome H. Powell", "John C. Williams", "Michelle W. Bowman"],
        )

    def test_two_vote_blocks_require_and_preserve_explicit_meeting_mapping(self):
        paragraphs = [
            "Votes for this action: Jerome H. Powell, John C. Williams, Michelle W. Bowman, and Lael Brainard.",
            "Votes against this action: Loretta J. Mester.",
            "President Mester preferred a smaller reduction.",
            "Voting for this action: Jerome H. Powell, John C. Williams, Michelle W. Bowman, Lael Brainard, and Loretta J. Mester.",
        ]

        with self.assertRaisesRegex(ValueError, "meeting mapping"):
            parse_vote_paragraphs(paragraphs)

        rounds = parse_vote_paragraphs(
            paragraphs,
            round_meeting_ids=["FOMC-2020-03-15", "FOMC-2020-03-02"],
        )

        self.assertEqual(len(rounds), 2)
        self.assertEqual(rounds[0]["meeting_id"], "FOMC-2020-03-15")
        self.assertEqual(rounds[0]["against_names"], ["Loretta J. Mester"])
        self.assertEqual(rounds[1]["meeting_id"], "FOMC-2020-03-02")
        self.assertEqual(rounds[1]["against_names"], [])
        self.assertFalse(rounds[1]["against_explicit"])

    def test_none_mapping_explicitly_excludes_a_non_policy_round(self):
        rounds = parse_vote_paragraphs(
            [
                "Votes for this action: Messrs. Bernanke and Geithner.",
                "Votes against this action: None.",
                "Votes for this action: Messrs. Bernanke and Kohn.",
                "Votes against this action: Mr. Poole.",
            ],
            round_meeting_ids=["FOMC-2008-01-29", None],
        )

        self.assertEqual(len(rounds), 1)
        self.assertEqual(rounds[0]["source_round"], 1)
        self.assertEqual(rounds[0]["meeting_id"], "FOMC-2008-01-29")

    def test_training_minutes_audit_preregisters_all_multi_vote_mappings(self):
        report = audit_vote_manifest(
            ROOT / "document_manifests" / "training_2006_2020.json"
        )

        self.assertEqual(report["errors"], [])
        self.assertEqual(report["multi_vote_document_count"], 4)
        self.assertEqual(report["multi_vote_round_count"], 8)
        self.assertEqual(report["mapped_vote_meeting_count"], 5)

    def test_persisted_votes_require_rostered_voters_and_balance(self):
        connection = sqlite3.connect(":memory:")
        connection.execute("PRAGMA foreign_keys = ON")
        create_schema(connection)
        connection.execute(
            """
            INSERT INTO document_source (
                document_id, meeting_id, document_type, publication_at,
                usage_class, source_locator, content_hash, created_at
            ) VALUES ('doc-1', 'FOMC-2020-03-15', 'minutes',
                      '2020-04-08T18:00:00Z', 'label_only', '{}',
                      'hash-1', '2026-08-27T00:00:00Z')
            """
        )
        for participant_id, name, is_chair in [
            ("jerome-h-powell", "Jerome H. Powell", 1),
            ("john-c-williams", "John C. Williams", 0),
            ("loretta-j-mester", "Loretta J. Mester", 0),
        ]:
            connection.execute(
                "INSERT INTO participant VALUES (?, ?, 'member', NULL, NULL)",
                (participant_id, name),
            )
            connection.execute(
                "INSERT INTO meeting_participant VALUES (?, ?, 'member', 1, ?)",
                ("FOMC-2020-03-15", participant_id, is_chair),
            )
        rounds = parse_vote_paragraphs(
            [
                "Voting for this action: Jerome H. Powell and John C. Williams.",
                "Voting against this action: Loretta J. Mester.",
            ],
            round_meeting_ids=["FOMC-2020-03-15"],
        )
        try:
            inserted = persist_vote_rounds(connection, rounds, evidence_id="doc-1")
            repeated = persist_vote_rounds(connection, rounds, evidence_id="doc-1")
            totals = connection.execute(
                """
                SELECT voter_choice, COUNT(*)
                FROM participant_vote
                GROUP BY voter_choice
                ORDER BY voter_choice
                """
            ).fetchall()
        finally:
            connection.close()

        self.assertEqual(inserted, 3)
        self.assertEqual(repeated, 3)
        self.assertEqual(totals, [("AGAINST", 1), ("FOR", 2)])

    def test_unrostered_voter_fails_without_partial_vote_rows(self):
        connection = sqlite3.connect(":memory:")
        connection.execute("PRAGMA foreign_keys = ON")
        create_schema(connection)
        connection.execute(
            """
            INSERT INTO document_source (
                document_id, meeting_id, document_type, publication_at,
                usage_class, source_locator, content_hash, created_at
            ) VALUES ('doc-1', 'FOMC-2020-03-15', 'minutes',
                      '2020-04-08T18:00:00Z', 'label_only', '{}',
                      'hash-1', '2026-08-27T00:00:00Z')
            """
        )
        rounds = parse_vote_paragraphs(
            [
                "Voting for this action: Unknown Person.",
                "Voting against this action: None.",
            ],
            round_meeting_ids=["FOMC-2020-03-15"],
        )
        try:
            with self.assertRaisesRegex(ValueError, "rostered voter"):
                persist_vote_rounds(connection, rounds, evidence_id="doc-1")
            count = connection.execute(
                "SELECT COUNT(*) FROM participant_vote"
            ).fetchone()[0]
        finally:
            connection.close()

        self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
