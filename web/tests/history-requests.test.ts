import assert from "node:assert/strict";
import { HistoryRequestCoordinator } from "../src/history-requests.ts";
import { RecoverableReadCoordinator } from "../src/recoverable-read.ts";
import {
  HistoryImageAssetCache,
  historyImageAssetKey,
} from "../src/history-image-assets.ts";

let now = 1_000;
const coordinator = new HistoryRequestCoordinator(() => now, 500);
let sends = 0;
const send = () => { sends += 1; };

coordinator.beginConnection();
assert.equal(coordinator.request({
  sid: "session-1", limit: 4,
}, send), true);
// connected, wrapper_reconnected and replay_start collapse even when the first
// focus request did not yet know the wrapper generation.
assert.equal(coordinator.request({
  sid: "session-1", limit: 4, generation: "generation-1",
}, send), false);
assert.equal(coordinator.request({
  sid: "session-1", limit: 4, generation: "generation-1",
}, send), false);
assert.equal(sends, 1);

// Pagination and another session are independent.
assert.equal(coordinator.request({
  sid: "session-1", before: "turn-5", limit: 12,
}, send), true);
assert.equal(coordinator.request({
  sid: "session-2", limit: 4,
}, send), true);
assert.equal(sends, 3);

// An older response must not clear a rollback-bound replacement request.
assert.equal(coordinator.request({
  sid: "session-1", limit: 4,
  generation: "generation-1", revision: "revision-2",
}, send), true);
assert.equal(sends, 4);
coordinator.complete({
  session_id: "session-1", generation: "generation-1",
  revision: "revision-1",
});
assert.equal(coordinator.size(), 3);
coordinator.complete({
  session_id: "session-1", generation: "generation-1",
  revision: "revision-2",
});
assert.equal(coordinator.size(), 2);

// A new socket and a timed-out request may retry exactly once.
coordinator.beginConnection();
assert.equal(coordinator.size(), 0);
assert.equal(coordinator.request({ sid: "session-1", limit: 4 }, send), true);
now += 600;
assert.equal(coordinator.request({ sid: "session-1", limit: 4 }, send), true);
assert.equal(sends, 6);

let nextTimer = 1;
const scheduled = new Map<number, () => void>();
const repair = new RecoverableReadCoordinator(
  (callback) => {
    const timer = nextTimer++;
    scheduled.set(timer, callback);
    return timer;
  },
  (timer) => { scheduled.delete(timer); },
  250,
);
let repairs = 0;
assert.equal(repair.retry("detail:turn-1", () => { repairs += 1; }), true);
assert.equal(repair.retry("detail:turn-1", () => { repairs += 1; }), false,
  "duplicate failures cannot schedule parallel repair reads");
assert.equal(scheduled.size, 1);
const firstRepair = scheduled.get(1);
scheduled.delete(1);
firstRepair?.();
assert.equal(repairs, 1);
assert.equal(repair.retry("detail:turn-1", () => { repairs += 1; }), false,
  "the failed repair response must stop instead of looping");
assert.equal(repair.retry("detail:turn-1", () => { repairs += 1; }), true,
  "a later explicit request starts a fresh one-shot repair cycle");
repair.complete("detail:turn-1");
assert.equal(scheduled.size, 0,
  "an authoritative response cancels a repair that has not fired");

assert.equal(repair.retry("history:page-1", () => { repairs += 1; }), true);
repair.clear();
assert.equal(scheduled.size, 0,
  "disconnect cleanup cancels every pending repair");

const images = new HistoryImageAssetCache(2);
assert.equal(images.begin({
  sid: "session-1", turnId: "turn-1", imageId: "image-1",
  variant: "thumbnail", requestId: "image-request-1", revision: "revision-1",
}), true);
assert.equal(images.accept({
  v: 19, type: "history_image", ts: 1,
  session_id: "session-2", turn_id: "turn-1", image_id: "image-1",
  variant: "thumbnail", request_id: "image-request-1", revision: "revision-1",
  media_type: "image/webp", data: "abc",
}), false, "a delayed response from another session cannot fill this cache");
assert.equal(images.accept({
  v: 19, type: "history_image", ts: 1,
  session_id: "session-1", turn_id: "turn-1", image_id: "image-1",
  variant: "thumbnail", request_id: "image-request-1", revision: "revision-1",
  media_type: "image/webp", width: 10, height: 5, data: "abc",
}), true);
assert.equal(images.forSession("session-1")[
  historyImageAssetKey("turn-1", "image-1", "thumbnail")
].status, "ready");
assert.deepEqual(images.forSession("session-2"), {});
