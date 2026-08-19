// crypto.randomUUID() is only available in secure contexts (HTTPS or
// localhost). On a phone over http://<lan-ip> it's undefined and throws,
// crashing the app. Fall back to a Math.random-based UUID v4 in that case.
export function uuid(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    return (c === "x" ? r : (r & 0x3) | 0x8).toString(16);
  });
}
