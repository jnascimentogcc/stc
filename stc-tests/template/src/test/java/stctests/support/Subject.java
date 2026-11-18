package stctests.support;

/**
 * What the scenarios run against: the migrated {@code TARGET} (default) or the executable
 * {@code LEGACY}, used as an oracle. Chosen with the environment variable {@code SUBJECT}.
 */
public enum Subject {
  LEGACY("LEGACY_URL"),
  TARGET("TARGET_URL");

  private final String urlVariable;

  Subject(String urlVariable) {
    this.urlVariable = urlVariable;
  }

  public static Subject current() {
    return "legacy".equalsIgnoreCase(System.getenv("SUBJECT")) ? LEGACY : TARGET;
  }

  public String urlVariable() {
    return urlVariable;
  }
}
