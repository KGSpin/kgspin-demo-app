# Arm A — KGSpin-tuned graph

**Status (sprint 20):** placeholder. Arm A consumes the tuner's
`alpha_runner` output and re-emits it into
`benchmarks/schemas/graph-v0.json` shape. Wave 2 lands the adapter once
alpha_runner stabilizes.

## Expected inputs

- A tuner run directory under `kgspin-tuner/.runs/<run_id>/` containing
  the emitted graph JSON + chunk manifest.
- A `corpus_id` matching the manifest in `benchmarks/corpus/manifest.yaml`.

## Expected output

`graph-v0`-shaped JSON at
`benchmarks/reports/<timestamp>/arm-a/graph.json` with
`producer.name = "kgspin-tuner-alpha-runner"` and `producer.llm_alias = null`
(Arm A is deterministic).

## Non-scope for sprint 20

The harness runner refuses `--arm a` with a clear "Arm A not yet wired
— see benchmarks/arms/a/README.md" error. This is intentional: pulling
in tuner-side infrastructure before the tuner stabilizes would couple
the benchmark to a moving target.
