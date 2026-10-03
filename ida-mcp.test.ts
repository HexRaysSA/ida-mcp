import assert from "node:assert/strict";
import { resolve } from "node:path";
import test from "node:test";

import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

import idaMcp from "./ida-mcp.ts";

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
  const statusEvents: { channel: string; data: unknown }[] = [];
  let widgetCalls = 0;
  const pi = {
    arktype: {},
    zod: {},
    events: {
      emit(channel: string, data: unknown) {
        statusEvents.push({ channel, data });
      },
    },
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
      setWidget() {
        widgetCalls++;
      },
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
  assert.equal(widgetCalls, 0, "OMP must not draw the Pi status widget");
  assert.deepEqual(statusEvents, [
    {
      channel: "mcp:connection-status",
      data: { type: "connecting", serverNames: ["ida"] },
    },
    {
      channel: "mcp:connection-status",
      data: { type: "connected", serverName: "ida" },
    },
  ]);
});

test("open_database resolves paths from the agent workspace", async (t) => {
  const originalConnect = Reflect.get(Client.prototype, "connect");
  const originalListTools = Reflect.get(Client.prototype, "listTools");
  const originalCallTool = Reflect.get(Client.prototype, "callTool");
  const originalClose = Reflect.get(Client.prototype, "close");
  t.after(() => {
    Reflect.set(Client.prototype, "connect", originalConnect);
    Reflect.set(Client.prototype, "listTools", originalListTools);
    Reflect.set(Client.prototype, "callTool", originalCallTool);
    Reflect.set(Client.prototype, "close", originalClose);
  });

  Reflect.set(Client.prototype, "connect", async () => undefined);
  Reflect.set(Client.prototype, "listTools", async () => ({
    tools: [{ name: "open_database", inputSchema: { type: "object" } }],
  }));
  Reflect.set(Client.prototype, "close", async () => undefined);
  const forwarded: Record<string, unknown>[] = [];
  Reflect.set(
    Client.prototype,
    "callTool",
    async (request: { arguments: Record<string, unknown> }) => {
      forwarded.push(request.arguments);
      return { content: [{ type: "text", text: "opened" }] };
    },
  );

  const handlers = new Map<string, Handler>();
  let openDatabase: {
    execute: (...args: unknown[]) => Promise<unknown>;
  } | undefined;
  const pi = {
    on(event: string, handler: Handler) {
      handlers.set(event, handler);
    },
    registerTool(tool: unknown) {
      openDatabase = tool as typeof openDatabase;
    },
  } as unknown as ExtensionAPI;
  const ctx = {
    cwd: resolve("agent-workspace"),
    ui: { setWidget() {} },
    sessionManager: { getSessionFile: () => undefined },
  };

  idaMcp(pi);
  requireHandler(handlers, "session_start")({}, ctx);
  await requireHandler(handlers, "input")({}, ctx);
  assert.ok(openDatabase);

  const relativeArgs = { path: "tests/crackme03.elf" };
  const absolutePath = resolve(ctx.cwd, "binary.elf");
  await openDatabase.execute("call-1", relativeArgs, undefined, undefined, ctx);
  await openDatabase.execute(
    "call-2",
    { path: absolutePath },
    undefined,
    undefined,
    ctx,
  );
  await openDatabase.execute(
    "call-3",
    { path: "~/binary.elf" },
    undefined,
    undefined,
    ctx,
  );

  assert.deepEqual(forwarded, [
    { path: resolve(ctx.cwd, "tests/crackme03.elf") },
    { path: absolutePath },
    { path: "~/binary.elf" },
  ]);
  assert.deepEqual(relativeArgs, { path: "tests/crackme03.elf" });
  await requireHandler(handlers, "session_shutdown")({}, ctx);
});
