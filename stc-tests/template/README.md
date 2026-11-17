# stc-tests template

The repository a client runs in its own CI: Gherkin scenarios traced to the STC specification, a
linter that checks the traceability, and an acceptance suite (Cucumber-JVM, REST Assured, JUnit 5)
that runs against the migrated system. No model and no STC run here; everything is deterministic.

## Layout

| Path | What it is |
|---|---|
| `specs/<application>/` | The sidecars (`*.spec.json`) the consultancy hands over. The linter reads them all. |
| `src/test/resources/features/` | The scenarios. Each carries `@<feature>/<KIND>-<nn>` tags (the requirements it covers) and a `@TC-nnnn` case ID. |
| `src/test/java/stctests/steps/` | The actions: business-level steps bound to the boundary of the system. They know the interface mapping; the scenarios do not. |
| `src/test/java/stctests/lint/` | The scenario linter (traceability, retired requirements, case ID, at least one `Then`, every step is an action in the catalog). |
| `src/test/java/stctests/catalog/` | `@Action` (description and layer of a step definition) and the generator of the action catalog. |
| `catalog/actions.json` | The action catalog, generated from the step definitions: expression, parameters, description, layer. The agents bind steps only to actions listed here. |
| `src/test/java/stctests/support/` | `Sidecar` (validates against the contract and refuses what does not follow it) and `Target`. |
| `contract/` | A copy of `contracts/` of the STC repository: the JSON Schemas and fixtures. Kept equal by a test in `stc-tests/tool`. |

The scenarios, sidecar and steps shipped here are a sample (a keyed table search) so the template
runs as it is; replace them with the real ones.

## Run

Java 21 and Maven, from this folder:

```bash
mvn test                                  # contract + linter: no target needed
TARGET_URL=http://host:port mvn -Pacceptance test   # the scenarios against a real system
mvn test -Dcatalog.update=true            # regenerate catalog/actions.json after changing the steps
```

The default build checks the contract fixtures, fails if `catalog/actions.json` is out of date with the steps, and lints every scenario against `specs/` and the catalog. The
`acceptance` profile runs the Cucumber suite and needs `TARGET_URL`; the report is
`target/cucumber.html`.

## GitHub Actions

`.github/workflows/validate-scenarios.yml` runs the default build on pull requests that touch
scenarios or specifications. The other workflows of the proposal (`test-pr`, `test-nightly`,
`test-on-demand`, `differential`, `hidden-suite`) are not here yet.

## Not here yet

The interface mapping file,
Playwright (web is out of the pilot), the Allure report and the differential comparison. See
`docs/proposal-test-solution.md`.
