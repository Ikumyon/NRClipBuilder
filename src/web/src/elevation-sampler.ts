import { fetchElevation } from "./elevation.ts";
import type { Position } from "./types.ts";

export class ElevationSampleError extends Error {
  readonly original: unknown;
  readonly timedOut: boolean;
  readonly point: Position;
  constructor(original: unknown, timedOut: boolean, point: Position) {
    super(String(original));
    this.original = original;
    this.timedOut = timedOut;
    this.point = point;
  }
}

/** Coordinate requests belong to the measurement, not to cursor movement. */
export function createElevationSampler() {
  const heights = new Map<string, Promise<number>>();
  const requests = new Set<AbortController>();
  let disposed = false;
  return {
    sample(point: Position): Promise<number> {
      const [longitude, latitude] = point;
      const key = `${longitude.toFixed(7)},${latitude.toFixed(7)}`;
      const cached = heights.get(key);
      if (cached) return cached;
      if (disposed)
        return Promise.reject(new Error("Disposed elevation sampler"));
      const controller = new AbortController();
      requests.add(controller);
      let timedOut = false;
      const timer = setTimeout(() => {
        timedOut = true;
        controller.abort();
      }, 5000);
      const pending = fetchElevation(longitude, latitude, controller.signal)
        .catch((error: unknown) => {
          heights.delete(key);
          throw new ElevationSampleError(error, timedOut, [
            longitude,
            latitude,
          ]);
        })
        .finally(() => {
          clearTimeout(timer);
          requests.delete(controller);
        });
      heights.set(key, pending);
      // Keep pending requests shared; completed old coordinates can be sampled again.
      if (heights.size > 1024) heights.delete(heights.keys().next().value!);
      return pending;
    },
    destroy() {
      disposed = true;
      for (const request of requests) request.abort();
      heights.clear();
    },
  };
}
