package stctests.steps;

import static io.restassured.RestAssured.given;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import io.cucumber.java.en.Given;
import io.cucumber.java.en.Then;
import io.cucumber.java.en.When;
import io.restassured.http.ContentType;
import io.restassured.path.json.JsonPath;
import java.util.LinkedHashMap;
import java.util.Map;
import stctests.catalog.Action;
import org.junit.jupiter.api.Assumptions;
import stctests.support.Mapping;
import stctests.support.Target;

/**
 * Sample actions of the library: business-level steps bound to the boundary of the system (HTTP
 * here). The steps know the interface mapping (which field carries which prompt); the scenarios do
 * not. Every step carries {@link Action}, which feeds catalog/actions.json.
 */
public class SearchSteps {

  private static final Mapping MAPPING = Mapping.of("search", "program-search-example");

  private final Map<String, String> input = new LinkedHashMap<>();
  private JsonPath result;

  @Action(description = "Checks that the program under test is up and loaded with the test data.")
  @Given("the program SEARCH-EXAMPLE is ready with the test data")
  public void ready() {
    Assumptions.assumeTrue(
        MAPPING.usable(), "the interface mapping is not approved for this subject: contract-level tests stay pending");
    given().baseUri(Target.baseUrl()).get("/health").then().statusCode(200);
  }

  @Action(description = "Searches the keyed table by the first id.")
  @When("I search the keyed table for the id {string}")
  public void searchById(String id) {
    put("keyedId", id);
    run();
  }

  @Action(description = "Searches the keyed table by the three ids together.")
  @When("I search the keyed table for the ids {string}, {string} and {string}")
  public void searchByThreeIds(String id1, String id2, String id3) {
    put("tripleId1", id1);
    put("tripleId2", id2);
    put("tripleId3", id3);
    run();
  }

  @Action(description = "Searches the unordered table sequentially by id.")
  @When("I search the unordered table for the id {string}")
  public void searchSequentially(String id) {
    put("sequentialId", id);
    run();
  }

  @Action(description = "Checks that the keyed search found nothing and showed the given message.")
  @Then("the keyed result is {string}")
  public void keyedResult(String message) {
    assertFalse(result.getBoolean(MAPPING.result("keyedFound")), "a record was found, but none was expected");
    assertEquals(message, result.getString(MAPPING.result("keyedMessage")));
  }

  @Action(description = "Checks that the keyed search found the record with the given ids and name.")
  @Then("the record found has the ids {string}, {string} and {string} and the name {string}")
  public void recordFound(String id1, String id2, String id3, String name) {
    assertTrue(result.getBoolean(MAPPING.result("keyedFound")), "a record should have been found");
    assertEquals(id1, result.getString(MAPPING.result("keyedRecordId1")));
    assertEquals(id2, result.getString(MAPPING.result("keyedRecordId2")));
    assertEquals(id3, result.getString(MAPPING.result("keyedRecordId3")));
    assertEquals(name, result.getString(MAPPING.result("keyedRecordName")));
  }

  @Action(description = "Checks the message of the sequential search.")
  @Then("the sequential result is {string}")
  public void sequentialResult(String message) {
    assertEquals(message, result.getString(MAPPING.result("sequentialMessage")));
  }

  private void put(String logicalName, String value) {
    input.put(MAPPING.field(logicalName), value);
  }

  private void run() {
    result =
        given()
            .baseUri(Target.baseUrl())
            .contentType(ContentType.JSON)
            .body(input)
            .request(MAPPING.method(), MAPPING.path())
            .then()
            .statusCode(200)
            .extract()
            .jsonPath();
  }
}
