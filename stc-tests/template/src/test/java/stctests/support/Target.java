package stctests.support;

/**
 * The adapter to the system under test: the base URL of the migrated target ({@code TARGET_URL}),
 * or, with {@code SUBJECT=legacy}, of the executable legacy ({@code LEGACY_URL}).
 */
public final class Target {

  private Target() {}

  public static String baseUrl() {
    Subject subject = Subject.current();
    String configured = System.getenv(subject.urlVariable());
    if (configured == null || configured.isBlank()) {
      throw new IllegalStateException(
          subject.urlVariable() + " is not set: point it to the system under test");
    }
    return configured;
  }
}
