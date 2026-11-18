package stctests;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.stream.Stream;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.MethodSource;
import stctests.support.Mapping;
import stctests.support.Sidecar;

/**
 * The interface mapping files follow the contract: the same fixtures STC checks in Python give the
 * same verdict here, and every mapping in {@code mapping/} loads.
 */
class MappingTest {

  private static Stream<Path> files(Path dir) throws IOException {
    try (Stream<Path> all = Files.list(dir)) {
      return all.filter(p -> p.toString().endsWith(".yaml")).sorted().toList().stream();
    }
  }

  static Stream<Path> valid() throws IOException {
    return files(Sidecar.contractsDir().resolve("fixtures/mapping/valid"));
  }

  static Stream<Path> invalid() throws IOException {
    return files(Sidecar.contractsDir().resolve("fixtures/mapping/invalid"));
  }

  @ParameterizedTest(name = "{0}")
  @MethodSource("valid")
  void validFixturesPass(Path fixture) {
    assertEquals(0, Mapping.violations(Mapping.read(fixture)).size(), fixture.toString());
  }

  @ParameterizedTest(name = "{0}")
  @MethodSource("invalid")
  void invalidFixturesFail(Path fixture) {
    assertFalse(Mapping.violations(Mapping.read(fixture)).isEmpty(), fixture + " should not validate");
  }

  @Test
  void aMappingThatBreaksTheContractIsRefused() {
    Path bad = Sidecar.contractsDir().resolve("fixtures/mapping/invalid/unknown-major.mapping.yaml");
    assertThrows(IllegalArgumentException.class, () -> Mapping.load(bad));
  }

  @Test
  void everyMappingOfTheRepositoryLoads() throws IOException {
    int found = 0;
    try (Stream<Path> all = Files.walk(Mapping.mappingsDir())) {
      for (Path file : all.filter(p -> p.toString().endsWith(".mapping.yaml")).sorted().toList()) {
        Mapping.load(file);
        found++;
      }
    }
    assertTrue(found > 0, "no mapping found in " + Mapping.mappingsDir());
  }

  @Test
  void theSampleMappingGivesTheStepsTheirNames() {
    Mapping mapping = Mapping.of("search", "program-search-example");
    assertTrue(mapping.approved());
    assertEquals("POST", mapping.method());
    assertEquals("/program-search-example/runs", mapping.path());
    assertEquals("keyedId", mapping.field("keyedId"));
    assertEquals("keyedSearch.message", mapping.result("keyedMessage"));
    assertThrows(IllegalStateException.class, () -> mapping.field("noSuchInput"));
  }
}
