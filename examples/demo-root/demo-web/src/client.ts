export async function loadGreeting(name: string): Promise<string> {
  const response = await fetch(`http://demo-api/greet/${name}`);
  const body = await response.json();
  return body.message;
}
