package stctests;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import org.junit.jupiter.api.Test;
import stctests.catalog.ActionCatalog;
import stctests.lint.ScenarioLint;
import stctests.lint.ScenarioLint.Violation;
import stctests.support.Sidecar;

/**
 * The gate of the {@code validate-scenarios} workflow: every scenario under {@code
 * src/test/resources/features} must trace to a requirement of the sidecars in {@code specs/}, carry
 * a case ID and assert something. No model, no STC: only the files in this repository.
 */
class ScenarioLintTest {

  private static final Path FEATURES = Path.of("src/test/resources/features");
  private static final Path SPECS = Path.of("specs");

  @Test
  void everyScenarioFollowsTheRules() throws IOException {
    Sidecar sidecar = Sidecar.loadAll(SPECS);
    assertFalse(sidecar.active().isEmpty(), "no specification found in " + SPECS);
    List<String> actions = ActionCatalog.expressions(ActionCatalog.FILE);
    List<String> problems = new ArrayList<>();
    try (var files = Files.walk(FEATURES)) {
      for (Path file : files.filter(p -> p.toString().endsWith(".feature")).sorted().toList()) {
        String text = Files.readString(file, StandardCharsets.UTF_8);
        for (Violation v : ScenarioLint.check(text, sidecar, actions)) {
          problems.add(file + ": [" + v.rule() + "] " + v.scenario() + ": " + v.detail());
        }
      }
    }
    assertEquals(List.of(), problems);
  }
}
