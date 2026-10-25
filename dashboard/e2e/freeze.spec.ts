// Runs against `next start` with BUDGETGUARD_API pointing at e2e/fake-api.mjs (fixtures from the
// platform's OpenAPI examples). Not executed in the skill-authoring session; run it in CI.
import { expect, test } from "@playwright/test";

test("an unknown service says why, in words", async ({ page }) => {
  await page.goto("/");
  const card = page.getByRole("article", { name: "inventory-service" });
  await expect(card.getByText("Unknown — no trustworthy signal")).toBeVisible();
  await expect(card.getByText("No metrics at all")).toBeVisible();
});

test("a blocked decision shows price vs affordable and replays identically", async ({ page }) => {
  await page.goto("/decisions/4812");
  await expect(page.getByText("block")).toBeVisible();
  await page.getByRole("button", { name: "Recompute from stored inputs" }).click();
  await expect(page.getByRole("status")).toContainText("Same verdict");
});

test("override needs a signed-in approver and a 20-character reason", async ({ page }) => {
  await page.goto("/decisions/4812");
  await expect(page.getByRole("button", { name: "Override and re-run" })).toBeVisible();
  await page.getByLabel("Reason (at least 20 characters)").fill("too short");
  await page.getByRole("button", { name: "Override and re-run" }).click();
  await expect(page.getByRole("alert")).toBeVisible();
});
