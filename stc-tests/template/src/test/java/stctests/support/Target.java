package stctests.support;

/**
 * The target adapter: where the migrated system is. Set {@code TARGET_URL} to its base URL; the
 * acceptance suite refuses to run without it.
 */
public final class Target {

  private Target() {}

  public static String baseUrl() {
    String configured = System.getenv("TARGET_URL");
    if (configured == null || configured.isBlank()) {
      throw new IllegalStateException("TARGET_URL is not set: point it to the system under test");
    }
    return configured;
  }
}
