package stctests.support;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.dataformat.yaml.YAMLMapper;
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
 * The interface mapping of one program (mapping/&lt;application&gt;/&lt;program&gt;.mapping.yaml): which
 * request field carries which input and where each result sits in the response. The steps read it,
 * so a change in the target's interface touches the mapping file, not the steps or the scenarios.
 * It is validated against the contract before use, and a mapping that is not approved keeps the
 * contract-level tests pending.
 */
public final class Mapping {

  private static final YAMLMapper YAML = new YAMLMapper();

  private final JsonNode document;

  private Mapping(JsonNode document) {
    this.document = document;
  }

  public static Path mappingsDir() {
    return Path.of(System.getProperty("mapping.dir", "mapping"));
  }

  private static JsonSchema schema() {
    Path file = Sidecar.contractsDir().resolve("interface-mapping.schema.json");
    try (InputStream in = Files.newInputStream(file)) {
      return JsonSchemaFactory.getInstance(SpecVersion.VersionFlag.V202012).getSchema(in);
    } catch (IOException e) {
      throw new IllegalStateException("cannot read the contract", e);
    }
  }

  /** The contract violations of a mapping document; empty when it is valid. */
  public static Set<ValidationMessage> violations(JsonNode document) {
    return schema().validate(document);
  }

  public static JsonNode read(Path file) {
    try {
      return YAML.readTree(Files.readAllBytes(file));
    } catch (IOException e) {
      throw new IllegalStateException("cannot read " + file, e);
    }
  }

  /** Loads a mapping, refusing one that does not follow the contract or contradicts itself. */
  public static Mapping load(Path file) {
    JsonNode document = read(file);
    Set<ValidationMessage> errors = violations(document);
    if (!errors.isEmpty()) {
      throw new IllegalArgumentException(file + " does not follow the contract: " + errors);
    }
    Set<String> inputs = new HashSet<>();
    document.at("/legacy/inputs").forEach(i -> inputs.add(i.get("name").asText()));
    document
        .at("/target/fields")
        .fieldNames()
        .forEachRemaining(
            name -> {
              if (!inputs.contains(name)) {
                throw new IllegalArgumentException(file + ": field " + name + " has no legacy input");
              }
            });
    return new Mapping(document);
  }

  /** The mapping of a program of an application, from {@link #mappingsDir()}. */
  public static Mapping of(String application, String program) {
    return load(mappingsDir().resolve(application).resolve(program + ".mapping.yaml"));
  }

  public boolean approved() {
    return "approved".equals(document.get("status").asText());
  }

  /**
   * Whether the scenarios may run against the current {@link Subject}. The target needs an approved
   * mapping. The legacy needs only to be executable: the run itself checks the mapping against the
   * real system, so it is how a mapping is confirmed, not something to approve first.
   */
  public boolean usable() {
    if (Subject.current() == Subject.LEGACY) {
      return document.at("/legacy/executable").asBoolean(false);
    }
    return approved();
  }

  private String request() {
    return Subject.current() == Subject.LEGACY ? "/legacy/request/" : "/target/request/";
  }

  private String fields() {
    return Subject.current() == Subject.LEGACY ? "/legacy/request/fields/" : "/target/fields/";
  }

  private String results() {
    return Subject.current() == Subject.LEGACY ? "/legacy/request/results/" : "/target/results/";
  }

  public String method() {
    return required(request(), "method");
  }

  public String path() {
    return required(request(), "path");
  }

  /** The request body property that carries a logical input. */
  public String field(String logicalName) {
    return required(fields(), logicalName);
  }

  /** The JSON path in the response of a logical result. */
  public String result(String logicalName) {
    return required(results(), logicalName);
  }

  private String required(String pointer, String name) {
    JsonNode node = document.at(pointer + name);
    if (node.isMissingNode()) {
      throw new IllegalStateException("the mapping has no " + pointer + name);
    }
    return node.asText();
  }
}
