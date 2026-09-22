import assert from "node:assert/strict";
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

test("tool labels prefer the MCP title over the legacy annotation title", async (t) => {
  const originalConnect = Reflect.get(Client.prototype, "connect");
  const originalListTools = Reflect.get(Client.prototype, "listTools");
  const originalClose = Reflect.get(Client.prototype, "close");
  t.after(() => {
    Reflect.set(Client.prototype, "connect", originalConnect);
    Reflect.set(Client.prototype, "listTools", originalListTools);
    Reflect.set(Client.prototype, "close", originalClose);
  });

  Reflect.set(Client.prototype, "connect", async () => undefined);
  Reflect.set(Client.prototype, "listTools", async () => ({
    tools: [
      {
        name: "execute_python",
        title: "Run IDA Python analysis",
        annotations: { title: "Legacy title" },
        inputSchema: { type: "object" },
      },
      {
        name: "save_database",
        annotations: { title: "Save IDA database" },
        inputSchema: { type: "object" },
      },
      { name: "list_databases", inputSchema: { type: "object" } },
    ],
  }));
  Reflect.set(Client.prototype, "close", async () => undefined);

  const handlers = new Map<string, Handler>();
  const labels = new Map<string, unknown>();
  const pi = {
    registerFlag() {},
    getFlag() {
      return false;
    },
    on(event: string, handler: Handler) {
      handlers.set(event, handler);
    },
    registerTool(tool: { name: string; label: unknown }) {
      labels.set(tool.name, tool.label);
    },
  } as unknown as ExtensionAPI;
  const ctx = {
    ui: {
      setWidget() {},
    },
  };

  idaMcp(pi);

  requireHandler(handlers, "session_start")({}, ctx);
  await requireHandler(handlers, "input")({}, ctx);
  assert.deepEqual(
    Object.fromEntries(labels),
    {
      ida_execute_python: "Run IDA Python analysis",
      ida_save_database: "Save IDA database",
      ida_list_databases: "IDA list_databases",
    },
  );

  await requireHandler(handlers, "session_shutdown")({}, ctx);
});
