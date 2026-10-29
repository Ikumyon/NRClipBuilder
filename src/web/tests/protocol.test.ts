import { test } from "node:test";
import assert from "node:assert/strict";
import { connectBackend } from "../src/protocol.ts";
import type { Command, MapPayload, QtBridge } from "../src/types.ts";

const empty: MapPayload = {
  layers: [],
  selected: null,
  bbox: null,
  history: [],
};

test("Qt bridge sends structured events and disconnects its subscription", () => {
  const listeners = new Set<(raw: string) => void>();
  const sent: string[] = [];
  const received: Command[] = [];
  let connected = false;
  const bridge: QtBridge = {
    receive: (raw) => sent.push(raw),
    command: {
      connect: (callback) => {
        listeners.add(callback);
      },
      disconnect: (callback) => {
        listeners.delete(callback);
      },
    },
  };
  globalThis.window = {
    qt: { webChannelTransport: {} },
    QWebChannel: class {
      constructor(
        _transport: unknown,
        callback: (channel: { objects: { bridge: QtBridge } }) => void,
      ) {
        callback({ objects: { bridge } });
      }
    },
  } as Window & typeof globalThis;
  const backend = connectBackend(
    (command) => received.push(command),
    () => {
      connected = true;
    },
  );
  assert.ok(connected);
  assert.deepEqual(JSON.parse(sent[0]), { type: "ready" });
  for (const listener of listeners)
    listener(JSON.stringify({ type: "update", payload: empty }));
  assert.deepEqual(received, [{ type: "update", payload: empty }]);
  backend.emit({ type: "bbox", bbox: [139, 35, 140, 36] });
  assert.equal(JSON.parse(sent[1]).type, "bbox");
  backend.dispose();
  assert.equal(listeners.size, 0);
  backend.emit({ type: "fetch_osm" });
  assert.equal(sent.length, 2);
});

test("a Qt callback arriving after unmount cannot reopen the channel", () => {
  let complete:
    ((channel: { objects: { bridge: QtBridge } }) => void) | undefined;
  globalThis.window = {
    qt: { webChannelTransport: {} },
    QWebChannel: class {
      constructor(_transport: unknown, callback: typeof complete) {
        complete = callback;
      }
    },
  } as Window & typeof globalThis;
  const backend = connectBackend(
    () => assert.fail("unexpected command"),
    () => assert.fail("unexpected connection"),
  );
  backend.dispose();
  complete!({
    objects: {
      bridge: {
        receive: () => assert.fail("unexpected send"),
        command: {
          connect: () => assert.fail("unexpected subscription"),
          disconnect: () => {},
        },
      },
    },
  });
});

test("standalone browser preview uses the same map commands", () => {
  globalThis.window = { NRCLIP_PREVIEW: empty } as Window & typeof globalThis;
  const commands: Command[] = [];
  const backend = connectBackend(
    (command) => commands.push(command),
    () => assert.fail("preview is not a Qt connection"),
  );
  assert.deepEqual(commands, [{ type: "update", payload: empty }]);
  backend.dispose();
});

test("missing Qt script reports a setup error", () => {
  globalThis.window = { qt: { webChannelTransport: {} } } as Window &
    typeof globalThis;
  assert.throws(
    () =>
      connectBackend(
        () => {},
        () => {},
      ),
    /WebChannel script is missing/,
  );
});
