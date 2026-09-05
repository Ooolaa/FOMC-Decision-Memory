PRAGMA foreign_keys = ON;

BEGIN TRANSACTION;

CREATE TABLE database_metadata (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE economic_release (
    release_id   INTEGER PRIMARY KEY,
    release_name TEXT NOT NULL,
    source_name  TEXT NOT NULL,
    source_url   TEXT
);

CREATE TABLE release_event (
    release_event_id INTEGER PRIMARY KEY,
    release_id       INTEGER NOT NULL,
    release_date     TEXT NOT NULL,
    scheduled_at_utc TEXT,
    time_precision   TEXT NOT NULL
        CHECK (time_precision IN ('date', 'minute')),
    fetched_at_utc   TEXT NOT NULL,
    FOREIGN KEY (release_id) REFERENCES economic_release(release_id),
    UNIQUE (release_id, release_date)
);

CREATE TABLE economic_series (
    series_id           TEXT PRIMARY KEY,
    title               TEXT NOT NULL,
    release_id          INTEGER,
    frequency           TEXT NOT NULL,
    units               TEXT NOT NULL,
    seasonal_adjustment TEXT,
    observation_start   TEXT,
    observation_end     TEXT,
    FOREIGN KEY (release_id) REFERENCES economic_release(release_id)
);

CREATE TABLE observation_vintage (
    series_id          TEXT NOT NULL,
    observation_date   TEXT NOT NULL,
    realtime_start     TEXT NOT NULL,
    realtime_end       TEXT NOT NULL DEFAULT '9999-12-31',
    value_raw          TEXT NOT NULL,
    value_num          REAL,
    is_initial_release INTEGER NOT NULL
        CHECK (is_initial_release IN (0, 1)),
    release_event_id   INTEGER,
    fetched_at_utc     TEXT NOT NULL,
    PRIMARY KEY (series_id, observation_date, realtime_start),
    FOREIGN KEY (series_id) REFERENCES economic_series(series_id),
    FOREIGN KEY (release_event_id) REFERENCES release_event(release_event_id),
    CHECK (realtime_end >= realtime_start)
);

CREATE TABLE fomc_meeting (
    meeting_id                 TEXT PRIMARY KEY,
    meeting_start_at_utc       TEXT NOT NULL,
    meeting_end_at_utc         TEXT NOT NULL,
    information_cutoff_at_utc  TEXT NOT NULL,
    information_cutoff_date_et TEXT NOT NULL,
    policy_action              TEXT
        CHECK (policy_action IN ('cut', 'hold', 'hike')),
    target_rate_lower          REAL,
    target_rate_upper          REAL
);

CREATE TABLE meeting_snapshot_value (
    meeting_id        TEXT NOT NULL,
    series_id         TEXT NOT NULL,
    observation_date TEXT NOT NULL,
    realtime_start   TEXT NOT NULL,
    selection_policy TEXT NOT NULL,
    created_at_utc   TEXT NOT NULL,
    PRIMARY KEY (meeting_id, series_id, observation_date),
    FOREIGN KEY (meeting_id) REFERENCES fomc_meeting(meeting_id),
    FOREIGN KEY (series_id, observation_date, realtime_start)
        REFERENCES observation_vintage(series_id, observation_date, realtime_start)
);

CREATE INDEX ix_vintage_asof
    ON observation_vintage(series_id, realtime_start, observation_date);

CREATE INDEX ix_release_event_date
    ON release_event(release_date);

INSERT INTO database_metadata (key, value) VALUES
    ('dataset_status', 'DEMO_ONLY'),
    ('value_warning', 'Illustrative values; not downloaded from FRED'),
    ('schema_purpose', 'Point-in-time FOMC decision simulation');

INSERT INTO economic_release
    (release_id, release_name, source_name, source_url)
VALUES
    (1, 'Employment Situation', 'DEMO / BLS-like', NULL),
    (2, 'Personal Income and Outlays', 'DEMO / BEA-like', NULL),
    (3, 'Gross Domestic Product', 'DEMO / BEA-like', NULL);

INSERT INTO release_event
    (release_event_id, release_id, release_date, scheduled_at_utc,
     time_precision, fetched_at_utc)
VALUES
    (101, 1, '2024-01-05', '2024-01-05T13:30:00Z', 'minute', '2026-08-21T00:00:00Z'),
    (102, 1, '2024-02-02', '2024-02-02T13:30:00Z', 'minute', '2026-08-21T00:00:00Z'),
    (103, 1, '2024-03-08', '2024-03-08T13:30:00Z', 'minute', '2026-08-21T00:00:00Z'),
    (201, 2, '2024-01-26', '2024-01-26T13:30:00Z', 'minute', '2026-08-21T00:00:00Z'),
    (202, 2, '2024-02-29', '2024-02-29T13:30:00Z', 'minute', '2026-08-21T00:00:00Z'),
    (301, 3, '2024-01-25', '2024-01-25T13:30:00Z', 'minute', '2026-08-21T00:00:00Z'),
    (302, 3, '2024-02-28', '2024-02-28T13:30:00Z', 'minute', '2026-08-21T00:00:00Z'),
    (303, 3, '2024-03-28', '2024-03-28T12:30:00Z', 'minute', '2026-08-21T00:00:00Z');

INSERT INTO economic_series
    (series_id, title, release_id, frequency, units, seasonal_adjustment,
     observation_start, observation_end)
VALUES
    ('DEMO_UNRATE', 'Unemployment Rate', 1, 'Monthly', 'Percent', 'Seasonally Adjusted', '2023-12-01', '2024-02-01'),
    ('DEMO_PAYEMS', 'All Employees, Total Nonfarm', 1, 'Monthly', 'Thousands of Persons', 'Seasonally Adjusted', '2023-12-01', '2024-01-01'),
    ('DEMO_CORE_PCE', 'Core PCE Price Index, 12-Month Change', 2, 'Monthly', 'Percent', 'Seasonally Adjusted', '2023-12-01', '2024-01-01'),
    ('DEMO_REAL_GDP', 'Real GDP, Annualized Quarterly Change', 3, 'Quarterly', 'Percent', 'Seasonally Adjusted Annual Rate', '2023-10-01', '2023-10-01');

INSERT INTO observation_vintage
    (series_id, observation_date, realtime_start, realtime_end, value_raw,
     value_num, is_initial_release, release_event_id, fetched_at_utc)
VALUES
    ('DEMO_UNRATE', '2023-12-01', '2024-01-05', '9999-12-31', '3.7', 3.7, 1, 101, '2026-08-21T00:00:00Z'),
    ('DEMO_UNRATE', '2024-02-01', '2024-03-08', '9999-12-31', '3.9', 3.9, 1, 103, '2026-08-21T00:00:00Z'),

    ('DEMO_PAYEMS', '2023-12-01', '2024-01-05', '2024-02-01', '216', 216, 1, 101, '2026-08-21T00:00:00Z'),
    ('DEMO_PAYEMS', '2023-12-01', '2024-02-02', '9999-12-31', '229', 229, 0, 102, '2026-08-21T00:00:00Z'),
    ('DEMO_PAYEMS', '2024-01-01', '2024-02-02', '2024-03-07', '353', 353, 1, 102, '2026-08-21T00:00:00Z'),
    ('DEMO_PAYEMS', '2024-01-01', '2024-03-08', '9999-12-31', '290', 290, 0, 103, '2026-08-21T00:00:00Z'),

    ('DEMO_CORE_PCE', '2023-12-01', '2024-01-26', '9999-12-31', '2.9', 2.9, 1, 201, '2026-08-21T00:00:00Z'),
    ('DEMO_CORE_PCE', '2024-01-01', '2024-02-29', '9999-12-31', '2.8', 2.8, 1, 202, '2026-08-21T00:00:00Z'),

    ('DEMO_REAL_GDP', '2023-10-01', '2024-01-25', '2024-02-27', '3.3', 3.3, 1, 301, '2026-08-21T00:00:00Z'),
    ('DEMO_REAL_GDP', '2023-10-01', '2024-02-28', '2024-03-27', '3.2', 3.2, 0, 302, '2026-08-21T00:00:00Z'),
    ('DEMO_REAL_GDP', '2023-10-01', '2024-03-28', '9999-12-31', '3.4', 3.4, 0, 303, '2026-08-21T00:00:00Z');

INSERT INTO fomc_meeting
    (meeting_id, meeting_start_at_utc, meeting_end_at_utc,
     information_cutoff_at_utc, information_cutoff_date_et,
     policy_action, target_rate_lower, target_rate_upper)
VALUES
    ('FOMC-2024-01', '2024-01-30T14:00:00Z', '2024-01-31T19:00:00Z',
     '2024-01-30T13:59:59Z', '2024-01-30', 'hold', 5.25, 5.50),
    ('FOMC-2024-03', '2024-03-19T13:00:00Z', '2024-03-20T18:00:00Z',
     '2024-03-19T12:59:59Z', '2024-03-19', 'hold', 5.25, 5.50);

INSERT INTO meeting_snapshot_value
    (meeting_id, series_id, observation_date, realtime_start,
     selection_policy, created_at_utc)
VALUES
    ('FOMC-2024-01', 'DEMO_UNRATE', '2023-12-01', '2024-01-05', 'latest_visible_before_cutoff', '2026-08-21T00:00:00Z'),
    ('FOMC-2024-01', 'DEMO_PAYEMS', '2023-12-01', '2024-01-05', 'latest_visible_before_cutoff', '2026-08-21T00:00:00Z'),
    ('FOMC-2024-01', 'DEMO_CORE_PCE', '2023-12-01', '2024-01-26', 'latest_visible_before_cutoff', '2026-08-21T00:00:00Z'),
    ('FOMC-2024-01', 'DEMO_REAL_GDP', '2023-10-01', '2024-01-25', 'latest_visible_before_cutoff', '2026-08-21T00:00:00Z'),

    ('FOMC-2024-03', 'DEMO_UNRATE', '2024-02-01', '2024-03-08', 'latest_visible_before_cutoff', '2026-08-21T00:00:00Z'),
    ('FOMC-2024-03', 'DEMO_PAYEMS', '2024-01-01', '2024-03-08', 'latest_visible_before_cutoff', '2026-08-21T00:00:00Z'),
    ('FOMC-2024-03', 'DEMO_CORE_PCE', '2024-01-01', '2024-02-29', 'latest_visible_before_cutoff', '2026-08-21T00:00:00Z'),
    ('FOMC-2024-03', 'DEMO_REAL_GDP', '2023-10-01', '2024-02-28', 'latest_visible_before_cutoff', '2026-08-21T00:00:00Z');

CREATE VIEW v_meeting_information_set AS
SELECT
    m.meeting_id,
    m.information_cutoff_at_utc,
    s.series_id,
    s.title,
    s.frequency,
    s.units,
    snap.observation_date,
    o.value_num,
    o.value_raw,
    o.realtime_start AS visible_version_date,
    o.is_initial_release,
    snap.selection_policy
FROM meeting_snapshot_value AS snap
JOIN fomc_meeting AS m
  ON m.meeting_id = snap.meeting_id
JOIN observation_vintage AS o
  ON o.series_id = snap.series_id
 AND o.observation_date = snap.observation_date
 AND o.realtime_start = snap.realtime_start
JOIN economic_series AS s
  ON s.series_id = snap.series_id;

COMMIT;
