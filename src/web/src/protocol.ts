import type { Command, MapEvent, QtBridge } from "./types.ts";

/** Each effect owns its subscription; late Qt callbacks cannot revive it. */
export function connectBackend(
  onCommand: (command: Command) => void,
  onConnected: () => void,
) {
  let bridge: QtBridge | undefined;
  let disposed = false;
  const receive = (raw: string) => {
    if (!disposed) onCommand(JSON.parse(raw) as Command);
  };
  if (window.qt) {
    if (!window.QWebChannel) throw new Error("Qt WebChannel script is missing");
    new window.QWebChannel(window.qt.webChannelTransport, (channel) => {
      if (disposed) return;
      bridge = channel.objects.bridge;
      bridge.command.connect(receive);
      onConnected();
      bridge.receive(JSON.stringify({ type: "ready" } satisfies MapEvent));
    });
  } else if (window.NRCLIP_PREVIEW) {
    onCommand({ type: "update", payload: window.NRCLIP_PREVIEW });
  }
  return {
    emit(event: MapEvent) {
      if (!disposed) bridge?.receive(JSON.stringify(event));
    },
    dispose() {
      disposed = true;
      bridge?.command.disconnect(receive);
      bridge = undefined;
    },
  };
}
