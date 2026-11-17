package stctests.catalog;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import io.cucumber.java.en.Given;
import io.cucumber.java.en.Then;
import io.cucumber.java.en.When;
import java.io.IOException;
import java.lang.reflect.Method;
import java.lang.reflect.Parameter;
import java.net.URISyntaxException;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * The action catalog: every step definition of the library with its expression, parameters and
 * description, generated from the code so it cannot drift. The agents bind scenarios only to
 * actions that exist here, and the linter rejects a step that does not match one.
 */
public final class ActionCatalog {

  public static final String SCHEMA = "stc.action-catalog/1";
  public static final String STEPS_PACKAGE = "stctests.steps";
  public static final Path FILE = Path.of("catalog/actions.json");

  public record Param(String name, String type) {}

  public record Entry(
      String keyword,
      String expression,
      String description,
      String layer,
      String implementation,
      List<Param> parameters) {}

  private static final ObjectMapper JSON =
      new ObjectMapper().enable(SerializationFeature.INDENT_OUTPUT);

  private ActionCatalog() {}

  /** Scans the step definition classes of {@link #STEPS_PACKAGE}. */
  public static List<Entry> scan() {
    List<Entry> entries = new ArrayList<>();
    for (Class<?> type : stepClasses(STEPS_PACKAGE)) {
      for (Method method : type.getDeclaredMethods()) {
        String[] step = step(method);
        if (step == null) {
          continue;
        }
        Action action = method.getAnnotation(Action.class);
        if (action == null) {
          throw new IllegalStateException(
              "step definition without @Action: " + type.getSimpleName() + "." + method.getName());
        }
        List<Param> params = new ArrayList<>();
        for (Parameter p : method.getParameters()) {
          params.add(new Param(p.getName(), p.getType().getSimpleName()));
        }
        entries.add(
            new Entry(
                step[0],
                step[1],
                action.description(),
                action.layer().name().toLowerCase(),
                type.getSimpleName() + "." + method.getName(),
                params));
      }
    }
    entries.sort(Comparator.comparing(Entry::expression).thenComparing(Entry::keyword));
    return entries;
  }

  private static String[] step(Method method) {
    if (method.isAnnotationPresent(Given.class)) {
      return new String[] {"Given", method.getAnnotation(Given.class).value()};
    }
    if (method.isAnnotationPresent(When.class)) {
      return new String[] {"When", method.getAnnotation(When.class).value()};
    }
    if (method.isAnnotationPresent(Then.class)) {
      return new String[] {"Then", method.getAnnotation(Then.class).value()};
    }
    return null;
  }

  private static List<Class<?>> stepClasses(String pkg) {
    try {
      URL url = Thread.currentThread().getContextClassLoader().getResource(pkg.replace('.', '/'));
      if (url == null) {
        return List.of();
      }
      Path dir = Path.of(url.toURI());
      List<Class<?>> classes = new ArrayList<>();
      try (var files = Files.walk(dir)) {
        for (Path file : files.filter(p -> p.toString().endsWith(".class")).sorted().toList()) {
          String relative = dir.relativize(file).toString().replace('\\', '/');
          if (relative.contains("$")) {
            continue;
          }
          String name = pkg + "." + relative.substring(0, relative.length() - ".class".length());
          classes.add(Class.forName(name.replace('/', '.')));
        }
      }
      return classes;
    } catch (URISyntaxException | IOException | ClassNotFoundException e) {
      throw new IllegalStateException("cannot scan " + pkg, e);
    }
  }

  /** The catalog as the text of catalog/actions.json (LF line endings). */
  public static String toJson(List<Entry> entries) {
    Map<String, Object> document = new LinkedHashMap<>();
    document.put("schema", SCHEMA);
    document.put("actions", entries);
    try {
      return JSON.writeValueAsString(document).replace("\r\n", "\n") + "\n";
    } catch (IOException e) {
      throw new IllegalStateException(e);
    }
  }

  /** The step expressions of a catalog file, for the linter. */
  public static List<String> expressions(Path file) {
    try {
      var document = JSON.readTree(Files.readAllBytes(file));
      if (!SCHEMA.equals(document.path("schema").asText())) {
        throw new IllegalArgumentException(file + " is not a " + SCHEMA + " document");
      }
      List<String> expressions = new ArrayList<>();
      document.get("actions").forEach(a -> expressions.add(a.get("expression").asText()));
      return expressions;
    } catch (IOException e) {
      throw new IllegalStateException("cannot read " + file, e);
    }
  }

  public static void write(Path file, String text) throws IOException {
    Files.createDirectories(file.toAbsolutePath().getParent());
    Files.writeString(file, text, StandardCharsets.UTF_8);
  }
}
