package spike.support;

/**
 * The target adapter: where the migrated system is. {@code TARGET_URL} points to a real one; with
 * none the spike starts its stub. {@code -Dstub.bug=true} plants a defect in the stub.
 */
public final class Target {

  private static StubServer stub;

  private Target() {}

  public static synchronized String baseUrl() {
    String configured = System.getenv("TARGET_URL");
    if (configured != null && !configured.isBlank()) {
      return configured;
    }
    if (stub == null) {
      stub = StubServer.start(Boolean.getBoolean("stub.bug"));
      Runtime.getRuntime().addShutdownHook(new Thread(stub::stop));
    }
    return "http://127.0.0.1:" + stub.port();
  }
}
