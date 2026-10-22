import assert from "node:assert/strict";
import { rm } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import test, { type TestContext } from "node:test";

import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

import idaMcp from "./ida-mcp.ts";

type Handler = (...args: unknown[]) => unknown;
type Tool = {
  name: string;
  loadMode?: string;
  execute: (...args: unknown[]) => Promise<unknown>;
};

function createHost(kind: "pi" | "omp" = "pi", discoverable = false) {
  const handlers = new Map<string, Handler>();
  const tools = new Map<string, Tool>();
  const status: { type: string; error?: string }[] = [];
  let widgetCalls = 0;
  const pi = {
    ...(kind === "omp" ? { arktype: {}, zod: {} } : {}),
    events: {
      emit(_channel: string, data: (typeof status)[number]) {
        status.push(data);
      },
    },
    registerFlag() {},
    getFlag() {
      return discoverable;
    },
    on(event: string, handler: Handler) {
      handlers.set(event, handler);
    },
    registerTool(tool: Tool) {
      tools.set(tool.name, tool);
    },
  } as unknown as ExtensionAPI;
  const ctx = {
    cwd: resolve("agent-workspace"),
    ui: {
      setWidget() {
        widgetCalls++;
      },
    },
    sessionManager: { getSessionFile: () => undefined },
  };
  idaMcp(pi);
  return {
    tools,
    status,
    ctx,
    get widgetCalls() {
      return widgetCalls;
    },
    start() {
      return handlers.get("session_start")!({}, ctx);
    },
    async shutdown() {
      await handlers.get("session_shutdown")!({}, ctx);
    },
    execute(
      name: string,
      args: Record<string, unknown> = {},
      signal?: AbortSignal,
    ) {
      const tool = tools.get(`ida_${name}`);
      assert.ok(tool, `${name} must be available before MCP starts`);
      return tool.execute("call", args, signal, undefined, ctx);
    },
  };
}

function mockMcp(t: TestContext) {
  return {
    connect: t.mock.method(Client.prototype, "connect", async () => undefined),
    listTools: t.mock.method(Client.prototype, "listTools", async () => ({
      tools: [],
    })),
    callTool: t.mock.method(Client.prototype, "callTool", async () => ({
      content: [{ type: "text", text: "ready" }],
    })),
    close: t.mock.method(Client.prototype, "close", async () => undefined),
  };
}

function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

for (const [kind, discoverable] of [
  ["pi", false],
  ["omp", false],
  ["omp", true],
] as const) {
  test(`${kind}${discoverable ? " discoverable" : ""}: lazy startup is shared and cancellation does not dispatch`, async (t) => {
    const mcp = mockMcp(t);
    const discoveryStarted = deferred();
    const discovery = deferred();
    mcp.listTools.mock.mockImplementation(async () => {
      discoveryStarted.resolve();
      await discovery.promise;
      return { tools: [] };
    });
    const host = createHost(kind, discoverable);
    t.after(() => host.shutdown());
    assert.equal(host.start(), undefined);
    await new Promise<void>((done) => setImmediate(done));
    assert.equal(mcp.connect.mock.callCount(), 0);
    assert.equal(host.widgetCalls, 0);
    assert.deepEqual(host.status, []);
    if (kind === "omp") {
      assert.equal(
        host.tools.get("ida_reference")?.loadMode,
        discoverable ? "discoverable" : "essential",
      );
    }

    const cancelled = new AbortController();
    cancelled.abort();
    await assert.rejects(
      host.execute("reference", { query: "decompile" }, cancelled.signal),
      { name: "AbortError" },
    );
    assert.equal(mcp.connect.mock.callCount(), 0);

    const waiting = new AbortController();
    const cancelledCall = host.execute(
      "reference",
      { query: "decompile" },
      waiting.signal,
    );
    const successfulCall = host.execute("list_databases");
    await discoveryStarted.promise;
    assert.equal(mcp.connect.mock.callCount(), 1);
    assert.equal(mcp.callTool.mock.callCount(), 0);
    waiting.abort();
    discovery.resolve();
    await assert.rejects(cancelledCall, { name: "AbortError" });
    await successfulCall;
    assert.equal(mcp.listTools.mock.callCount(), 1);
    assert.equal(
      mcp.callTool.mock.callCount(),
      1,
      "cancelled calls must never reach IDA",
    );
    await host.execute("list_databases");
    assert.equal(
      mcp.connect.mock.callCount(),
      1,
      "later calls must reuse the connection",
    );
    if (kind === "omp") {
      assert.equal(host.widgetCalls, 0);
      assert.deepEqual(
        host.status.map((event) => event.type),
        ["connecting", "connected"],
      );
    }
  });
}

test("startup failure reaches all callers; a later explicit call can start again", async (t) => {
  const mcp = mockMcp(t);
  const failure = new Error("backend launch failed");
  mcp.connect.mock.mockImplementationOnce(async () => {
    throw failure;
  });
  const host = createHost("omp");
  t.after(async () => {
    await host.shutdown();
    for (const event of host.status) {
      const logPath = event.error?.split(" — Log: ")[1];
      if (logPath) await rm(dirname(logPath), { recursive: true, force: true });
    }
  });
  host.start();
  await Promise.all([
    assert.rejects(
      host.execute("reference", { query: "decompile" }),
      (error) => error === failure,
    ),
    assert.rejects(
      host.execute("list_databases"),
      (error) => error === failure,
    ),
  ]);
  assert.equal(mcp.connect.mock.callCount(), 1);
  assert.equal(mcp.callTool.mock.callCount(), 0);
  assert.equal(mcp.close.mock.callCount(), 1);
  assert.deepEqual(
    host.status.map((event) => event.type),
    ["connecting", "failed"],
  );
  await host.execute("list_databases");
  assert.equal(mcp.connect.mock.callCount(), 2);
  assert.equal(mcp.callTool.mock.callCount(), 1);
});

test("shutdown during discovery cannot attach the old connection to a new session", async (t) => {
  const mcp = mockMcp(t);
  const discoveryStarted = deferred();
  const discovery = deferred();
  mcp.listTools.mock.mockImplementationOnce(async () => {
    discoveryStarted.resolve();
    await discovery.promise;
    return { tools: [] };
  });
  const host = createHost("omp");
  t.after(() => host.shutdown());
  host.start();
  const oldCall = host.execute("list_databases");
  await discoveryStarted.promise;
  await host.shutdown();
  host.start();
  await host.execute("list_databases");
  discovery.resolve();
  await assert.rejects(oldCall, /session ended/);
  assert.equal(mcp.callTool.mock.callCount(), 1);
  await host.execute("list_databases");
  assert.equal(mcp.connect.mock.callCount(), 2);
  assert.deepEqual(
    host.status.map((event) => event.type),
    ["connecting", "connecting", "connected"],
  );
});

test("shutdown before the SDK loads cannot launch a backend for the next session", async (t) => {
  const mcp = mockMcp(t);
  const host = createHost("omp");
  t.after(() => host.shutdown());
  host.start();
  const oldCall = host.execute("list_databases");
  await host.shutdown();
  host.start();
  await assert.rejects(oldCall, /session ended/);
  assert.equal(mcp.connect.mock.callCount(), 0);
  await host.execute("list_databases");
  assert.equal(mcp.connect.mock.callCount(), 1);
});

test("open_database resolves paths from the agent workspace", async (t) => {
  const mcp = mockMcp(t);
  const host = createHost();
  t.after(() => host.shutdown());
  host.start();
  const relativeArgs = { path: "tests/crackme03.elf" };
  const absolutePath = resolve(host.ctx.cwd, "binary.elf");
  await host.execute("open_database", relativeArgs);
  await host.execute("open_database", { path: absolutePath });
  await host.execute("open_database", { path: "~/binary.elf" });
  assert.deepEqual(
    mcp.callTool.mock.calls.map((call) => call.arguments[0].arguments),
    [
      { path: resolve(host.ctx.cwd, "tests/crackme03.elf") },
      { path: absolutePath },
      { path: "~/binary.elf" },
    ],
  );
  assert.deepEqual(relativeArgs, { path: "tests/crackme03.elf" });
});
