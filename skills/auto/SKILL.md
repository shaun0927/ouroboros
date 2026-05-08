---
name: auto
description: "Automatically converge from goal to A-grade Seed and execute it"
mcp_tool: ouroboros_auto
mcp_args:
  goal: "$goal"
  resume: "$resume"
  cwd: "$CWD"
  max_interview_rounds: "$max_interview_rounds"
  max_repair_rounds: "$max_repair_rounds"
  skip_run: "$skip_run"
---

# /ouroboros:auto

Run the full-quality auto pipeline from a single task description.

## Dispatch requirement

This skill must be executed by invoking MCP tool `ouroboros_auto`. Do not
manually inspect repositories, run shell commands, query GitHub, edit files, or
otherwise emulate the auto pipeline as a substitute.

If `ouroboros_auto` is unavailable, stop and report that the required MCP tool
is unavailable. A manual fallback is not an `ooo auto` run.

## Usage

```text
ooo auto "Build a local-first habit tracker CLI"
ooo auto --resume auto_abc123
ooo auto "Build a local-first habit tracker CLI" --skip-run
ooo auto "Build a local-first habit tracker CLI" --complete-product
/ouroboros:auto "Build a local-first habit tracker CLI"
```

## Behavior

1. Starts an auto session.
2. Runs bounded Socratic interview rounds with source-tagged auto answers.
3. Generates a Seed.
4. Reviews and repairs until A-grade or blocked.
5. Starts execution only after A-grade.
6. *(opt-in via `--complete-product`)* Hands off to the Ralph loop and waits
   for a terminal status: a QA-pass on the executed product completes the
   auto session; recognized failure modes (`iteration_timeout`,
   `wall_clock_exhausted`, `oscillation_detected`, `grade_regressing`,
   `max_generations reached`) block the auto session with the matching
   `stop_reason` in `last_error` so operators can resume after the cause is
   addressed.

The pipeline must not hang indefinitely: all loops are bounded and timeout failures return a resumable `auto_session_id`. Resume with `ooo auto --resume <auto_session_id>`. Use `--skip-run` to stop after the A-grade Seed. Use `--complete-product` to drive the full Interview → Seed → Run → Ralph → Product chain on a single `ooo auto` invocation; the chained Ralph loop honors the same wall-clock deadline as the parent auto session (`--timeout`). The CLI-only `--show-ledger` flag prints assumptions/non-goals; MCP skill responses already include the same ledger summary when available.
