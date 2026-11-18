package stctests.steps;

import static io.restassured.RestAssured.given;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;

import io.cucumber.java.en.Given;
import io.cucumber.java.en.Then;
import io.cucumber.java.en.When;
import io.restassured.http.ContentType;
import io.restassured.response.Response;
import java.math.BigDecimal;
import java.util.LinkedHashMap;
import java.util.Map;
import org.junit.jupiter.api.Assumptions;
import stctests.catalog.Action;
import stctests.catalog.Action.Layer;
import stctests.support.Mapping;
import stctests.support.Target;

/**
 * Sample actions for a JSON loan-simulation API (the PHP example). The steps know which request
 * property carries which input and where each result sits, from the interface mapping; the
 * scenarios do not. They run against the migrated target or, with SUBJECT=legacy, against the
 * executable legacy as an oracle.
 */
public class LoanSteps {

  private static final Mapping MAPPING = Mapping.of("loan-api", "endpoints-public-index-php");
  private static final Map<String, String> VALID =
      Map.of("amount", "1000", "months", "12", "annualRate", "12");

  private Response response;

  @Action(description = "Checks that the loan API is up.")
  @Given("the loan API is ready")
  public void ready() {
    Assumptions.assumeTrue(
        MAPPING.usable(),
        "the interface mapping is not approved for this subject: contract-level tests stay pending");
    given().baseUri(Target.baseUrl()).get("/health").then().statusCode(200);
  }

  @Action(
      description = "Simulates a loan, sending the amount, the number of months and the yearly rate.")
  @When("I simulate a loan of {string} for {string} months at {string} percent a year")
  public void simulate(String amount, String months, String annualRate) {
    send(body(amount, months, annualRate, false));
  }

  @Action(description = "Simulates a loan whose amount is sent as text instead of as a number.")
  @When(
      "I simulate a loan with the amount {string} written as text, for {string} months at {string}"
          + " percent a year")
  public void simulateWithTextAmount(String amount, String months, String annualRate) {
    send(body(amount, months, annualRate, true));
  }

  @Action(description = "Simulates a valid loan leaving out one field (amount, months or annualRate).")
  @When("I simulate a loan without the field {string}")
  public void simulateWithout(String field) {
    Map<String, Object> body =
        body(VALID.get("amount"), VALID.get("months"), VALID.get("annualRate"), false);
    body.remove(MAPPING.field(field));
    send(body);
  }

  @Action(
      description = "Calls any method on any path of the API, with an empty JSON body.",
      layer = Layer.CORE)
  @When("I call the method {string} on the path {string}")
  public void call(String method, String path) {
    response =
        given()
            .baseUri(Target.baseUrl())
            .contentType(ContentType.JSON)
            .body("{}")
            .request(method, path);
  }

  @Action(description = "Checks the HTTP status of the last response.", layer = Layer.CORE)
  @Then("the response status is {int}")
  public void status(int expected) {
    assertEquals(expected, response.statusCode());
  }

  @Action(description = "Checks the error message of the last response.")
  @Then("the response error is {string}")
  public void error(String expected) {
    assertEquals(expected, response.jsonPath().getString(MAPPING.result("error")));
  }

  @Action(description = "Checks the monthly installment, compared as a number.")
  @Then("the installment is {string}")
  public void installment(String expected) {
    assertMoney(expected, "installment");
  }

  @Action(description = "Checks the total paid, compared as a number.")
  @Then("the total paid is {string}")
  public void totalPaid(String expected) {
    assertMoney(expected, "totalPaid");
  }

  @Action(description = "Checks the total interest, compared as a number.")
  @Then("the total interest is {string}")
  public void totalInterest(String expected) {
    assertMoney(expected, "totalInterest");
  }

  private void assertMoney(String expected, String logicalName) {
    String actual = response.jsonPath().getString(MAPPING.result(logicalName));
    assertNotNull(actual, "the response has no " + logicalName);
    assertEquals(
        0, new BigDecimal(expected).compareTo(new BigDecimal(actual)), logicalName + ": " + actual);
  }

  private Map<String, Object> body(
      String amount, String months, String annualRate, boolean amountAsText) {
    Map<String, Object> body = new LinkedHashMap<>();
    body.put(MAPPING.field("amount"), amountAsText ? amount : value(amount));
    body.put(MAPPING.field("months"), value(months));
    body.put(MAPPING.field("annualRate"), value(annualRate));
    return body;
  }

  /** A number when the text is one, otherwise the text itself (an invalid value to be rejected). */
  private static Object value(String text) {
    try {
      return new BigDecimal(text);
    } catch (NumberFormatException e) {
      return text;
    }
  }

  private void send(Map<String, Object> body) {
    response =
        given()
            .baseUri(Target.baseUrl())
            .contentType(ContentType.JSON)
            .body(body)
            .request(MAPPING.method(), MAPPING.path());
  }
}
