# LLM Callpoints and Expected Return Formats

This document records the active LLM callsites in the agent pipeline, what each one is broadly responsible for, and what kind of output the rest of the system expects.

It is intentionally higher-level than the code. The goal is to clarify:

- which stage uses an LLM
- what job that LLM call is doing
- whether the output is structured JSON or narrative text
- whether the system treats failures as recoverable or fatal

## Active callsites

| Node | Call site | Role |
|---|---|---|
| `parse_intent` | Planning-stage intent and report-plan generation | Turn the raw query into intent plus downstream planning context |
| `fundamental_analysis` | Fundamental analysis generation | Produce structured company, quality, valuation, and risk analysis |
| `macro_analysis` | Macro regime analysis generation | Produce a structured macro view with drivers and risks |
| `market_sentiment` | Sentiment and price-action analysis generation | Produce a structured market narrative and sentiment view |
| `llm_judge` | Retry decision gate | Decide whether another research pass is worth doing before scenarios/reporting |
| `scenario_scoring` | Baseline scenario generation | Produce the initial scenario set and raw probability weights |
| `scenario_debate` | Scenario calibration workflow | Stress-test and recalibrate the baseline scenarios |
| `report_finalize` | Narrative section writing | Write the final report sections in markdown |

## `parse_intent`

This is the planning-stage LLM call. It turns a raw user query into structured intent plus a lightweight plan for the rest of the pipeline.

Expected output type:

- structured JSON
- intent-level fields
- planning context for downstream research and reporting

Behavior:

- parsed into typed planning objects
- fallback-friendly rather than fail-fast
- if the call is unusable, the system falls back to a safe default interpretation instead of stopping the run

## `fundamental_analysis`

This call generates the structured fundamental view: business quality, valuation framing, core claims, and major risks.

Expected output type:

- structured JSON
- validated as typed fundamental-analysis output
- expected to include evidence-linked reasoning, not just prose

Behavior:

- merged with normalized research metrics before validation
- treated as a core analysis dependency
- fail-fast on unusable output

## `macro_analysis`

This call produces the macro layer of the report: regime view, main drivers, and major risks.

Expected output type:

- structured JSON
- validated as typed macro-analysis output

Behavior:

- consumed as a normal downstream analysis artifact
- fail-fast on unusable output

## `market_sentiment`

This call produces the market-facing layer of the report: news/sentiment read, market narrative, and sentiment risks.

Expected output type:

- structured JSON
- validated as typed sentiment-analysis output

Behavior:

- used alongside fundamental and macro analysis
- fail-fast on unusable output

## `llm_judge`

This node is the retry decision point. It uses small structured judge calls to decide whether the pipeline should do one more evidence pass before moving on.

Expected output type:

- small structured JSON judge result
- retry / no-retry decision
- optional targeted retry question

Behavior:

- may run up to two judge passes
- combines structural checks with LLM-based adequacy/conflict judgment
- best-effort rather than fail-fast
- if the judge is unavailable, the pipeline continues instead of aborting

## `scenario_scoring`

This call generates the baseline scenario set for the rest of the pipeline.

Expected output type:

- structured JSON
- 3 to 5 scenarios
- raw probability weights plus scenario descriptions and support

Behavior:

- post-processed in Python into typed `Scenario` objects
- probabilities are normalized after generation
- fail-fast on unusable output

## `scenario_debate`

This stage is not a single call but a small workflow. It first gathers competing scenario arguments, then asks a follow-up judge/arbitrator call to recalibrate probabilities.

Expected output type:

- structured JSON
- calibrated scenario output
- summary of adjustments and confidence

Behavior:

- multi-call pattern rather than single-shot generation
- Python-side validation constrains how far probabilities can move
- fallback-friendly: if debate output is unusable, the system falls back to baseline scenarios instead of failing the run

## `report_finalize`

This is the narrative-writing stage. It is the only active callsite that primarily expects markdown text rather than structured JSON.

Expected output type:

- markdown section text
- readable final-report prose
- suitable for section-by-section assembly into the export report

Behavior:

- section failures degrade locally rather than killing the full report
- validation and quality checks happen mostly in Python after generation
- hard-fails only on missing core evidence, not on ordinary text-generation failure

## Shared client behavior

Most callsites run through JSON mode:

- `complete(...)` and `call_with_retry(...)`
- provider-side JSON response enforcement where possible
- client-side JSON parsing and validation

Final report writing uses text mode:

- `complete_text(...)`
- no JSON enforcement

Retry and failover behavior are shared across the client:

- retryable transport / timeout / invalid-JSON failures can stay on the current model for retries
- fatal failures can skip directly to the next model
- the client only gives up after all configured models are exhausted

## Failure-handling summary

Fallback-friendly callsites:

- `parse_intent`
- `llm_judge`
- `scenario_debate`
- section-level narrative rendering inside `report_finalize`

Fail-fast callsites:

- `fundamental_analysis`
- `macro_analysis`
- `market_sentiment`
- `scenario_scoring`

## Streaming telemetry

Every LLM call emits lifecycle events into `LLMCallCollector`:

- `calling`
- `success`
- `retry`
- `failed`

`OrchestratorAgent.run_stream(...)` interleaves those events with graph execution updates, so the frontend can show near-real-time `llm_call` status while the workflow is still running.
