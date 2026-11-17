package spike.support;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.networknt.schema.JsonSchema;
import com.networknt.schema.JsonSchemaFactory;
import com.networknt.schema.SpecVersion;
import com.networknt.schema.ValidationMessage;
import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.HashSet;
import java.util.Set;

/**
 * Reads a specification sidecar the way a consumer must: validate it against the contract
 * (contracts/spec-sidecar.schema.json), refuse it if it does not validate, and only then use it.
 */
public final class Sidecar {

  private static final ObjectMapper JSON = new ObjectMapper();

  private final Set<String> active = new HashSet<>();
  private final Set<String> retired = new HashSet<>();

  private Sidecar() {}

  public static Path contractsDir() {
    return Path.of(System.getProperty("contracts.dir", "../../contracts"));
  }

  public static JsonSchema schema() {
    try (InputStream in = Files.newInputStream(contractsDir().resolve("spec-sidecar.schema.json"))) {
      return JsonSchemaFactory.getInstance(SpecVersion.VersionFlag.V202012).getSchema(in);
    } catch (IOException e) {
      throw new IllegalStateException("cannot read the contract", e);
    }
  }

  /** The contract violations of a document; empty when it is valid. */
  public static Set<ValidationMessage> violations(JsonNode document) {
    return schema().validate(document);
  }

  public static JsonNode read(Path file) {
    try {
      return JSON.readTree(Files.readAllBytes(file));
    } catch (IOException e) {
      throw new IllegalStateException("cannot read " + file, e);
    }
  }

  public static Sidecar load(Path file) {
    JsonNode document = read(file);
    Set<ValidationMessage> errors = violations(document);
    if (!errors.isEmpty()) {
      throw new IllegalArgumentException("the sidecar does not follow the contract: " + errors);
    }
    Sidecar sidecar = new Sidecar();
    document.get("items").forEach(item -> sidecar.active.add(item.get("id").asText()));
    document.get("retired").forEach(item -> sidecar.retired.add(item.get("id").asText()));
    return sidecar;
  }

  public Set<String> active() {
    return active;
  }

  public Set<String> retired() {
    return retired;
  }
}
