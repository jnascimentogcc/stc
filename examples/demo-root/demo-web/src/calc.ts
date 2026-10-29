export function add(a: number, b: number): number {
  return a + b;
}

export class Calculator {
  total(values: number[]): number {
    return values.reduce((sum, value) => add(sum, value), 0);
  }
}
