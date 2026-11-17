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
import stctests.support.Target;

/**
 * Sample actions of the library: business-level steps bound to the boundary of the system (HTTP
 * here). The steps know the interface mapping (which field carries which prompt); the scenarios do
 * not. Every step carries {@link Action}, which feeds catalog/actions.json.
 */
public class SearchSteps {

  private final Map<String, String> input = new LinkedHashMap<>();
  private JsonPath result;
  private String section;

  @Action(description = "Checks that the program under test is up and loaded with the test data.")
  @Given("the program SEARCH-EXAMPLE is ready with the test data")
  public void ready() {
    given().baseUri(Target.baseUrl()).get("/health").then().statusCode(200);
  }

  @Action(description = "Searches the keyed table by the first id.")
  @When("I search the keyed table for the id {string}")
  public void searchById(String id) {
    input.put("keyedId", id);
    section = "keyedSearch";
    run();
  }

  @Action(description = "Searches the keyed table by the three ids together.")
  @When("I search the keyed table for the ids {string}, {string} and {string}")
  public void searchByThreeIds(String id1, String id2, String id3) {
    input.put("tripleId1", id1);
    input.put("tripleId2", id2);
    input.put("tripleId3", id3);
    section = "keyedSearch";
    run();
  }

  @Action(description = "Searches the unordered table sequentially by id.")
  @When("I search the unordered table for the id {string}")
  public void searchSequentially(String id) {
    input.put("sequentialId", id);
    section = "sequentialSearch";
    run();
  }

  @Action(description = "Checks that the keyed search found nothing and showed the given message.")
  @Then("the keyed result is {string}")
  public void keyedResult(String message) {
    assertFalse(result.getBoolean("keyedSearch.found"), "a record was found, but none was expected");
    assertEquals(message, result.getString("keyedSearch.message"));
  }

  @Action(description = "Checks that the keyed search found the record with the given ids and name.")
  @Then("the record found has the ids {string}, {string} and {string} and the name {string}")
  public void recordFound(String id1, String id2, String id3, String name) {
    assertTrue(result.getBoolean("keyedSearch.found"), "a record should have been found");
    assertEquals(id1, result.getString("keyedSearch.record.id1"));
    assertEquals(id2, result.getString("keyedSearch.record.id2"));
    assertEquals(id3, result.getString("keyedSearch.record.id3"));
    assertEquals(name, result.getString("keyedSearch.record.name"));
  }

  @Action(description = "Checks the message of the sequential search.")
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
