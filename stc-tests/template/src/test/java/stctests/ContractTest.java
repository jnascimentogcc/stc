package stctests;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.stream.Stream;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.MethodSource;
import stctests.lint.ScenarioLint;
import stctests.lint.ScenarioLint.Violation;
import stctests.support.Sidecar;

/**
 * A Java consumer of the contract: the same fixtures STC checks in Python must give the same
 * verdict here, and the linter can rely on the sidecar.
 */
class ContractTest {

  private static final Path SIDECAR = Path.of("src/test/resources/spec/program-search-example.spec.json");

  static Stream<Path> fixtures(String folder) throws IOException {
    try (Stream<Path> all = Files.list(Sidecar.contractsDir().resolve("fixtures").resolve(folder))) {
      return all.filter(p -> p.getFileName().toString().endsWith(".spec.json")).sorted().toList().stream();
    }
  }

  static Stream<Path> valid() throws IOException {
    return fixtures("valid");
  }

  static Stream<Path> invalid() throws IOException {
    return fixtures("invalid");
  }

  @ParameterizedTest(name = "{0}")
  @MethodSource("valid")
  void validFixturesPass(Path fixture) {
    assertEquals(0, Sidecar.violations(Sidecar.read(fixture)).size(), fixture.toString());
  }

  @ParameterizedTest(name = "{0}")
  @MethodSource("invalid")
  void invalidFixturesFail(Path fixture) {
    assertFalse(Sidecar.violations(Sidecar.read(fixture)).isEmpty(), fixture + " should not validate");
  }

  @Test
  void aSidecarThatBreaksTheContractIsRefused() {
    Path bad = Sidecar.contractsDir().resolve("fixtures/invalid/unknown-major.spec.json");
    assertThrows(IllegalArgumentException.class, () -> Sidecar.load(bad));
  }

  // --- the linter -----------------------------------------------------------------------------

  private static String feature(String tags, String steps) {
    return "Feature: F\n\n  " + tags + "\n  Scenario: S\n" + steps;
  }

  @Test
  void theRealScenariosFollowTheRules() throws IOException {
    String text = Files.readString(Path.of("src/test/resources/features/keyed-table-search.feature"), StandardCharsets.UTF_8);
    assertEquals(List.of(), ScenarioLint.check(text, Sidecar.load(SIDECAR)));
  }

  private static List<String> rules(String featureText) {
    return ScenarioLint.check(featureText, Sidecar.load(SIDECAR)).stream().map(Violation::rule).toList();
  }

  @Test
  void aScenarioNeedsATraceabilityTagThatExists() {
    String steps = "    When I search\n    Then it is found\n";
    assertEquals(List.of("traceability"), rules(feature("@TC-0001", steps)));
    assertEquals(
        List.of("unknown-requirement"),
        rules(feature("@program-search-example/FR-99 @TC-0001", steps)));
  }

  @Test
  void aRetiredRequirementCannotBeTested() {
    String steps = "    When I search\n    Then it is found\n";
    assertEquals(
        List.of("retired-requirement"),
        rules(feature("@program-search-example/FR-06 @TC-0001", steps)));
  }

  @Test
  void aScenarioNeedsACaseIdAndAnAssertion() {
    String tags = "@program-search-example/FR-04";
    assertTrue(rules(feature(tags, "    When I search\n    Then it is found\n")).contains("case-id"));
    assertTrue(rules(feature(tags + " @TC-0001", "    When I search\n")).contains("assertion"));
  }
}
