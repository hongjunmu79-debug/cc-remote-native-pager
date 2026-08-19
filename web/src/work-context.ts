import type { ContextReport } from "./protocol";

export interface WorkContextMetrics {
  sessionTokens: number;
  sessionPercentage: number;
  fixedTokens: number;
  totalTokens: number;
  totalPercentage: number;
  hasBreakdown: boolean;
}

function nonNegative(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value >= 0
    ? value
    : null;
}

/**
 * Work presents conversation growth separately from its fresh-session startup
 * baseline. Older wrappers only send total_tokens/percentage, so
 * they retain the legacy reading until a breakdown is available rather than
 * showing a misleading synthetic zero.
 */
export function workContextMetrics(report: ContextReport): WorkContextMetrics {
  const totalTokens = nonNegative(report.total_tokens) ?? 0;
  const maxTokens = nonNegative(report.max_tokens) ?? 0;
  const explicitSession = nonNegative(report.session_tokens);
  const explicitFixed = nonNegative(report.fixed_tokens);
  const hasBreakdown = explicitSession !== null || explicitFixed !== null;

  const sessionTokens = explicitSession
    ?? (explicitFixed === null ? totalTokens : Math.max(0, totalTokens - explicitFixed));
  const fixedTokens = explicitFixed
    ?? (explicitSession === null ? 0 : Math.max(0, totalTokens - explicitSession));
  const explicitSessionPercentage = nonNegative(report.session_percentage);
  const sessionPercentage = explicitSessionPercentage
    ?? (maxTokens > 0 ? sessionTokens / maxTokens * 100 : 0);
  const totalPercentage = nonNegative(report.percentage)
    ?? (maxTokens > 0 ? totalTokens / maxTokens * 100 : 0);

  return {
    sessionTokens,
    sessionPercentage,
    fixedTokens,
    totalTokens,
    totalPercentage,
    hasBreakdown,
  };
}
