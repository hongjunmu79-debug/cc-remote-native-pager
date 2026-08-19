import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";

import type { Notice, ServerEvent, StatusReport } from "../src/protocol.ts";

const harness = await createServer({
  root: process.cwd(),
  appType: "custom",
  logLevel: "silent",
  server: { middlewareMode: true, watch: null },
});

try {
  const {
    createRuntime, initialState, reduce, MAX_SESSION_NOTICES,
  } = await harness.ssrLoadModule("/src/reducer.ts");
  const { NoticeStack } = await harness.ssrLoadModule(
    "/src/components/NoticeStack.tsx");
  const { StatusSheet } = await harness.ssrLoadModule(
    "/src/components/StatusSheet.tsx");
  const { statusNotices } = await harness.ssrLoadModule(
    "/src/notice-presentation.ts");
  const {
    ReconnectBanner, TRANSIENT_BANNER_TTL_MS,
  } = await harness.ssrLoadModule("/src/components/ReconnectBanner.tsx");
  const { ErrorBoundary } = await harness.ssrLoadModule(
    "/src/ErrorBoundary.tsx");
  const {
    presentCommandProblem, presentHistoricalTurnProblem, presentTurnProblem,
  } = await harness.ssrLoadModule("/src/problem-presentation.ts");
  const sid = "notice-session";
  const event = (body: Record<string, unknown>): ServerEvent => ({
    v: 10, ts: 10, sid, ...body,
  } as ServerEvent);
  let state = {
    ...initialState,
    banner: "machine reconnected — syncing…",
    focusedSid: sid,
    runtimes: { [sid]: createRuntime() },
  };

  // Per-session retention is bounded and duplicate ids replace/move instead of
  // growing the list.  Notice reduction must not mutate the reconnect banner.
  for (let index = 0; index < MAX_SESSION_NOTICES + 3; index += 1) {
    state = reduce(state, { type: "event", event: event({
      type: "notice",
      notice_id: `notice-${index}`,
      severity: "warning",
      category: "runtime",
      title: `warning ${index}`,
      message: "bounded message",
    }) });
  }
  assert.equal(state.runtimes[sid].notices.length, MAX_SESSION_NOTICES);
  assert.equal(state.runtimes[sid].notices[0].notice_id, "notice-3");
  state = reduce(state, { type: "event", event: event({
    type: "notice",
    notice_id: "notice-3",
    severity: "info",
    category: "deprecation",
    title: "updated",
    message: "same id",
  }) });
  assert.equal(state.runtimes[sid].notices.length, MAX_SESSION_NOTICES);
  assert.equal(state.runtimes[sid].notices.at(-1)?.title, "updated");
  assert.equal(state.banner, "machine reconnected — syncing…");

  state = reduce(state, {
    type: "dismiss_notice", sid, noticeId: "notice-3",
  });
  assert.equal(state.runtimes[sid].notices.some(
    (notice: Notice) => notice.notice_id === "notice-3"), false);

  state = reduce(state, {
    type: "command_error", detail: "详细过程已过期，请刷新会话后重试",
  });
  const staleDismiss = reduce(state, {
    type: "dismiss_banner", banner: "older warning",
  });
  assert.equal(staleDismiss.banner, "详细过程已过期，请刷新会话后重试",
    "an old timer must not dismiss a newer banner");
  state = reduce(state, {
    type: "dismiss_banner", banner: "详细过程已过期，请刷新会话后重试",
  });
  assert.equal(state.banner, undefined);

  const report = event({
    type: "status_report",
    thread: { thread_id: sid, status: "idle", active_flags: [] },
    runtime: {},
    context: {},
    account: null,
    rate_limits: [{
      limit_id: "codex", limit_name: "Codex", plan_type: "pro",
      primary: { used_percent: 40, resets_at: 900, window_duration_mins: 300 },
    }],
    usage: null,
    component_errors: [],
  }) as StatusReport;
  state = reduce(state, { type: "event", event: report });
  state = reduce(state, { type: "event", event: event({
    type: "rate_limit_update",
    limit_id: "codex",
    name: null,
    plan_type: null,
    reached_type: "rate_limit_reached",
    primary: { used_percent: 100, resets_at: null, window_duration_mins: null },
    secondary: null,
  }) });
  const merged = state.runtimes[sid].statusReport?.rate_limits[0];
  assert.equal(merged?.limit_name, "Codex");
  assert.equal(merged?.plan_type, "pro");
  assert.equal(merged?.primary?.used_percent, 100);
  assert.equal(merged?.primary?.resets_at, 900);
  assert.equal(merged?.rate_limit_reached_type, "rate_limit_reached");
  assert.equal(Object.hasOwn(merged ?? {}, "credits"), false);
  assert.equal(Object.hasOwn(merged ?? {}, "individualLimit"), false);

  const officialDiagnostic = event({
    type: "notice", notice_id: "codex-notice-private-diagnostic",
    severity: "warning", category: "runtime",
    title: "Codex runtime warning",
    message: "provider crash at /private/token; see wrapper logs",
    detail: "Traceback: secret",
  }) as Notice;
  const officialConversationMarkup = renderToStaticMarkup(createElement(
    NoticeStack, { notices: [officialDiagnostic], onDismiss: () => {} }));
  assert.equal(officialConversationMarkup, "",
    "official app-server diagnostics must not interrupt the transcript");
  const safeStatusNotices = statusNotices([officialDiagnostic]);
  assert.equal(safeStatusNotices.length, 1);
  assert.doesNotMatch(safeStatusNotices.map((notice: Notice) =>
    [notice.title, notice.message, notice.detail ?? ""].join(" ")).join(" "),
    /crash|warning|wrapper|private|traceback|secret/i);

  const hiddenDiagnostic = "provider crash at /private/token; see wrapper logs";
  assert.equal(presentTurnProblem({ code: "cc_crash", message: hiddenDiagnostic }),
    "本次回复未完成，请重试。");
  assert.equal(presentCommandProblem({ code: "internal", message: hiddenDiagnostic }),
    "操作未完成，请稍后重试。");
  assert.doesNotMatch(
    presentCommandProblem({ code: "protocol", message: hiddenDiagnostic }),
    /crash|wrapper|private|protocol/i);
  assert.equal(presentHistoricalTurnProblem("error"), "该轮未正常结束");

  const markup = renderToStaticMarkup(createElement(NoticeStack, {
    notices: state.runtimes[sid].notices,
    onDismiss: () => {},
  }));
  assert.match(markup, /notice-stack/);
  assert.equal((markup.match(/notice-dismiss/g) ?? []).length,
    state.runtimes[sid].notices.length);
  assert.doesNotMatch(markup, /\bwarning\b|crash|traceback/i,
    "conversation notices must use product copy instead of diagnostics");

  const statusMarkup = renderToStaticMarkup(createElement(StatusSheet, {
    open: true, report, notices: [officialDiagnostic], error: null,
    onClose: () => {}, onRefresh: () => {}, onDismissNotice: () => {},
  }));
  assert.match(statusMarkup, /需要关注/);
  assert.match(statusMarkup, /运行状态/);
  assert.doesNotMatch(statusMarkup,
    /crash|warning|wrapper|private|traceback|secret|rate_limit_reached/i,
    "the status sheet must not expose provider diagnostics or raw enums");

  assert.equal(TRANSIENT_BANNER_TTL_MS, 6_000);
  const transientBanner = renderToStaticMarkup(createElement(ReconnectBanner, {
    banner: "详细过程已过期，请刷新会话后重试",
    replaying: false,
    truncated: false,
    busy: false,
    onDismiss: () => {},
  }));
  assert.match(transientBanner, /banner-dismiss/);
  assert.match(transientBanner, /关闭提示/);
  const connectionBanner = renderToStaticMarkup(createElement(ReconnectBanner, {
    banner: "machine offline — waiting for reconnect",
    replaying: false,
    truncated: false,
    busy: true,
    onDismiss: () => {},
  }));
  assert.match(connectionBanner, /banner-dismiss/,
    "every persistent banner remains explicitly dismissible");
  const reconnectBannerSource = readFileSync(resolve(
    process.cwd(), "src/components/ReconnectBanner.tsx"), "utf8");
  assert.match(reconnectBannerSource,
    /window\.setTimeout\([\s\S]*TRANSIENT_BANNER_TTL_MS/,
    "transient banners must schedule their own dismissal");
  assert.match(reconnectBannerSource, /window\.clearTimeout\(timer\)/,
    "a changed or unmounted banner must cancel its stale timer");

  const appSource = readFileSync(resolve(process.cwd(), "src/App.tsx"), "utf8");
  assert.ok(appSource.indexOf("<ReconnectBanner") < appSource.indexOf("<NoticeStack"),
    "NoticeStack must remain below, not replace, ReconnectBanner");

  const boundary = new ErrorBoundary({ children: createElement("div") });
  boundary.state = { error: new Error(hiddenDiagnostic) };
  const boundaryMarkup = renderToStaticMarkup(boundary.render());
  assert.match(boundaryMarkup, /页面需要重新载入/);
  assert.match(boundaryMarkup, /重新载入/);
  assert.doesNotMatch(boundaryMarkup, /crash|wrapper|private|stack/i,
    "the recovery screen must not expose the render exception");
} finally {
  await harness.close();
}

console.log("notice and live rate-limit tests passed");
