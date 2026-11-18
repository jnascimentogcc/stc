# language: en
Feature: Keyed table search

  Background:
    Given the program SEARCH-EXAMPLE is ready with the test data

  @program-search-example/FR-04 @program-search-example/FR-05 @TC-0001 @status:approved
  Scenario: A search on the first id finds the record
    When I search the keyed table for the id "0002"
    Then the record found has the ids "0002", "0102" and "0499" and the name "test item 2"

  @program-search-example/FR-04 @TC-0002 @status:approved
  Scenario: A search on an id that is not in the table finds nothing
    When I search the keyed table for the id "0004"
    Then the keyed result is "Item not found."

  @program-search-example/FR-07 @TC-0003 @status:approved
  Scenario: Three ids that match the same record find it
    When I search the keyed table for the ids "0003", "0103" and "0498"
    Then the record found has the ids "0003", "0103" and "0498" and the name "test item 3"

  @program-search-example/FR-07 @TC-0004 @status:approved
  Scenario: Three ids where one differs find no record
    When I search the keyed table for the ids "0001", "0101" and "0499"
    Then the keyed result is "Item not found."

  @program-search-example/FR-02 @program-search-example/FR-09 @TC-0005 @status:approved
  Scenario: A sequential search on an unordered table finds the value
    When I search the unordered table for the id "1"
    Then the sequential result is "Value of id 1."

  @program-search-example/FR-09 @TC-0006 @status:approved
  Scenario: A sequential search that finds nothing says so
    When I search the unordered table for the id "4"
    Then the sequential result is "Item not found."
