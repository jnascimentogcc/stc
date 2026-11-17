package stctests.lint;

import io.cucumber.cucumberexpressions.ExpressionFactory;
import io.cucumber.cucumberexpressions.ParameterTypeRegistry;
import io.cucumber.gherkin.GherkinParser;
import io.cucumber.messages.types.Envelope;
import io.cucumber.messages.types.Feature;
import io.cucumber.messages.types.FeatureChild;
import io.cucumber.messages.types.Scenario;
import io.cucumber.messages.types.Step;
import io.cucumber.messages.types.StepKeywordType;
import io.cucumber.messages.types.Tag;
import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.regex.Pattern;
import stctests.support.Sidecar;

/**
 * The scenario linter: runs on a pull request, with no model and no STC, only the Gherkin parser,
 * the sidecars and the action catalog. The rules are the ones that can be checked mechanically:
 * traceability, the shape of a scenario, and that every step is an action that exists.
 */
public final class ScenarioLint {

  public record Violation(String scenario, String rule, String detail) {}

  private static final Pattern TRACE = Pattern.compile("@([a-z0-9][a-z0-9-]*/[A-Z]{2}-[0-9]{2,})");
  private static final Pattern CASE_ID = Pattern.compile("@TC-[0-9]{4}");
  private static final ExpressionFactory EXPRESSIONS =
      new ExpressionFactory(new ParameterTypeRegistry(Locale.ENGLISH));

  private ScenarioLint() {}

  /** Traceability and shape rules only. */
  public static List<Violation> check(String featureText, Sidecar sidecar) {
    return check(featureText, sidecar, null);
  }

  /**
   * All rules. With {@code actions} (the step expressions of catalog/actions.json) every step must
   * match one of them; with {@code null} that rule is skipped.
   */
  public static List<Violation> check(String featureText, Sidecar sidecar, List<String> actions) {
    List<Violation> found = new ArrayList<>();
    GherkinParser parser =
        GherkinParser.builder().includeSource(false).includePickles(false).build();
    List<Envelope> envelopes;
    try {
      envelopes =
          parser
              .parse("feature", new ByteArrayInputStream(featureText.getBytes(StandardCharsets.UTF_8)))
              .toList();
    } catch (IOException e) {
      throw new UncheckedIOException(e);
    }
    for (Envelope envelope : envelopes) {
      envelope.getParseError().ifPresent(e -> found.add(new Violation("-", "parse", e.getMessage())));
      Feature feature = envelope.getGherkinDocument().flatMap(d -> d.getFeature()).orElse(null);
      if (feature == null) {
        continue;
      }
      for (FeatureChild child : feature.getChildren()) {
        child.getBackground().ifPresent(b -> checkSteps("Background", b.getSteps(), actions, found));
        child.getScenario().ifPresent(s -> checkScenario(s, sidecar, actions, found));
      }
    }
    return found;
  }

  private static void checkScenario(
      Scenario scenario, Sidecar sidecar, List<String> actions, List<Violation> found) {
    String name = scenario.getName();
    List<String> traces = new ArrayList<>();
    boolean hasCaseId = false;
    for (Tag tag : scenario.getTags()) {
      var trace = TRACE.matcher(tag.getName());
      if (trace.matches()) {
        traces.add(trace.group(1));
      }
      hasCaseId |= CASE_ID.matcher(tag.getName()).matches();
    }
    if (traces.isEmpty()) {
      found.add(new Violation(name, "traceability", "no tag points to a requirement"));
    }
    for (String id : traces) {
      if (sidecar.retired().contains(id)) {
        found.add(new Violation(name, "retired-requirement", id + " no longer exists"));
      } else if (!sidecar.active().contains(id)) {
        found.add(new Violation(name, "unknown-requirement", id + " is not in the specification"));
      }
    }
    if (!hasCaseId) {
      found.add(new Violation(name, "case-id", "no @TC-nnnn tag"));
    }
    boolean asserts =
        scenario.getSteps().stream()
            .map(Step::getKeywordType)
            .anyMatch(type -> type.filter(StepKeywordType.OUTCOME::equals).isPresent());
    if (!asserts) {
      found.add(new Violation(name, "assertion", "no Then step"));
    }
    checkSteps(name, scenario.getSteps(), actions, found);
  }

  private static void checkSteps(
      String scenario, List<Step> steps, List<String> actions, List<Violation> found) {
    if (actions == null) {
      return;
    }
    for (Step step : steps) {
      boolean known =
          actions.stream().anyMatch(a -> EXPRESSIONS.createExpression(a).match(step.getText()) != null);
      if (!known) {
        found.add(new Violation(scenario, "unknown-step", "no action matches: " + step.getText()));
      }
    }
  }
}
