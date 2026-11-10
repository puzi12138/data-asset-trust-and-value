/** Test-only native Node APIs; the browser build keeps its existing dependency set. */
declare module 'node:crypto' {
  export const webcrypto: Crypto
  export function createHash(algorithm: string): { update(data: string): { digest(encoding: 'hex'): string } }
}
