import assert from "node:assert/strict";
import test, { type TestContext } from "node:test";

import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import type {
  ExtensionAPI,
  ExtensionContext,
} from "@earendil-works/pi-coding-agent";

import idaMcp from "./ida-mcp.ts";
import { SessionStdioTransport } from "./ida-mcp-transport.ts";

type Handler = (...args: unknown[]) => unknown;

function requireHandler(
  handlers: ReadonlyMap<string, Handler>,
  event: string,
): Handler {
  const handler = handlers.get(event);
  assert.ok(handler, `missing ${event} handler`);
  return handler;
}

test("OMP waits for MCP tool registration at the first agent start", async (t) => {
  const originalConnect = Reflect.get(Client.prototype, "connect");
  const originalListTools = Reflect.get(Client.prototype, "listTools");
  const originalClose = Reflect.get(Client.prototype, "close");
  t.after(() => {
    Reflect.set(Client.prototype, "connect", originalConnect);
    Reflect.set(Client.prototype, "listTools", originalListTools);
    Reflect.set(Client.prototype, "close", originalClose);
  });

  let releaseToolDiscovery: (() => void) | undefined;
  const toolDiscoveryBlocked = new Promise<void>((resolve) => {
    releaseToolDiscovery = resolve;
  });
  let discoveryStarted = false;
  Reflect.set(Client.prototype, "connect", async () => undefined);
  Reflect.set(Client.prototype, "listTools", async () => {
    discoveryStarted = true;
    await toolDiscoveryBlocked;
    return {
      tools: [
        {
          name: "execute_python",
          description: "Run Python",
          inputSchema: { type: "object" },
        },
      ],
    };
  });
  Reflect.set(Client.prototype, "close", async () => undefined);

  const handlers = new Map<string, Handler>();
  const registeredTools: string[] = [];
  const pi = {
    arktype: {},
    zod: {},
    registerFlag() {},
    getFlag() {
      return false;
    },
    on(event: string, handler: Handler) {
      handlers.set(event, handler);
    },
    registerTool(tool: unknown) {
      if (
        tool !== null &&
        typeof tool === "object" &&
        "name" in tool &&
        typeof tool.name === "string"
      ) {
        registeredTools.push(tool.name);
      }
    },
  } as unknown as ExtensionAPI;
  const ctx = {
    ui: {
      setWidget() {},
    },
  };

  idaMcp(pi);

  const sessionStartResult = requireHandler(handlers, "session_start")({}, ctx);
  assert.equal(
    sessionStartResult,
    undefined,
    "session startup must not await MCP",
  );
  await new Promise<void>((resolve) => setImmediate(resolve));
  assert.equal(discoveryStarted, true);
  assert.deepEqual(registeredTools, []);

  let agentStartFinished = false;
  const agentStart = Promise.resolve(
    requireHandler(handlers, "before_agent_start")({}, ctx),
  ).then(() => {
    agentStartFinished = true;
  });
  await Promise.resolve();
  assert.equal(agentStartFinished, false);
  assert.deepEqual(registeredTools, []);

  releaseToolDiscovery?.();
  await agentStart;
  assert.deepEqual(registeredTools, ["ida_execute_python"]);

  await requireHandler(handlers, "session_shutdown")({}, ctx);
});

function deferred() {
  let resolve!: () => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<void>((done, fail) => {
    resolve = done;
    reject = fail;
  });
  return { promise, resolve, reject };
}

const tick = () => new Promise<void>((resolve) => setImmediate(resolve));

function lifecycleHarness(
  t: TestContext,
  options: {
    omp?: boolean;
    connect?: (client: Client) => Promise<void>;
    discover?: (client: Client) => Promise<void>;
    close?: (client: Client) => Promise<void>;
  } = {},
) {
  const handlers = new Map<string, Handler>();
  type Tool = Parameters<ExtensionAPI["registerTool"]>[0];
  const tools = new Map<string, Tool>();
  const registrations: string[] = [];
  const connections: Client[] = [];
  const closes: Client[] = [];
  const calls: { client: Client; params: unknown }[] = [];
  let widget: { render(): string[] } | undefined;
  t.mock.method(Client.prototype, "connect", async function (this: Client) {
    connections.push(this);
    await options.connect?.(this);
  });
  t.mock.method(Client.prototype, "listTools", async function (this: Client) {
    await options.discover?.(this);
    return {
      tools: [{ name: "execute_python", inputSchema: { type: "object" } }],
    };
  });
  t.mock.method(Client.prototype, "close", async function (this: Client) {
    closes.push(this);
    await options.close?.(this);
  });
  t.mock.method(
    Client.prototype,
    "callTool",
    async function (this: Client, params: unknown) {
      calls.push({ client: this, params });
      return { content: [{ type: "text", text: "ok" }] };
    },
  );
  const pi = {
    ...(options.omp === false ? {} : { arktype: {}, zod: {} }),
    registerFlag() {},
    getFlag() {
      return false;
    },
    on(event: string, handler: Handler) {
      handlers.set(event, handler);
    },
    registerTool(tool: Tool) {
      tools.set(tool.name, tool);
      registrations.push(tool.name);
    },
  } as unknown as ExtensionAPI;
  const ctx = {
    sessionManager: { getSessionFile: () => "/sessions/current.jsonl" },
    ui: {
      setWidget(_key: string, value: unknown) {
        widget =
          typeof value === "function"
            ? value(
                { requestRender() {} },
                {
                  fg: (_color: string, text: string) => text,
                  bold: (text: string) => text,
                },
              )
            : undefined;
      },
    },
  } as unknown as ExtensionContext;
  idaMcp(pi);
  const emit = (event: string, data: unknown = {}) =>
    requireHandler(handlers, event)(data, ctx);
  t.after(async () => {
    await Promise.resolve(emit("session_shutdown")).catch(() => undefined);
  });
  return {
    emit,
    handlers,
    connections,
    closes,
    calls,
    registrations,
    status: () => widget?.render().join("\n"),
    tool: () => {
      const tool = tools.get("ida_execute_python");
      assert.ok(tool);
      return tool;
    },
    execute: (tool: Tool) =>
      tool.execute("call", {}, undefined, undefined, ctx),
  };
}

for (const [event, reason] of [
  ["session_switch", "new"],
  ["session_switch", "resume"],
  ["session_switch", "fork"],
  ["session_branch", undefined],
] as const) {
  test(`OMP replaces its client after ${event} (${reason ?? "branch"})`, async (t) => {
    const h = lifecycleHarness(t);
    h.emit("session_start");
    await h.emit("before_agent_start");
    const oldTool = h.tool();
    await h.execute(oldTool);
    const first = h.connections[0];

    await h.emit(event, { reason });
    await h.emit("input");
    assert.equal(h.connections.length, 2);
    assert.notEqual(h.connections[1], first);
    assert.deepEqual(h.closes, [first]);
    await assert.rejects(h.execute(oldTool), /not connected/);
    await h.execute(h.tool());
    assert.deepEqual(
      h.calls.map((call) => call.client),
      h.connections,
    );
    assert.deepEqual(h.calls[1].params, {
      name: "execute_python",
      arguments: {},
      _meta: { omp_session_path: "/sessions/current.jsonl" },
    });
  });
}

test("Pi shutdown/start waits for the preceding close and keeps startup nonblocking", async (t) => {
  const gate = deferred();
  t.after(gate.resolve);
  const h = lifecycleHarness(t, { omp: false, close: () => gate.promise });
  assert.equal(h.handlers.has("session_switch"), false);
  assert.equal(h.emit("session_start"), undefined);
  await h.emit("input");
  const oldTool = h.tool();
  const stopped = h.emit("session_shutdown");
  assert.equal(h.emit("session_start"), undefined);
  await tick();
  assert.equal(h.connections.length, 1);
  await assert.rejects(h.execute(oldTool), /not connected/);
  gate.resolve();
  await stopped;
  await h.emit("input");
  assert.equal(h.connections.length, 2);
  await h.execute(h.tool());
  assert.deepEqual(h.calls[0].params, {
    name: "execute_python",
    arguments: {},
    _meta: { pi_session_path: "/sessions/current.jsonl" },
  });
});

test("replacement also waits for cleanup of a failed startup", async (t) => {
  const gate = deferred();
  t.after(gate.resolve);
  let first = true;
  const h = lifecycleHarness(t, {
    discover: async () => {
      if (!first) return;
      first = false;
      throw new Error("discovery failed");
    },
    close: () => gate.promise,
  });
  h.emit("session_start");
  await tick();
  const switched = h.emit("session_switch", { reason: "new" });
  await tick();
  assert.equal(h.connections.length, 1);
  gate.resolve();
  await switched;
  await h.emit("input");
  assert.equal(h.connections.length, 2);
  assert.deepEqual(h.closes, [h.connections[0]]);
  assert.match(h.status() ?? "", /ready/);
});

test("OMP discards pending tool registration on switch", async (t) => {
  const h = lifecycleHarness(t);
  h.emit("session_start");
  await tick();
  assert.deepEqual(h.registrations, []);
  await h.emit("session_switch", { reason: "new" });
  await h.emit("input");
  assert.deepEqual(h.registrations, ["ida_execute_python"]);
  await h.execute(h.tool());
  assert.equal(h.calls[0].client, h.connections[1]);
});

for (const fails of [false, true]) {
  test(`stale discovery ${fails ? "failure" : "success"} cannot publish into a replacement session`, async (t) => {
    const gate = deferred();
    let first = true;
    const h = lifecycleHarness(t, {
      discover: async () => {
        if (!first) return;
        first = false;
        await gate.promise;
      },
    });
    h.emit("session_start");
    await tick();
    const oldWaiter = h.emit("before_agent_start");
    await h.emit("session_switch", { reason: "new" });
    await h.emit("input");
    if (fails) gate.reject(new Error("obsolete discovery failed"));
    else gate.resolve();
    await oldWaiter;
    await tick();
    assert.deepEqual(h.registrations, ["ida_execute_python"]);
    assert.deepEqual(h.closes, [h.connections[0]]);
    assert.match(h.status() ?? "", /ready/);
    assert.doesNotMatch(h.status() ?? "", /failed/);
  });
}

test("shutdown invalidates a pending connect without duplicate closes", async (t) => {
  const gate = deferred();
  let first = true;
  const h = lifecycleHarness(t, {
    connect: async () => {
      if (!first) return;
      first = false;
      await gate.promise;
    },
  });
  h.emit("session_start");
  await tick();
  await h.emit("session_shutdown");
  h.emit("session_start");
  await h.emit("input");
  gate.resolve();
  await tick();
  assert.equal(h.connections.length, 2);
  assert.deepEqual(h.registrations, ["ida_execute_python"]);
  assert.deepEqual(h.closes, [h.connections[0]]);
});

test("shutdown before startup runs never connects and repeated shutdown is safe", async (t) => {
  const h = lifecycleHarness(t);
  h.emit("session_start");
  await h.emit("session_shutdown");
  await h.emit("session_shutdown");
  assert.deepEqual(h.connections, []);
  assert.equal(h.closes.length, 1);
  assert.deepEqual(h.registrations, []);
  assert.equal(h.status(), undefined);
});

test("shutdown supersedes rapid switches while the old child is closing", async (t) => {
  const gate = deferred();
  t.after(gate.resolve);
  const h = lifecycleHarness(t, { close: () => gate.promise });
  h.emit("session_start");
  await h.emit("input");
  const firstSwitch = h.emit("session_switch", { reason: "new" });
  const secondSwitch = h.emit("session_switch", { reason: "new" });
  const shutdown = h.emit("session_shutdown");
  await tick();
  assert.equal(h.connections.length, 1);
  gate.resolve();
  await Promise.all([firstSwitch, secondSwitch, shutdown]);
  await tick();
  assert.equal(h.connections.length, 1);
  assert.equal(new Set(h.closes).size, h.closes.length);
  assert.equal(h.status(), undefined);
});

test("an old in-flight tool result is rejected after replacement", async (t) => {
  const gate = deferred();
  t.after(gate.resolve);
  const h = lifecycleHarness(t);
  t.mock.method(Client.prototype, "callTool", async () => {
    await gate.promise;
    return { content: [{ type: "text", text: "obsolete result" }] };
  });
  h.emit("session_start");
  await h.emit("input");
  const rejected = assert.rejects(h.execute(h.tool()), /session has changed/);
  await h.emit("session_switch", { reason: "new" });
  await h.emit("input");
  gate.resolve();
  await rejected;
});

test("failed close prevents a replacement connection", async (t) => {
  const h = lifecycleHarness(t, {
    close: async () => {
      throw new Error("close failed");
    },
  });
  h.emit("session_start");
  await h.emit("input");
  await assert.rejects(
    Promise.resolve(h.emit("session_switch")),
    /close failed/,
  );
  await h.emit("input");
  assert.equal(h.connections.length, 1);
  assert.match(h.status() ?? "", /close failed/);
});

test(
  "stdio close waits for slow EOF cleanup rather than killing the owned child",
  { timeout: 15000 },
  async (t) => {
    const transport = new SessionStdioTransport({
      command: process.execPath,
      args: [
        "-e",
        `
      process.stdin.resume();
      process.stdout.write(JSON.stringify({jsonrpc: "2.0", method: "ready"}) + "\\n");
      process.stdin.on("end", () => setTimeout(() => {
        process.stderr.write("cleanup complete");
        process.exit(0);
      }, 4500));
    `,
      ],
    });
    t.after(() => transport.close());
    const ready = deferred();
    let stderr = "";
    let closed = 0;
    transport.stderr.on("data", (chunk) => {
      stderr += chunk.toString();
    });
    transport.onmessage = () => ready.resolve();
    transport.onclose = () => {
      closed++;
    };
    await transport.start();
    await ready.promise;
    const closing = transport.close();
    assert.equal(transport.close(), closing);
    await closing;
    assert.equal(stderr, "cleanup complete");
    assert.equal(closed, 1);
    await assert.rejects(
      transport.send({ jsonrpc: "2.0", method: "late" }),
      /not connected/,
    );
  },
);

test("stdio frames requests, handles split responses, and observes natural exit", async (t) => {
  const transport = new SessionStdioTransport({
    command: process.execPath,
    args: [
      "-e",
      `
      let input = "";
      process.stdin.on("data", (chunk) => {
        input += chunk;
        if (!input.includes("\\n")) return;
        const request = JSON.parse(input);
        const response = JSON.stringify({jsonrpc: "2.0", id: request.id, result: {ok: true}}) + "\\n";
        process.stdout.write(response.slice(0, 10));
        setTimeout(() => { process.stdout.write(response.slice(10)); process.exit(0); }, 20);
      });
    `,
    ],
  });
  t.after(() => transport.close());
  const received: unknown[] = [];
  const exited = deferred();
  transport.onmessage = (message) => received.push(message);
  transport.onclose = exited.resolve;
  await transport.start();
  await transport.send({ jsonrpc: "2.0", id: 1, method: "ping" });
  await exited.promise;
  await transport.close();
  assert.deepEqual(received, [{ jsonrpc: "2.0", id: 1, result: { ok: true } }]);
});

test("stdio startup errors can be closed, and a closed transport cannot restart", async () => {
  const transport = new SessionStdioTransport({
    command: process.execPath,
    args: [],
    cwd: `/nonexistent-ida-mcp-test-${process.pid}-${Date.now()}`,
  });
  let errors = 0;
  let closes = 0;
  transport.onerror = () => {
    errors++;
  };
  transport.onclose = () => {
    closes++;
  };
  await assert.rejects(transport.start(), /ENOENT/);
  await transport.close();
  await transport.close();
  assert.equal(errors, 1);
  assert.equal(closes, 1);
  await assert.rejects(transport.start(), /already started or closed/);
});

test("duplicate starts and unrelated events do not restart the connection", async (t) => {
  const h = lifecycleHarness(t);
  h.emit("session_start");
  await h.emit("input");
  h.emit("session_start");
  await h.emit("before_agent_start");
  assert.equal(h.connections.length, 1);
  assert.deepEqual(h.closes, []);
  for (const event of [
    "session_before_switch",
    "session_before_branch",
    "session_tree",
    "session_compact",
  ]) {
    assert.equal(h.handlers.has(event), false);
  }
});
