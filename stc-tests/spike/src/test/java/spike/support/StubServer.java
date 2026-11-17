package spike.support;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.io.BufferedInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.util.List;

/**
 * A stand-in for the migrated target, so the spike needs no real system. The REST contract is
 * invented for the spike (the real one comes from the interface mapping): one POST with the
 * prompts of the console program as fields, one result per search. The values of record 1 are an
 * assumption: the example specification does not give them.
 *
 * <p>It is a plain blocking socket server, not {@code com.sun.net.httpserver}: that one opens an NIO
 * selector, which some restricted environments refuse. It answers one request per connection.
 *
 * <p>With {@code bug} set, the three-id search ignores the third id, a defect the scenarios must
 * catch.
 */
public final class StubServer {

  private static final ObjectMapper JSON = new ObjectMapper();
  private static final String NOT_FOUND = "Item not found.";
  private static final String CRLF = "\r\n";

  private record Row(String id1, String id2, String id3, String name) {}

  private static final List<Row> KEYED =
      List.of(
          new Row("0001", "0101", "0500", "test item 1"),
          new Row("0002", "0102", "0499", "test item 2"),
          new Row("0003", "0103", "0498", "test item 3"));
  private static final List<String> UNORDERED = List.of("5", "7", "1");

  private final ServerSocket server;
  private final boolean bug;

  private StubServer(ServerSocket server, boolean bug) {
    this.server = server;
    this.bug = bug;
  }

  public static StubServer start(boolean bug) {
    try {
      ServerSocket socket = new ServerSocket(0, 50, InetAddress.getByName("127.0.0.1"));
      StubServer stub = new StubServer(socket, bug);
      Thread.ofVirtual().name("stub-accept").start(stub::accept);
      return stub;
    } catch (IOException e) {
      throw new IllegalStateException("cannot start the stub", e);
    }
  }

  public int port() {
    return server.getLocalPort();
  }

  public void stop() {
    try {
      server.close();
    } catch (IOException ignored) {
      // closing a stub that is going away
    }
  }

  private void accept() {
    while (!server.isClosed()) {
      try {
        Socket client = server.accept();
        Thread.ofVirtual().start(() -> serve(client));
      } catch (IOException e) {
        return;
      }
    }
  }

  private void serve(Socket client) {
    try (client) {
      InputStream in = new BufferedInputStream(client.getInputStream());
      String requestLine = line(in);
      int length = 0;
      for (String header = line(in); !header.isEmpty(); header = line(in)) {
        if (header.toLowerCase().startsWith("content-length:")) {
          length = Integer.parseInt(header.substring("content-length:".length()).trim());
        }
      }
      byte[] body = in.readNBytes(length);
      String[] parts = requestLine.split(" ");
      String method = parts[0];
      String path = parts[1];
      if (path.equals("/health")) {
        reply(client, 200, "{\"status\":\"UP\"}");
      } else if (path.equals("/program-search-example/runs") && method.equals("POST")) {
        reply(client, 200, JSON.writeValueAsString(runs(JSON.readTree(body))));
      } else {
        reply(client, 404, "{\"error\":\"not found\"}");
      }
    } catch (IOException | RuntimeException e) {
      // a broken request just closes the connection
    }
  }

  private static String line(InputStream in) throws IOException {
    StringBuilder text = new StringBuilder();
    for (int c = in.read(); c != -1 && c != '\n'; c = in.read()) {
      if (c != '\r') {
        text.append((char) c);
      }
    }
    return text.toString();
  }

  private ObjectNode runs(JsonNode in) {
    ObjectNode out = JSON.createObjectNode();
    if (in.hasNonNull("keyedId")) {
      out.set("keyedSearch", keyed(in.get("keyedId").asText(), null, null));
    }
    if (in.hasNonNull("tripleId1")) {
      out.set(
          "keyedSearch",
          keyed(
              in.get("tripleId1").asText(),
              in.path("tripleId2").asText(),
              in.path("tripleId3").asText()));
    }
    if (in.hasNonNull("sequentialId")) {
      out.set("sequentialSearch", sequential(in.get("sequentialId").asText()));
    }
    return out;
  }

  private ObjectNode keyed(String id1, String id2, String id3) {
    ObjectNode result = JSON.createObjectNode();
    for (Row row : KEYED) {
      boolean match =
          id2 == null
              ? row.id1().equals(id1)
              : row.id1().equals(id1) && row.id2().equals(id2) && (bug || row.id3().equals(id3));
      if (match) {
        result.put("found", true);
        ObjectNode record = result.putObject("record");
        record.put("id1", row.id1()).put("id2", row.id2()).put("id3", row.id3());
        record.put("name", row.name());
        return result;
      }
    }
    return result.put("found", false).put("message", NOT_FOUND);
  }

  private ObjectNode sequential(String id) {
    ObjectNode result = JSON.createObjectNode();
    int position = UNORDERED.indexOf(id);
    if (position < 0) {
      return result.put("found", false).put("message", NOT_FOUND);
    }
    return result
        .put("found", true)
        .put("position", position + 1)
        .put("message", "Value of id " + id + ".");
  }

  private static void reply(Socket client, int status, String body) throws IOException {
    byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
    String head =
        "HTTP/1.1 " + status + " X" + CRLF
            + "Content-Type: application/json" + CRLF
            + "Content-Length: " + bytes.length + CRLF
            + "Connection: close" + CRLF + CRLF;
    OutputStream out = client.getOutputStream();
    out.write(head.getBytes(StandardCharsets.US_ASCII));
    out.write(bytes);
    out.flush();
  }
}
