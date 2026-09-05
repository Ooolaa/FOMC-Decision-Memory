import hashlib
import json
import sqlite3
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CANDIDATE = ROOT / "fomc_simulation.transcript_segmentation_v3_candidate.sqlite"
MANIFEST = (
    ROOT
    / "document_manifests"
    / "transcripts_2006_2020_sample50_v3_inline_handoff_no_period.json"
)
PREFLIGHT = (
    ROOT
    / "artifacts"
    / "codex_subscription"
    / "decision_trace_50_v5_segmentation_v3"
    / "bundle_preflight.json"
)


TARGETS = (
    (
        "FOMC-2009-09-22",
        "jeffrey-m-lacker",
        "Thank you, Mr. Chairman. No w that the economy has stabilized",
    ),
    (
        "FOMC-2009-09-22",
        "william-c-dudley",
        "Tha nk you, Mr. Chairman. First, I just want to talk",
    ),
    (
        "FOMC-2011-08-09",
        "narayana-kocherlakota",
        "Thank you, Mr. Chairman. O f necessity more than preference",
    ),
    (
        "FOMC-2012-06-19",
        "jeffrey-m-lacker",
        "Thank you, Mr. Chairman. I do not believe that the current situation",
    ),
    (
        "FOMC-2012-06-19",
        "janet-l-yellen",
        "Thank you, Mr. Chairman. I support a lternative B. With respect",
    ),
    (
        "FOMC-2015-07-28",
        "lael-brainard",
        "Thank you, Madam Chair. I can support alternative B. The labor market",
    ),
    (
        "FOMC-2017-06-13",
        "neel-kashkari",
        "Thank you, Madam Chair. I support a lternative A at this meeting",
    ),
    (
        "FOMC-2018-01-30",
        "neel-kashkari",
        "With regard to the policy decision , I support alternative A",
    ),
    (
        "FOMC-2018-01-30",
        "jerome-h-powell",
        "Thank you, Madam Chair. I will support alternative B",
    ),
    (
        "FOMC-2019-07-30",
        "eric-s-rosengren",
        "Thank you, Mr. Chair. I support alternative C for this meeting",
    ),
    (
        "FOMC-2019-07-30",
        "charles-l-evans",
        "Thank you, Mr. Chair. You know, this is a very tough meeting",
    ),
    (
        "FOMC-2019-07-30",
        "esther-l-george",
        "Thank you, Mr. Chairman. In March of this year",
    ),
    (
        "FOMC-2020-04-28",
        "esther-l-george",
        "Thank you, Mr. Chairman. Today’s policy decision seems straightforward",
    ),
    (
        "FOMC-2020-04-28",
        "lael-brainard",
        "Thank you, Mr. Chair. The COVID-19 shock is of historic proportions",
    ),
)


class TranscriptSegmentationV3AuditTests(unittest.TestCase):
    def test_frozen_candidate_manifest_and_preflight_match(self):
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        preflight = json.loads(PREFLIGHT.read_text(encoding="utf-8"))
        candidate_hash = hashlib.sha256(CANDIDATE.read_bytes()).hexdigest()

        self.assertEqual(
            manifest["extraction_version"],
            "pypdf_speaker_regex_v3_inline_handoff_no_period",
        )
        self.assertEqual(manifest["app_database_sha256"], candidate_hash)
        self.assertEqual(preflight["app_database_sha256"], candidate_hash)
        self.assertEqual(preflight["case_count"], 50)
        self.assertEqual(preflight["platform_api_calls"], 0)
        self.assertEqual(preflight["platform_api_cost_usd"], 0.0)

    def test_corpus_integrity_counts_and_speaker_label_guard(self):
        app = sqlite3.connect(f"file:{CANDIDATE.as_posix()}?mode=ro", uri=True)
        try:
            self.assertEqual(app.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(app.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(
                app.execute("SELECT COUNT(*) FROM transcript_segment").fetchone()[0],
                14010,
            )
            self.assertEqual(
                app.execute(
                    "SELECT COUNT(*) FROM transcript_segment "
                    "WHERE participant_id IS NOT NULL"
                ).fetchone()[0],
                11147,
            )
            self.assertEqual(
                app.execute(
                    "SELECT COUNT(*) FROM transcript_segment "
                    "WHERE (length(speaker_label) - "
                    "length(replace(speaker_label, '.', ''))) > 1"
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                app.execute(
                    "SELECT COUNT(*) FROM transcript_segment WHERE length(text) = 0"
                ).fetchone()[0],
                0,
            )
        finally:
            app.close()

    def test_human_review_attribution_failures_are_correct_in_downstream_bundles(self):
        app = sqlite3.connect(f"file:{CANDIDATE.as_posix()}?mode=ro", uri=True)
        try:
            for meeting_id, participant_id, prefix in TARGETS:
                rows = app.execute(
                    """
                    SELECT content_hash, participant_id
                    FROM transcript_segment
                    WHERE meeting_id = ? AND text LIKE ?
                    """,
                    (meeting_id, prefix + "%"),
                ).fetchall()
                self.assertEqual(
                    rows,
                    [(rows[0][0], participant_id)] if rows else [],
                    msg=f"Unexpected attribution for {meeting_id}/{participant_id}",
                )
                bundle = json.loads(
                    (
                        PREFLIGHT.parent
                        / "bundles"
                        / f"{meeting_id}.json"
                    ).read_text(encoding="utf-8")
                )
                transcript = next(
                    item
                    for item in bundle["documents"]
                    if item["document_type"] == "transcript"
                )
                bundled = [
                    item
                    for item in transcript["segments"]
                    if item["content_hash"] == rows[0][0]
                ]
                self.assertEqual(len(bundled), 1)
                self.assertEqual(bundled[0]["participant_id"], participant_id)
        finally:
            app.close()


if __name__ == "__main__":
    unittest.main()
