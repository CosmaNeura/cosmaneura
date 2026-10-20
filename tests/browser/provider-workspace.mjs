// Synthetic native storage/identity host; actual browser, Core and Scribe routes.
import { pathToFileURL } from "node:url";
const { chromium } = await import(
  pathToFileURL(process.env.SCRIBE_PLAYWRIGHT_MODULE).href
);
const { expect } = await import(
  pathToFileURL(process.env.SCRIBE_PLAYWRIGHT_TEST_MODULE).href
);
const [root, origin, output, width] = process.argv.slice(2);
if (![390, 1280].includes(Number(width)))
  throw new Error("Known viewport required");
if (!/^http:\/\/127\.0\.0\.1:\d+$/u.test(origin))
  throw new Error("Loopback only");
const { createServer } = await import(
  pathToFileURL(`${root}/node_modules/vite/dist/node/index.js`).href
);
const vite = await createServer({
  root: `${root}/apps/capture`,
  configFile: false,
  publicDir: false,
  server: {
    host: "127.0.0.1",
    port: 0,
    fs: { allow: [root] },
    proxy: { "/provider-fixture": origin },
  },
});
const browser = await chromium.launch({ headless: true });
let page;
try {
  await vite.listen();
  const port = vite.httpServer.address().port;
  page = await browser.newPage({
    viewport: { width: Number(width), height: 900 },
  });
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto(
    `http://127.0.0.1:${port}/e2e/fixtures/provider-workspace.html`,
  );
  await page.getByRole("button", { name: /Synthetic QA visit/ }).click();
  await page.locator('select[name="language"]').selectOption("en-US+es-US");
  await page.locator('select[name="modality"]').selectOption("in_person");
  if ((await page.evaluate(() => window.providerTest.sourceStarts)) !== 0)
    throw new Error("Recording started before consent");
  await page
    .getByRole("button", { name: "Confirm consent & record", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Finish recording", exact: true }),
  ).toBeVisible({ timeout: 20000 });
  await page
    .getByRole("button", { name: "Finish recording", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Accept English translation" }),
  ).toBeVisible({ timeout: 20000 });
  await expect(
    page.getByRole("button", { name: "Review draft note", exact: true }),
  ).toHaveCount(0);
  await page
    .getByRole("button", { name: "Accept English translation" })
    .click();
  await page
    .getByRole("button", { name: "Review draft note", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Prepare draft note", exact: true })
    .click();
  await expect(page.locator('textarea[name="note-0"]')).toHaveValue(
    "No fever reported.",
  );
  await expect(page.locator('textarea[name="note-1"]')).toHaveValue(
    "Not mentioned in transcript",
  );
  await page.locator('textarea[name="note-0"]').fill("Synthetic manual edit");
  await page.getByRole("button", { name: "Save draft", exact: true }).click();
  await expect(page.locator("[data-note-save-state]")).toHaveText(
    "Saved · Not clinically reviewed",
  );
  await expect(page.locator("#provider")).not.toHaveAttribute(
    "aria-busy",
    "true",
  );
  await page
    .getByRole("button", { name: "Open saved note in Core", exact: true })
    .click();
  await expect(page.locator("[data-core-encounter]")).toContainText(
    "Synthetic manual edit",
  );
  await page.reload();
  await page.getByRole("button", { name: /Synthetic QA visit/ }).click();
  await page
    .getByRole("button", { name: "Review draft note", exact: true })
    .click();
  await expect(page.locator('textarea[name="note-0"]')).toHaveValue(
    "Synthetic manual edit",
  );
  if (
    await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)
  )
    throw new Error("Horizontal overflow");
  if ((await page.evaluate(() => window.providerTest.sourceStarts)) !== 0)
    throw new Error("Reopening started another recording");
  await page.screenshot({
    path: `${output}/provider-workspace-${width}.png`,
    fullPage: true,
  });
  if (errors.length) throw new Error(errors.join("; "));
  process.stdout.write(JSON.stringify({ saved_and_reopened: true }) + "\n");
} catch (error) {
  if (page) {
    await page.screenshot({
      path: `${output}/provider-workspace-failure.png`,
      fullPage: true,
    });
  }
  throw error;
} finally {
  await browser.close();
  await vite.close();
}
