import sqlite3
import tempfile
import unittest
from pathlib import Path

from decision_memory.app_db import create_schema as create_app_schema
from decision_memory.documents import ingest_local_document
from decision_memory.lag_evaluator import (
    _first_statement_flip,
    evaluate_observable_lag,
    persist_observable_lag_result,
)
from decision_memory.lag_spec import load_observable_lag_spec
from fred_vintage_db import (
    create_schema as create_source_schema,
    insert_meetings,
    insert_observations,
    insert_series,
)


SPEC_PATH = Path("metric_spec/inflation_transitory_v1.json")


class ObservableLagEvaluatorTests(unittest.TestCase):
    def test_support_phrase_coexisting_with_flip_phrase_is_not_a_false_flip(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = sqlite3.connect(":memory:")
            app.execute("PRAGMA foreign_keys = ON")
            create_app_schema(app)
            for index, (published, text) in enumerate(
                (
                    (
                        "2021-04-28T23:59:59Z",
                        "Inflation has risen, largely reflecting transitory factors.",
                    ),
                    (
                        "2021-06-16T23:59:59Z",
                        "Elevated levels of inflation continue to reflect transitory factors.",
                    ),
                ),
                start=1,
            ):
                path = root / f"statement-{index}.html"
                path.write_text(f"<p>{text}</p>", encoding="utf-8")
                ingest_local_document(
                    app,
                    path,
                    meeting_id=f"FOMC-2021-0{index + 3}-01",
                    document_type="statement",
                    publication_at=published,
                    usage_class="label_only",
                    source_url="https://www.federalreserve.gov/statement.htm",
                )

            result = _first_statement_flip(
                app,
                adopted_at="2021-04-01",
                contradiction_at="2021-05-12",
                support_patterns=[r"transitory factors"],
                flip_patterns=[r"elevated levels of inflation"],
            )
            app.close()

        self.assertIsNone(result)

    def test_first_release_phrase_flip_and_rate_response_are_deterministic(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = sqlite3.connect(":memory:")
            create_source_schema(source)
            insert_series(
                source,
                {
                    "id": "CPIAUCSL",
                    "title": "CPI",
                    "frequency": "Monthly",
                    "frequency_short": "M",
                    "units": "Index",
                },
                release_id=None,
                vintage_mode="ALFRED",
            )
            insert_observations(
                source,
                "CPIAUCSL",
                [
                    {
                        "date": "2020-04-01",
                        "value": "256.0",
                        "realtime_start": "2020-05-12",
                        "realtime_end": "9999-12-31",
                    },
                    {
                        "date": "2021-04-01",
                        "value": "266.8",
                        "realtime_start": "2021-05-12",
                        "realtime_end": "9999-12-31",
                    },
                ],
            )
            insert_meetings(
                source,
                [
                    {
                        "meeting_id": "FOMC-2021-12-14",
                        "meeting_start_date": "2021-12-14",
                        "meeting_end_date": "2021-12-15",
                        "information_cutoff_date_et": "2021-12-13",
                        "cutoff_policy": "previous_calendar_day",
                        "calendar_source_url": "https://www.federalreserve.gov/",
                    },
                    {
                        "meeting_id": "FOMC-2022-03-15",
                        "meeting_start_date": "2022-03-15",
                        "meeting_end_date": "2022-03-16",
                        "information_cutoff_date_et": "2022-03-14",
                        "cutoff_policy": "previous_calendar_day",
                        "calendar_source_url": "https://www.federalreserve.gov/",
                    },
                ],
            )

            app = sqlite3.connect(":memory:")
            app.execute("PRAGMA foreign_keys = ON")
            create_app_schema(app)
            documents = []
            for meeting_id, publication_at, text in (
                (
                    "FOMC-2021-04-27",
                    "2021-04-28T23:59:59Z",
                    "Inflation has risen, largely reflecting transitory factors.",
                ),
                (
                    "FOMC-2021-12-14",
                    "2021-12-15T23:59:59Z",
                    "Supply and demand imbalances have continued to contribute "
                    "to elevated levels of inflation.",
                ),
                (
                    "FOMC-2022-03-15",
                    "2022-03-16T23:59:59Z",
                    "The Committee decided to raise the target range.",
                ),
            ):
                path = root / f"{meeting_id}.html"
                path.write_text(f"<p>{text}</p>", encoding="utf-8")
                document_id = ingest_local_document(
                    app,
                    path,
                    meeting_id=meeting_id,
                    document_type="statement",
                    publication_at=publication_at,
                    usage_class="label_only",
                    source_url="https://www.federalreserve.gov/statement.htm",
                )
                documents.append((meeting_id, document_id))
            app.execute(
                """
                INSERT INTO meeting_outcome (
                    meeting_id, action_class, target_rate, target_lower,
                    target_upper, source_document_id, created_at
                ) VALUES ('FOMC-2021-12-14', 'HOLD', NULL, 0, 0.25, ?, 'now')
                """,
                (documents[1][1],),
            )
            app.execute(
                """
                INSERT INTO meeting_outcome (
                    meeting_id, action_class, target_rate, target_lower,
                    target_upper, source_document_id, created_at
                ) VALUES ('FOMC-2022-03-15', 'HIKE', NULL, 0.25, 0.50, ?, 'now')
                """,
                (documents[2][1],),
            )

            result = evaluate_observable_lag(
                source,
                app,
                load_observable_lag_spec(SPEC_PATH),
                as_of_date="2022-04-01",
            )
            first_persist = persist_observable_lag_result(
                app,
                result,
                load_observable_lag_spec(SPEC_PATH),
            )
            second_persist = persist_observable_lag_result(
                app,
                result,
                load_observable_lag_spec(SPEC_PATH),
            )
            event_count = app.execute(
                "SELECT COUNT(*) FROM assumption_event"
            ).fetchone()[0]

            source.close()
            app.close()

        self.assertEqual(result["first_contradiction_at"], "2021-05-12")
        self.assertAlmostEqual(result["contradiction_metric_value"], 4.21875)
        self.assertEqual(result["statement_flip_at"], "2021-12-15")
        self.assertEqual(result["policy_response_at"], "2022-03-16")
        self.assertEqual(result["recognition_lag_days"], 217)
        self.assertEqual(result["action_lag_days"], 91)
        self.assertEqual(result["response_lag_days"], 308)
        self.assertEqual(result["censoring_status"], "OBSERVED")
        self.assertEqual(first_persist, second_persist)
        self.assertEqual(first_persist["event_count"], 3)
        self.assertEqual(event_count, 3)


if __name__ == "__main__":
    unittest.main()
