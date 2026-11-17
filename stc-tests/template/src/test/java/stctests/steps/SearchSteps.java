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
import stctests.support.Target;

/**
 * The core actions of the library: business-level steps bound to the boundary of the system (HTTP
 * here). The steps know the interface mapping (which field carries which prompt); the scenarios do
 * not.
 */
public class SearchSteps {

  private final Map<String, String> input = new LinkedHashMap<>();
  private JsonPath result;
  private String section;

  @Given("the program SEARCH-EXAMPLE is ready with the test data")
  public void ready() {
    given().baseUri(Target.baseUrl()).get("/health").then().statusCode(200);
  }

  @When("I search the keyed table for the id {string}")
  public void searchById(String id) {
    input.put("keyedId", id);
    section = "keyedSearch";
    run();
  }

  @When("I search the keyed table for the ids {string}, {string} and {string}")
  public void searchByThreeIds(String id1, String id2, String id3) {
    input.put("tripleId1", id1);
    input.put("tripleId2", id2);
    input.put("tripleId3", id3);
    section = "keyedSearch";
    run();
  }

  @When("I search the unordered table for the id {string}")
  public void searchSequentially(String id) {
    input.put("sequentialId", id);
    section = "sequentialSearch";
    run();
  }

  @Then("the keyed result is {string}")
  public void keyedResult(String message) {
    assertFalse(result.getBoolean("keyedSearch.found"), "a record was found, but none was expected");
    assertEquals(message, result.getString("keyedSearch.message"));
  }

  @Then("the record found has the ids {string}, {string} and {string} and the name {string}")
  public void recordFound(String id1, String id2, String id3, String name) {
    assertTrue(result.getBoolean("keyedSearch.found"), "a record should have been found");
    assertEquals(id1, result.getString("keyedSearch.record.id1"));
    assertEquals(id2, result.getString("keyedSearch.record.id2"));
    assertEquals(id3, result.getString("keyedSearch.record.id3"));
    assertEquals(name, result.getString("keyedSearch.record.name"));
  }

  @Then("the sequential result is {string}")
  public void sequentialResult(String message) {
    assertEquals(message, result.getString("sequentialSearch.message"));
  }

  private void run() {
    result =
        given()
            .baseUri(Target.baseUrl())
            .contentType(ContentType.JSON)
            .body(input)
            .post("/program-search-example/runs")
            .then()
            .statusCode(200)
            .extract()
            .jsonPath();
    assertTrue(result.getMap("").containsKey(section), "the run should answer " + section);
  }
}
