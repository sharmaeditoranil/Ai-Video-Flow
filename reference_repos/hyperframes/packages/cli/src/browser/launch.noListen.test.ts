import { EventEmitter } from "node:events";
import type { LaunchOptions, PuppeteerNode } from "puppeteer-core";
import { describe, expect, it, vi } from "vitest";

const probe = vi.hoisted(() => ({ options: [] as LaunchOptions[] }));

vi.mock("./gpuPolicy.js", async (actual) => ({
  ...(await actual<typeof import("./gpuPolicy.js")>()),
  resolveCaptureBrowserGpuMode: async (
    _mode: string,
    _path: string | undefined,
    hooks: { launch: (puppeteer: PuppeteerNode, options: LaunchOptions) => Promise<unknown> },
  ) => {
    const puppeteer = {
      launch: async (options: LaunchOptions) => {
        probe.options.push(options);
        return { process: () => new EventEmitter() };
      },
    } as unknown as PuppeteerNode;
    await hooks.launch(puppeteer, { headless: true, args: ["--headless"] });
    return "software";
  },
}));

import { resolveManagedGpuMode } from "./launch.js";

describe("the GPU probe where nothing may listen", () => {
  it("talks to its Chrome over a pipe in one process, and keeps the usual launch otherwise", async () => {
    await resolveManagedGpuMode("auto", "/chrome", { noListen: true });
    await resolveManagedGpuMode("auto", "/chrome");
    expect(probe.options[0]).toMatchObject({
      pipe: true,
      args: ["--headless", "--single-process"],
    });
    expect(probe.options[1]!.pipe).toBeUndefined();
    expect(probe.options[1]!.args).toEqual(["--headless"]);
  });
});
