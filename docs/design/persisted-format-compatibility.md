# Persisted-format compatibility

This matrix is the recovery contract for the 2026-08-11 runner update. The runner never
loads old bytes under new state semantics.

| Persisted artifact | Historical format | Current format | Compatibility action |
| --- | --- | --- | --- |
| Workflow manifest | `workflow_manifest.v1` | `workflow_manifest.v2` | v1 remains loadable for authored-workflow compatibility. New packs use v2. |
| Run manifest | `run_manifest.v1` | `run_manifest.v2` | v1 terminal evidence remains schema-readable. Live continuation fails closed because v1 has no frozen contract or attempt identity; preserve/archive it and start a v2 run. |
| Stage checkpoint | `stage_checkpoint.v1` | `stage_checkpoint.v2` | v1 remains schema-readable for classification and audit. Only v2 checkpoints participate in live v2 continuation. |
| Review bundle | `review_bundle.v1` | `review_bundle.v1` plus a hash-bound supervisor binding | Read unchanged. A bundle advances a stage only when its paths and hashes match the recorded source artifact. |
| Supervisor session | `supervisor_session.v1` | `supervisor_session.v2` | v1 remains schema-readable. Mutating supervisor commands fail closed; begin a v2 session for new work. |
| Final implementation bundle | `final_implementation_bundle.v1` | `final_implementation_bundle.v2` | The shared validator accepts frozen v1 GPT-5.5 evidence and current v2 GPT-5.6 evidence as distinct branches. New bundles are emitted as v2. |
| Generic final delivery | none | `final_delivery_bundle.v1` | New, additive contract for non-implementation work. |

Frozen v1 fixtures live in `automation/tests/fixtures/persisted_v1/`. Their GPT-5.5 model
identifiers and 24-hour cache field are historical evidence required to validate old bytes;
they are not active defaults. Current runtime, workflow, reviewer, session-v2, and final-v2
surfaces use GPT-5.6 and its current prompt-cache options.

No automatic in-place migration is provided. The safe recovery path is deliberately small:
retain the original evidence, create a new v2 run or supervisor session, and attach the old
terminal artifact as reviewed/reference evidence when the task authority permits it.
