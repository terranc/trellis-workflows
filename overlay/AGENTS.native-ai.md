## Native AI Mode

This repo uses Trellis for **requirements and design alignment**. Implementation is Native AI:
the main session (the high-capability model) owns coding and verification.

- Planning artifacts are `prd.md` and `design.md`. Do not create `implement.md`,
  `implement.jsonl`, or `check.jsonl` unless the user explicitly asks for that task.
- After PRD + design are approved, activate the task with
  `python3 ./.trellis/scripts/task.py start <task-dir> --allow-empty-context`.
- Implement directly in the main session. Do not dispatch `trellis-implement`,
  `trellis-check`, or `trellis-research` sub-agents unless the user explicitly asks.
- Read `prd.md` -> `design.md` -> `.trellis/workflow.md` -> relevant `.trellis/spec/**/index.md`
  before coding.
- Verify with the project's own build / lint / test commands. `trellis-update-spec` and
  `/trellis:finish-work` are optional.
