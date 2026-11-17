# Spike: Java test stack against an STC sidecar

A short experiment to check the stack chosen in `docs/proposal-test-solution.md` (Cucumber-JVM,
REST Assured, JUnit 5; Java) before building the real template. It is not the template: the stub,
the REST contract and the sidecar fixture are invented for the spike.

## What it contains

| Piece | File |
|---|---|
| Six Gherkin scenarios of the keyed table search (the cases "safely derived" in the proposal), each tagged with the requirement it covers and a case ID | `src/test/resources/features/keyed-table-search.feature` |
| Business-level steps bound to HTTP with REST Assured | `src/test/java/spike/steps/SearchSteps.java` |
| A stub of the target (`POST /program-search-example/runs`), with a switch that plants a defect | `src/test/java/spike/support/StubServer.java` |
| The target adapter: `TARGET_URL` points to a real system, otherwise the stub starts | `src/test/java/spike/support/Target.java` |
| A sidecar reader that validates against `contracts/spec-sidecar.schema.json` and refuses what does not follow it | `src/test/java/spike/support/Sidecar.java` |
| A scenario linter on the Gherkin parser: traceability tag that exists, requirement not retired, case ID, at least one `Then` | `src/test/java/spike/lint/ScenarioLint.java` |
| The same fixtures STC checks in Python, checked here, plus the linter rules | `src/test/java/spike/ContractTest.java` |
| A sidecar for the example (`program-search-example`) | `src/test/resources/spec/program-search-example.spec.json` |

## Run it

Java 21 and Maven, from this folder (the tests read `../../contracts`):

```bash
mvn test                      # 6 scenarios + 18 contract and linter tests
mvn test -Dstub.bug=true      # the stub ignores the third id: one scenario must fail
TARGET_URL=http://host:port mvn test   # against a real target instead of the stub
```

The Cucumber report is written to `target/cucumber.html`.

## What it showed

- **The stack works end to end.** Cucumber 7.20.1, REST Assured 5.5.0 and JUnit 5.11.3 run the six
  scenarios and 18 other tests; a run takes a few seconds once the dependencies are downloaded.
- **Java reads the contract.** A JSON Schema 2020-12 validator (networknt 1.5.5) gives the same verdict
  as the Python tests on every fixture in `contracts/fixtures/` (the two valid sidecars pass, the
  eleven invalid ones fail), including the `if`/`then`/`else` and `not` rules. A sidecar that does not
  follow the contract is refused before it is used.
- **The linter needs no STC.** The traceability rules take about eighty lines on the official Gherkin
  parser and run on a pull request with only the sidecar file.
- **An injected defect is caught by the right scenario.** With `-Dstub.bug=true` exactly one scenario
  fails, "Three ids where one differs find no record". This is the first sample of the proposal's
  "injected defects detected" metric.
- **The steps are where the interface mapping lives.** The scenarios say what the program does, the
  step class says which field carries which prompt. A mapping change touches the steps, not the
  scenarios.

## What it did not show

- Playwright for Java (web is out of the pilot).
- Generation of the scenarios and steps by a model, or the binding to an action catalog.
- The real console-to-REST mapping of the COBOL program: the contract and the values of record 1 are
  assumptions of the stub.
- A run on GitHub Actions, the Allure report, the hidden suite, the differential comparison.
- Dependency versions were chosen for the spike and should be reviewed when the template is built.

## Environment note

`com.sun.net.httpserver` (and anything built on it or on Jetty or Netty, such as WireMock) opens an
NIO selector, which fails in some restricted environments ("Unable to establish loopback
connection"). The stub therefore uses a blocking `ServerSocket`. A normal developer machine or a
GitHub-hosted runner is not affected; it matters only where the tests run in a sandbox.
