export function isCodexRoute(route: unknown): boolean {
  return typeof route === "string" && route.startsWith("openai-codex:");
}
