import { expect, test } from "@playwright/test";

async function readingAnchor(page: import("@playwright/test").Page): Promise<{
  id: string;
  offset: number;
}> {
  return page.evaluate(() => {
    const viewport = document.querySelector<HTMLElement>(".thread");
    if (!viewport) throw new Error("thread viewport is missing");
    const viewportRect = viewport.getBoundingClientRect();
    const rows = [...document.querySelectorAll<HTMLElement>("[data-turn-id]")]
      .map((row) => ({ row, rect: row.getBoundingClientRect() }))
      .filter(({ rect }) =>
        rect.bottom > viewportRect.top && rect.top < viewportRect.bottom);
    const selected = rows.sort((left, right) =>
      Math.abs(left.rect.top - viewportRect.top)
      - Math.abs(right.rect.top - viewportRect.top))[0];
    const id = selected?.row.dataset.turnId;
    if (!selected || !id) throw new Error("no visible reading anchor");
    return { id, offset: selected.rect.top - viewportRect.top };
  });
}

async function turnIntersectsViewport(
  page: import("@playwright/test").Page,
  turnId: string,
): Promise<boolean> {
  return page.evaluate((id) => {
    const viewport = document.querySelector<HTMLElement>(".thread");
    const row = document.querySelector<HTMLElement>(
      `[data-turn-id="${CSS.escape(id)}"]`,
    );
    if (!viewport || !row) return false;
    const viewportRect = viewport.getBoundingClientRect();
    const rowRect = row.getBoundingClientRect();
    return rowRect.bottom > viewportRect.top && rowRect.top < viewportRect.bottom;
  }, turnId);
}

async function waitForScrollIdle(
  page: import("@playwright/test").Page,
): Promise<void> {
  let previous: number | null = null;
  let stableSamples = 0;
  for (let attempt = 0; attempt < 30; attempt += 1) {
    const current = await page.locator(".thread").evaluate(
      (node) => node.scrollTop,
    );
    stableSamples = previous != null && Math.abs(current - previous) < 0.5
      ? stableSamples + 1 : 0;
    if (stableSamples >= 4) return;
    previous = current;
    await page.waitForTimeout(50);
  }
  throw new Error("thread scroll position did not settle");
}

async function wheelUntilTurn(
  page: import("@playwright/test").Page,
  turnId: string,
  deltaY: number,
  projectName: string,
): Promise<void> {
  const viewport = page.locator(".thread");
  if (projectName === "webkit") {
    for (let attempt = 0; attempt < 12; attempt += 1) {
      if (await turnIntersectsViewport(page, turnId)) return;
      await dispatchTouchGesture(page, deltaY < 0 ? 60 : -60);
      await viewport.evaluate((node, delta) => {
        node.scrollBy({ top: delta, behavior: "smooth" });
      }, deltaY);
      await waitForScrollIdle(page);
    }
    expect(await turnIntersectsViewport(page, turnId)).toBe(true);
    return;
  }
  const box = await viewport.boundingBox();
  if (!box) throw new Error("thread viewport has no bounds");
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  for (let attempt = 0; attempt < 12; attempt += 1) {
    if (await turnIntersectsViewport(page, turnId)) return;
    await page.mouse.wheel(0, deltaY);
    await page.waitForTimeout(40);
  }
  expect(await turnIntersectsViewport(page, turnId)).toBe(true);
}

async function dispatchTouchGesture(
  page: import("@playwright/test").Page,
  fingerDeltaY: number,
  moves = 1,
): Promise<void> {
  await page.locator(".thread").evaluate((node, input) => {
    const target = node as HTMLElement;
    const dispatchTouch = (
      type: "touchstart" | "touchmove" | "touchend",
      clientY: number,
    ) => {
      // WebKit's Touch constructor is intentionally not public. React only
      // needs the TouchEvent list shape, so define it on a real bubbling Event.
      const touch = { identifier: 1, target, clientX: 120, clientY };
      const event = new Event(type, { bubbles: true, cancelable: true });
      Object.defineProperties(event, {
        touches: { value: type === "touchend" ? [] : [touch] },
        targetTouches: { value: type === "touchend" ? [] : [touch] },
        changedTouches: { value: [touch] },
      });
      target.dispatchEvent(event);
    };
    const startY = 160;
    dispatchTouch("touchstart", startY);
    for (let index = 0; index < input.moves; index += 1) {
      dispatchTouch("touchmove", startY + input.fingerDeltaY * (index + 1));
    }
    dispatchTouch("touchend", startY + input.fingerDeltaY * input.moves);
  }, { fingerDeltaY, moves });
}

async function dispatchTouchPhase(
  page: import("@playwright/test").Page,
  type: "touchstart" | "touchmove" | "touchend",
  clientY: number,
): Promise<void> {
  await page.locator(".thread").evaluate((node, input) => {
    const target = node as HTMLElement;
    const touch = {
      identifier: 1,
      target,
      clientX: 120,
      clientY: input.clientY,
    };
    const event = new Event(input.type, { bubbles: true, cancelable: true });
    Object.defineProperties(event, {
      touches: { value: input.type === "touchend" ? [] : [touch] },
      targetTouches: { value: input.type === "touchend" ? [] : [touch] },
      changedTouches: { value: [touch] },
    });
    target.dispatchEvent(event);
  }, { type, clientY });
}

async function requestOlderHistory(
  page: import("@playwright/test").Page,
  projectName: string,
  repeat = 1,
): Promise<void> {
  const viewport = page.locator(".thread");
  if (projectName !== "webkit") {
    for (let index = 0; index < repeat; index += 1) {
      await viewport.dispatchEvent("wheel", { deltaY: -80 });
    }
    return;
  }
  await dispatchTouchGesture(page, 60, repeat);
}

test("prepend preserves the exact reading row through delayed row growth", async ({
  page,
}, testInfo) => {
  await page.goto("/tests/history-browser.html");
  const viewport = page.locator(".thread");
  await expect(page.locator('[data-turn-id="o1"]')).toBeVisible();
  await viewport.evaluate((node) => { node.scrollTop = 0; });
  const before = await readingAnchor(page);
  await requestOlderHistory(page, testInfo.project.name);
  await expect(page.locator('[data-turn-id="n8"]')).toBeVisible();

  await expect.poll(async () => (await readingAnchor(page)).id).toBe(before.id);
  await expect.poll(async () =>
    Math.abs((await readingAnchor(page)).offset - before.offset),
  ).toBeLessThan(2);

  await page.waitForTimeout(800);
  const settled = await readingAnchor(page);
  expect(settled.id).toBe(before.id);
  expect(Math.abs(settled.offset - before.offset)).toBeLessThan(2);
});

test("a canonical image reference does not reserve a second hidden image row", async ({
  page,
}) => {
  await page.goto("/tests/history-browser.html?dual-image=1");
  const assertCanonicalImageLayout = async () => {
    const turn = page.locator('[data-turn-id="dual-image"]');
    await expect(turn).toBeVisible();
    await expect(turn.locator(".ubub-image-trigger")).toHaveCount(1);
    const gap = await turn.evaluate((node) => {
      const image = node.querySelector<HTMLElement>(".ubub-image-trigger");
      const meta = node.querySelector<HTMLElement>(".ubub-meta");
      if (!image || !meta) throw new Error("image layout is incomplete");
      return meta.getBoundingClientRect().top - image.getBoundingClientRect().bottom;
    });
    expect(gap).toBeLessThan(20);
  };

  await assertCanonicalImageLayout();
  await page.getByTestId("switch-session").click();
  await expect(page.locator('[data-turn-id="b4"]')).toBeVisible();
  await page.getByTestId("switch-session").click();
  await assertCanonicalImageLayout();
});

test("streaming rerenders cannot cancel an image preview close", async ({
  page,
}) => {
  await page.goto("/tests/history-browser.html?dual-image=1");
  await page.locator(".ubub-image-trigger").click();
  await expect(page.locator(".image-lightbox")).toBeVisible();

  await page.evaluate(() => {
    document.querySelector<HTMLButtonElement>(".image-lightbox-close")?.click();
    document.querySelector<HTMLButtonElement>('[data-testid="append-turn"]')?.click();
  });

  await expect(page.locator(".image-lightbox")).toHaveCount(0);
});

test("expanded tool batches use dense rows instead of individual cards", async ({
  page,
}) => {
  await page.goto("/tests/history-browser.html?compact-tools=1");
  await page.locator(".turn-process-head").click();
  await page.locator(".tool-group-h").click();

  const rows = page.locator(".tool-group-b .tool");
  await expect(rows).toHaveCount(3);
  const styles = await rows.evaluateAll((nodes) => nodes.map((node) => {
    const style = getComputedStyle(node);
    return {
      height: node.getBoundingClientRect().height,
      border: style.borderTopWidth,
      radius: style.borderTopLeftRadius,
      shadow: style.boxShadow,
    };
  }));
  for (const style of styles) {
    expect(style.height).toBeLessThan(40);
    expect(style.border).toBe("0px");
    expect(style.radius).toBe("0px");
    expect(style.shadow).toBe("none");
  }
});

test("a pending composer image previews without triggering removal", async ({
  page,
}) => {
  await page.goto("/tests/history-browser.html?composer-attachment=1");
  const preview = page.getByRole("button", { name: "预览待发送图片 1" });
  await expect(preview).toBeVisible();

  await preview.click();
  await expect(page.locator(".image-lightbox")).toBeVisible();
  await page.getByRole("button", { name: "关闭图片预览" }).click();
  await expect(page.locator(".image-lightbox")).toHaveCount(0);
  await expect(preview).toBeVisible();

  await page.getByRole("button", { name: "移除待发送图片 1" }).click();
  await expect(preview).toHaveCount(0);
  await expect(page.locator(".image-lightbox")).toHaveCount(0);
});

test("a page that finishes under an active touch restores its retained boundary", async ({
  page,
}, testInfo) => {
  test.skip(testInfo.project.name !== "webkit", "iOS WebKit touch settlement");
  await page.goto("/tests/history-browser.html?delay=5&manual-growth=1");
  const viewport = page.locator(".thread");
  await viewport.evaluate((node) => { node.scrollTop = 0; });
  const before = await readingAnchor(page);

  await dispatchTouchPhase(page, "touchstart", 160);
  await dispatchTouchPhase(page, "touchmove", 220);
  await expect(page.getByTestId("load-count")).toHaveText("1");
  await expect(page.locator('[data-turn-id="n8"]')).toBeAttached();
  await dispatchTouchPhase(page, "touchend", 220);

  await expect.poll(async () => (await readingAnchor(page)).id).toBe(before.id);
  await expect.poll(async () =>
    Math.abs((await readingAnchor(page)).offset - before.offset),
  ).toBeLessThan(2);
  await page.waitForTimeout(300);
  const settled = await readingAnchor(page);
  expect(settled.id).toBe(before.id);
  expect(Math.abs(settled.offset - before.offset)).toBeLessThan(2);
});

test("movement after an attached page rebases the held touch boundary", async ({
  page,
}, testInfo) => {
  test.skip(testInfo.project.name !== "webkit", "iOS WebKit touch settlement");
  await page.goto("/tests/history-browser.html?delay=5&manual-growth=1");
  const viewport = page.locator(".thread");
  await viewport.evaluate((node) => { node.scrollTop = 0; });
  const original = await readingAnchor(page);

  await dispatchTouchPhase(page, "touchstart", 160);
  await dispatchTouchPhase(page, "touchmove", 220);
  await expect(page.getByTestId("load-count")).toHaveText("1");
  await expect(page.locator('[data-turn-id="n8"]')).toBeAttached();

  // The response is already installed, but the same finger deliberately
  // reverses toward newer content before it is lifted.
  await dispatchTouchPhase(page, "touchmove", 80);
  await viewport.evaluate((node) => { node.scrollBy({ top: 720 }); });
  await expect.poll(async () => (await readingAnchor(page)).id)
    .not.toBe(original.id);
  // WebKit dispatches scroll before the virtualizer has necessarily committed
  // the newly visible row measurements. Freeze the user's actual settled
  // reading position, not that intermediate layout frame.
  await waitForScrollIdle(page);
  const moved = await readingAnchor(page);
  await dispatchTouchPhase(page, "touchend", 80);

  await expect.poll(async () => (await readingAnchor(page)).id).toBe(moved.id);
  await expect.poll(async () =>
    Math.abs((await readingAnchor(page)).offset - moved.offset),
  ).toBeLessThan(2);
  await page.waitForTimeout(300);
  const settled = await readingAnchor(page);
  expect(settled.id).toBe(moved.id);
  expect(Math.abs(settled.offset - moved.offset)).toBeLessThan(2);
});

test("repeated prepends preserve each page boundary instead of jumping to the inserted page", async ({
  page,
}, testInfo) => {
  await page.goto("/tests/history-browser.html?pages=4&delay=5&manual-growth=1");
  const viewport = page.locator(".thread");

  for (let pageNumber = 1; pageNumber <= 4; pageNumber += 1) {
    if (pageNumber === 1) {
      await viewport.evaluate((node) => { node.scrollTop = 0; });
    }
    await waitForScrollIdle(page);
    const before = await readingAnchor(page);
    const beforeScrollHeight = await viewport.evaluate((node) => node.scrollHeight);
    if (pageNumber === 1) {
      await requestOlderHistory(page, testInfo.project.name);
    } else {
      await page.locator(".load-more-btn").dispatchEvent("click");
    }
    await expect(page.getByTestId("load-count")).toHaveText(String(pageNumber));
    const insertedOldestId = pageNumber === 1 ? "n1" : `p${pageNumber}-1`;
    await expect.poll(async () =>
      viewport.evaluate((node) => node.scrollHeight),
    ).toBeGreaterThan(beforeScrollHeight);

    await expect.poll(async () => (await readingAnchor(page)).id).toBe(before.id);
    await expect.poll(async () =>
      Math.abs((await readingAnchor(page)).offset - before.offset),
    ).toBeLessThan(2);
    expect((await readingAnchor(page)).id).not.toBe(insertedOldestId);
    // End the wheel/touch gesture before pulling the next page. The product
    // intentionally allows only one request per physical gesture.
    await page.waitForTimeout(250);
  }
});

test("one upward gesture starts at most one older-page request", async ({
  page,
}, testInfo) => {
  await page.goto("/tests/history-browser.html");
  const viewport = page.locator(".thread");
  await viewport.evaluate((node) => { node.scrollTop = 0; });
  await requestOlderHistory(page, testInfo.project.name, 2);
  await expect(page.getByTestId("load-count")).toHaveText("1");
});

test("an empty final page removes the loader without moving the reading row", async ({
  page,
}, testInfo) => {
  await page.goto("/tests/history-browser.html?empty-final=1");
  const viewport = page.locator(".thread");
  await viewport.evaluate((node) => { node.scrollTop = 0; });
  const before = await readingAnchor(page);
  await requestOlderHistory(page, testInfo.project.name);
  await expect(page.getByRole("button", {
    name: "加载更早的历史",
  })).toHaveCount(0);
  const after = await readingAnchor(page);
  expect(after.id).toBe(before.id);
  expect(Math.abs(after.offset - before.offset)).toBeLessThan(2);
});

test("user movement after prepend stays stable through delayed growth", async ({
  page,
}, testInfo) => {
  await page.goto("/tests/history-browser.html?manual-growth=1");
  const viewport = page.locator(".thread");
  await viewport.evaluate((node) => { node.scrollTop = 0; });
  const initial = await readingAnchor(page);
  await requestOlderHistory(page, testInfo.project.name);
  await expect.poll(async () => (await readingAnchor(page)).id).toBe(initial.id);
  await wheelUntilTurn(page, "o2", 1_000, testInfo.project.name);
  await waitForScrollIdle(page);
  await page.waitForTimeout(300);
  const before = await readingAnchor(page);
  await page.getByTestId("grow-row").click();
  await expect(page.locator('[data-turn-id="n8"] p')).toHaveCount(28);
  await waitForScrollIdle(page);
  const after = await readingAnchor(page);
  expect(after.id).toBe(before.id);
  expect(Math.abs(after.offset - before.offset)).toBeLessThan(2);
});

test("a delayed page from the previous session cannot move the new session", async ({
  page,
}, testInfo) => {
  await page.goto("/tests/history-browser.html?delay=350");
  const viewport = page.locator(".thread");
  await viewport.evaluate((node) => { node.scrollTop = 0; });
  await requestOlderHistory(page, testInfo.project.name);
  await page.getByTestId("switch-session").click();
  await expect(page.locator('[data-turn-id="b4"]')).toBeVisible();
  await page.waitForTimeout(500);
  await expect(page.locator('[data-turn-id="n8"]')).toHaveCount(0);
  await expect(page.locator('[data-turn-id="b4"]')).toBeVisible();

  await page.getByTestId("switch-session").click();
  await expect(page.locator('[data-turn-id="o4"]')).toBeVisible();
});

test("same-session revision replacement resets to the latest row", async ({
  page,
}, testInfo) => {
  await page.goto("/tests/history-browser.html?large=40");
  await expect(page.locator('[data-turn-id="m40"]')).toBeVisible();
  await wheelUntilTurn(page, "m1", -2_000, testInfo.project.name);
  await expect(page.locator('[data-turn-id="m1"]')).toBeVisible();

  await page.getByTestId("replace-revision").click();
  await expect(page.locator('[data-turn-id="m1"]')).toHaveCount(0);
  await expect(page.locator('[data-turn-id="r24"]')).toBeVisible();
  await expect(page.locator('[data-turn-id="r1"]')).toHaveCount(0);
});

test("reversing direction while a page is pending preserves the reading row", async ({
  page,
}, testInfo) => {
  await page.goto("/tests/history-browser.html?delay=700");
  const viewport = page.locator(".thread");
  await viewport.evaluate((node) => { node.scrollTop = 0; });
  await requestOlderHistory(page, testInfo.project.name);
  await wheelUntilTurn(page, "o4", 2_000, testInfo.project.name);
  const before = await readingAnchor(page);
  await expect(page.locator('[data-turn-id="n8"]')).toHaveCount(1);
  const after = await readingAnchor(page);
  expect(after.id).toBe(before.id);
  expect(Math.abs(after.offset - before.offset)).toBeLessThan(2);
});

test("virtualization bounds mounted rows and preserves an expanded timeline", async ({
  page,
}) => {
  await page.goto("/tests/history-browser.html?timeline=1");
  const viewport = page.locator(".thread");
  await viewport.evaluate((node) => { node.scrollTop = 0; });
  const timeline = page.locator('[data-turn-id="timeline"]');
  await expect(timeline).toBeVisible();
  await timeline.locator(".turn-process-head").click();
  await expect(timeline.locator(".turn-process-head")).toHaveAttribute("aria-expanded", "true");

  await viewport.evaluate((node) => { node.scrollTop = node.scrollHeight; });
  await expect(timeline).toHaveCount(0);
  expect(await page.locator(".turn").count()).toBeLessThan(40);

  await viewport.evaluate((node) => { node.scrollTop = 0; });
  await expect(timeline).toBeVisible();
  await expect(timeline.locator(".turn-process-head")).toHaveAttribute("aria-expanded", "true");
});

test("nested process disclosures survive virtual row unmounts", async ({
  page,
}) => {
  await page.goto("/tests/history-browser.html?timeline=1&engine=claude");
  const viewport = page.locator(".thread");
  await viewport.evaluate((node) => { node.scrollTop = 0; });
  const timeline = page.locator('[data-turn-id="timeline"]');
  await timeline.locator(".turn-process-head").click();
  const activity = timeline.locator("details.process-activity");
  const reasoning = timeline.locator("details.process-reasoning");
  await activity.locator(":scope > summary").click();
  await reasoning.locator(":scope > summary").click();
  await expect(activity).toHaveAttribute("open", "");
  await expect(reasoning).toHaveAttribute("open", "");

  await viewport.evaluate((node) => { node.scrollTop = node.scrollHeight; });
  await expect(timeline).toHaveCount(0);
  await viewport.evaluate((node) => { node.scrollTop = 0; });
  await expect(timeline).toBeVisible();
  await expect(timeline.locator("details.process-activity"))
    .toHaveAttribute("open", "");
  await expect(timeline.locator("details.process-reasoning"))
    .toHaveAttribute("open", "");
});

test("one stationary press opens a process timeline while a newer turn grows", async ({
  page,
}) => {
  await page.goto("/tests/history-browser.html?interactive-timeline=1");
  const viewport = page.locator(".thread");
  await viewport.evaluate((node) => { node.scrollTop = node.scrollHeight; });

  const header = page.locator(
    '[data-turn-id="timeline"] .turn-process-head',
  );
  await expect(header).toBeVisible();
  await expect(header).toHaveAttribute("aria-expanded", "false");
  const box = await header.boundingBox();
  if (!box) throw new Error("process header has no bounds");
  const point = {
    x: box.x + box.width / 2,
    y: box.y + box.height / 2,
  };

  await page.mouse.move(point.x, point.y);
  await page.mouse.down();
  for (let index = 0; index < 4; index += 1) {
    await page.getByTestId("grow-stream").evaluate(
      (button: HTMLButtonElement) => button.click(),
    );
    await page.waitForTimeout(35);
  }
  await page.mouse.up();

  await expect(header).toHaveAttribute("aria-expanded", "true");
});

test("one stationary press opens nested thinking while a newer turn grows", async ({
  page,
}) => {
  await page.goto(
    "/tests/history-browser.html?interactive-timeline=1&engine=claude",
  );
  const viewport = page.locator(".thread");
  await viewport.evaluate((node) => { node.scrollTop = node.scrollHeight; });
  const timeline = page.locator('[data-turn-id="timeline"]');
  await timeline.locator(".turn-process-head").click();
  await waitForScrollIdle(page);
  const summary = timeline.locator(".process-reasoning > summary");
  const box = await summary.boundingBox();
  if (!box) throw new Error("nested reasoning summary has no bounds");

  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  for (let index = 0; index < 4; index += 1) {
    await page.getByTestId("grow-stream").evaluate(
      (button: HTMLButtonElement) => button.click(),
    );
    await page.waitForTimeout(35);
  }
  await page.mouse.up();

  await expect(timeline.locator("details.process-reasoning"))
    .toHaveAttribute("open", "");
});

test("live append follows at the bottom but not while reading history", async ({
  page,
}, testInfo) => {
  await page.goto("/tests/history-browser.html?large=40");
  await expect(page.locator('[data-turn-id="m40"]')).toBeVisible();
  await page.getByTestId("append-turn").click();
  await expect(page.locator('[data-turn-id="live-41"]')).toBeVisible();

  // Use the browser's native scroll pipeline here so the virtualizer and
  // React receive the same wheel/scroll ordering as a real user gesture.
  await wheelUntilTurn(page, "m1", -2_000, testInfo.project.name);
  await expect(page.locator(".scroll-bottom-btn")).toBeVisible();
  await page.waitForTimeout(250);
  await expect(page.locator('[data-turn-id="live-41"]')).toHaveCount(0);
  const before = await readingAnchor(page);
  await page.getByTestId("append-turn").click();
  await page.waitForTimeout(100);
  const after = await readingAnchor(page);
  expect(after.id).toBe(before.id);
  expect(Math.abs(after.offset - before.offset)).toBeLessThan(2);
  await expect(page.locator('[data-turn-id="live-42"]')).toHaveCount(0);
  expect(await page.locator(".turn").count()).toBeLessThan(40);
});

test("composer action growth keeps the live tail visible without stealing history", async ({
  page,
}, testInfo) => {
  await page.goto("/tests/history-browser.html?large=40&composer-resize=1");
  const viewport = page.locator(".thread");
  await expect(page.locator('[data-turn-id="m40"]')).toBeVisible();
  await viewport.evaluate((node) => { node.scrollTop = node.scrollHeight; });
  await expect.poll(async () => viewport.evaluate((node) =>
    node.scrollHeight - node.scrollTop - node.clientHeight,
  )).toBeLessThan(2);

  await page.getByTestId("toggle-composer").click();
  await expect.poll(async () => viewport.evaluate((node) =>
    node.scrollHeight - node.scrollTop - node.clientHeight,
  )).toBeLessThan(2);
  const spark = page.locator('[data-turn-id="m40"] .turn-done-mark');
  await expect(spark).toBeVisible();
  expect(await spark.evaluate((node) => {
    const viewportNode = document.querySelector<HTMLElement>(".thread");
    if (!viewportNode) throw new Error("thread viewport is missing");
    return node.getBoundingClientRect().bottom
      <= viewportNode.getBoundingClientRect().bottom + 1;
  })).toBe(true);

  await wheelUntilTurn(page, "m1", -2_000, testInfo.project.name);
  const before = await readingAnchor(page);
  await page.getByTestId("toggle-composer").click();
  await page.waitForTimeout(200);
  const after = await readingAnchor(page);
  expect(after.id).toBe(before.id);
  expect(Math.abs(after.offset - before.offset)).toBeLessThan(2);
});
