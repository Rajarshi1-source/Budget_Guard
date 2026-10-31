// The four D-8 dashboard flows (plan W8.4), against `next start` and e2e/fake-api.mjs.
import { expect, test } from "@playwright/test";

test("an unknown service says why, in words", async ({ page }) => {
  await page.goto("/");
  const card = page.getByRole("article", { name: "inventory-service" });
  await expect(card.getByText("Unknown — no trustworthy signal")).toBeVisible();
  await expect(card.getByText(/No metrics at all/)).toBeVisible();
  await expect(card.getByText("—").first()).toBeVisible();          // no number invented for missing data
});

test("recompute from stored inputs says Same verdict", async ({ page }) => {
  await page.goto("/decisions/4812");
  await expect(page.getByText("ACTIVE_PAGE")).toBeVisible();
  await page.getByRole("button", { name: "Recompute from stored inputs" }).click();
  await expect(page.getByRole("status")).toContainText("Same verdict (block, ACTIVE_PAGE)");
});

test("a short override reason is refused with the rule in words", async ({ page }) => {
  await page.goto("/decisions/4812");
  await page.getByLabel("Reason (at least 20 characters)").fill("too short");
  await page.getByRole("button", { name: "Override and re-run" }).click();
  // scoped to main: Next.js renders its own (empty) route-announcer alert outside it
  await expect(page.getByRole("main").getByRole("alert")).toContainText("at least 20 characters");
});

test("the override form appears only on an overridable block", async ({ page }) => {
  await page.goto("/decisions/4813");
  await expect(page.getByText("WITHIN_BUDGET")).toBeVisible();
  await expect(page.getByRole("button", { name: "Recompute from stored inputs" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Override and re-run" })).toHaveCount(0);
});
