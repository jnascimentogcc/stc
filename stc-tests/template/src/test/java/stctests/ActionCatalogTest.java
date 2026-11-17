package stctests;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.List;
import org.junit.jupiter.api.Test;
import stctests.catalog.ActionCatalog;
import stctests.lint.ScenarioLint;
import stctests.support.Sidecar;

/**
 * The catalog is generated from the step definitions and committed; this fails when it is stale.
 * Regenerate it with {@code mvn test -Dcatalog.update=true}.
 */
class ActionCatalogTest {

  @Test
  void theCommittedCatalogMatchesTheCode() throws IOException {
    String expected = ActionCatalog.toJson(ActionCatalog.scan());
    if (Boolean.getBoolean("catalog.update")) {
      ActionCatalog.write(ActionCatalog.FILE, expected);
    }
    assertTrue(Files.exists(ActionCatalog.FILE), "catalog/actions.json is missing");
    String committed =
        Files.readString(ActionCatalog.FILE, StandardCharsets.UTF_8).replace("\r\n", "\n");
    assertEquals(
        expected,
        committed,
        "catalog/actions.json is out of date: run mvn test -Dcatalog.update=true");
  }

  @Test
  void everyActionIsDescribed() {
    List<ActionCatalog.Entry> entries = ActionCatalog.scan();
    assertFalse(entries.isEmpty());
    entries.forEach(e -> assertFalse(e.description().isBlank(), e.implementation()));
  }

  private static List<String> rules(String steps) {
    String feature =
        "Feature: F\n\n  @program-search-example/FR-04 @TC-0001\n  Scenario: S\n" + steps;
    Sidecar sidecar =
        Sidecar.load(java.nio.file.Path.of("src/test/resources/spec/program-search-example.spec.json"));
    List<String> actions = ActionCatalog.expressions(ActionCatalog.FILE);
    return ScenarioLint.check(feature, sidecar, actions).stream()
        .map(ScenarioLint.Violation::rule)
        .toList();
  }

  @Test
  void aStepThatIsNotInTheCatalogIsRejected() {
    assertEquals(
        List.of(),
        rules("    When I search the keyed table for the id \"0002\"\n    Then the keyed result is \"x\"\n"));
    assertEquals(
        List.of("unknown-step"),
        rules("    When I click the red button\n    Then the keyed result is \"x\"\n"));
  }
}
