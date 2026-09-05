# R5 transcript segmentation v2 execution plan

## Goal

Correct inline speaker handoffs in the 50-document FOMC transcript corpus so
participant-level debate evidence can support the known-roster FOR/AGAINST
prediction target without attributing the next speaker's remarks to the Chair.

## Verified assumptions

- The formal app database remains read-only at SHA-256
  `83EF409125BEA85F9463F2C1BF2C7A9ACCB46414D6E7268262B53C93A1C9732C`.
- All 50 official transcript PDFs are present in the local cache.
- The frozen v1 transcript manifest contains 50 documents and records
  `pypdf_speaker_regex_v1`.
- Human review identified real inline handoffs in multiple meetings; this is a
  deterministic segmentation defect, not a roster-prediction task.

## Dependency map

`cached transcript PDFs` -> `split_speaker_segments` ->
`transcript_segment participant_id` -> `DecisionTrace participant positions` ->
`persona evidence for per-voter simulation`.

## Milestones

1. Add a failing regression case for an inline Chair-to-member handoff.
2. Make the minimum speaker-boundary change and pass transcript unit tests.
3. Add a fail-closed, manifest-driven resegmentation path that writes only to a
   new candidate database and a new immutable manifest.
4. Rebuild all 50 documents, compare segment/resolution counts, and verify the
   eight human-review failure segments resolve to the intended participants.
5. Run the full test suite, SQLite integrity/FK checks, and confirm the formal
   database hash is unchanged.

Each milestone is independently verifiable. Rollback is deletion of the new
candidate database and v2 manifest; the v1 manifest, vote-core candidate, and
formal database are not modified.

## Risks

- Treating every uppercase title as a speaker boundary could create false
  splits. Corpus-level speaker-label and resolution comparisons are required.
- The first corpus probe exposed a period-consuming name pattern; retain that
  v2 probe as diagnostic evidence and use the corrected v3 lineage for gates.
- New segment IDs intentionally invalidate old DecisionTrace evidence IDs.
  Existing reviewed artifacts remain immutable and must be regenerated against
  the new lineage before any import.

## Out of scope

- No model/API calls or DecisionTrace regeneration in this milestone.
- No promotion into `fomc_simulation.sqlite`.
- No claim that roster identity itself is predicted; roster remains known input.
