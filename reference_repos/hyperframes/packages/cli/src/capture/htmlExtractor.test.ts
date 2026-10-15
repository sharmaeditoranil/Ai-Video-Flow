import { afterEach, describe, expect, it, vi } from "vitest";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { runInNewContext } from "node:vm";
import { Window } from "happy-dom";
import type { Page } from "puppeteer-core";
import { downloadAndRewriteFonts } from "./assetDownloader.js";
import { extractHtml } from "./htmlExtractor.js";

const FONT = readFileSync(
  new URL("../../../../docs/public/catalog/assets/a634cb9e7783af7e.woff2", import.meta.url),
);

function fakePage(window: Window): Page {
  const evaluate = async (script: unknown) => {
    if (typeof script !== "string") throw new Error("Expected a page expression");
    return runInNewContext(script, {
      window,
      document: window.document,
      location: window.location,
      URL,
    });
  };
  return { evaluate, url: () => window.location.href } as unknown as Page;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("extractHtml", () => {
  it("keeps a font the page declares in its own <style> with a relative url", async () => {
    const window = new Window({ url: "https://brand.example/pricing/" });
    window.document.head.innerHTML =
      '<style>@font-face{font-family:"Display";src:url("/fonts/Display-Medium.woff2") format("woff2")}' +
      ".glow{filter:url(#glow)}</style>";
    const dir = mkdtempSync(join(tmpdir(), "hf-inline-font-"));
    try {
      const extracted = await extractHtml(fakePage(window), { settleTime: 0 });
      expect(extracted.headHtml).toContain("url(#glow)");

      const fetched: string[] = [];
      vi.stubGlobal(
        "fetch",
        vi.fn(async (url: string) => {
          fetched.push(url);
          return new Response(new Uint8Array(FONT));
        }),
      );
      const fonts = await downloadAndRewriteFonts(extracted.headHtml, dir);
      expect(fetched).toEqual(["https://brand.example/fonts/Display-Medium.woff2"]);
      expect(fonts.css).toContain("assets/fonts/Display-Medium.woff2");
      expect(readFileSync(join(dir, "assets/fonts/Display-Medium.woff2"))).toEqual(FONT);
    } finally {
      rmSync(dir, { recursive: true, force: true });
      await window.happyDOM.close();
    }
  });
});
