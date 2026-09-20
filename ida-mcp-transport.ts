import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { PassThrough } from "node:stream";

import { getDefaultEnvironment } from "@modelcontextprotocol/sdk/client/stdio.js";
import {
  ReadBuffer,
  serializeMessage,
} from "@modelcontextprotocol/sdk/shared/stdio.js";
import type { Transport } from "@modelcontextprotocol/sdk/shared/transport.js";
import type { JSONRPCMessage } from "@modelcontextprotocol/sdk/types.js";

interface StdioOptions {
  command: string;
  args: string[];
  cwd?: string;
  env?: Record<string, string>;
}

/**
 * Stdio for the extension's own MCP child. Unlike the SDK transport, closing
 * does not escalate to SIGTERM/SIGKILL after two/four seconds: EOF already
 * triggers Nexus lease cleanup, whose database saves can take much longer.
 * The backend owns operation/shutdown deadlines; we wait for its actual exit.
 */
export class SessionStdioTransport implements Transport {
  readonly stderr = new PassThrough();
  onclose?: Transport["onclose"];
  onerror?: Transport["onerror"];
  onmessage?: Transport["onmessage"];

  private child: ChildProcessWithoutNullStreams | undefined;
  private readonly buffer = new ReadBuffer();
  private exited: Promise<void> = Promise.resolve();
  private closing: Promise<void> | undefined;
  private started = false;
  private closed = false;

  private readonly options: StdioOptions;

  constructor(options: StdioOptions) {
    this.options = options;
  }

  async start(): Promise<void> {
    if (this.started || this.closed)
      throw new Error("Transport already started or closed");
    this.started = true;
    const child = spawn(this.options.command, this.options.args, {
      cwd: this.options.cwd,
      env: { ...getDefaultEnvironment(), ...this.options.env },
      stdio: ["pipe", "pipe", "pipe"],
      windowsHide: true,
    });
    this.child = child;
    this.exited = new Promise<void>((resolve) => {
      child.once("close", () => {
        this.child = undefined;
        resolve();
        this.finishClose();
      });
    });
    child.stderr.on("error", (error) => this.onerror?.(error));
    child.stderr.pipe(this.stderr);
    child.stdin.on("error", (error) => this.onerror?.(error));
    child.stdout.on("error", (error) => this.onerror?.(error));
    child.stdout.on("data", (chunk: Buffer) => {
      this.buffer.append(chunk);
      while (true) {
        try {
          const message = this.buffer.readMessage();
          if (message === null) break;
          this.onmessage?.(message);
        } catch (error) {
          this.onerror?.(
            error instanceof Error ? error : new Error(String(error)),
          );
        }
      }
    });
    await new Promise<void>((resolve, reject) => {
      child.once("spawn", resolve);
      child.on("error", (error) => {
        this.onerror?.(error);
        reject(error);
      });
    });
  }

  async send(message: JSONRPCMessage): Promise<void> {
    const child = this.child;
    if (!child || this.closed || this.closing)
      throw new Error("Transport is not connected");
    await new Promise<void>((resolve, reject) => {
      child.stdin.write(serializeMessage(message), (error) =>
        error ? reject(error) : resolve(),
      );
    });
  }

  close(): Promise<void> {
    if (this.closing) return this.closing;
    if (!this.child) {
      this.finishClose();
      this.closing = Promise.resolve();
    } else {
      this.child.stdin.end();
      this.closing = this.exited;
    }
    return this.closing;
  }

  private finishClose(): void {
    if (this.closed) return;
    this.closed = true;
    this.buffer.clear();
    this.stderr.end();
    this.onclose?.();
  }
}
