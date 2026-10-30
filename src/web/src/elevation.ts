import type { Strings } from "./types.ts";

type ElevationErrorCode = "http" | "invalid_json" | "no_data" | "invalid_value";

export class ElevationError extends Error {
  code: ElevationErrorCode;

  constructor(code: ElevationErrorCode, detail: string) {
    super(detail);
    this.code = code;
    this.name = "ElevationError";
  }
}

export function formatElevationError(
  error: unknown,
  timedOut: boolean,
  strings: Strings,
): string {
  const label = (key: string, defaultValue: string) =>
    typeof strings[key] === "string" ? strings[key] : defaultValue;
  let reason: string;
  if (timedOut)
    reason = label(
      "map_elevation_timeout",
      "Elevation request timed out ({seconds} s).",
    ).replace("{seconds}", "5");
  else if (error instanceof ElevationError) {
    const messages = {
      http: [
        "map_elevation_http_error",
        "The elevation server returned an HTTP error.",
      ],
      invalid_json: [
        "map_elevation_invalid_json",
        "The elevation server returned invalid JSON.",
      ],
      no_data: ["map_elevation_no_data", "No elevation data at this location."],
      invalid_value: [
        "map_elevation_invalid_value",
        "The elevation response does not contain a valid height.",
      ],
    };
    const [key, defaultValue] = messages[error.code];
    reason = label(key, defaultValue);
  } else
    reason = label(
      "map_elevation_network_error",
      "Could not communicate with the elevation server.",
    );
  const detail =
    error instanceof ElevationError
      ? error.message
      : error instanceof Error
        ? `${error.name}: ${error.message}`
        : String(error);
  return `${reason}\n${label("map_elevation_error_detail", "Original error / response")}: ${detail}`;
}

export async function fetchElevation(
  longitude: number,
  latitude: number,
  signal: AbortSignal,
  fetcher: typeof fetch = fetch,
): Promise<number> {
  const url =
    "https://cyberjapandata2.gsi.go.jp/general/dem/scripts/getelevation.php?lon=" +
    longitude.toFixed(7) +
    "&lat=" +
    latitude.toFixed(7) +
    "&outtype=JSON";
  const response = await fetcher(url, { signal });
  const body = await response.text();
  if (!response.ok)
    throw new ElevationError(
      "http",
      `HTTP ${response.status} ${response.statusText}: ${body.trim().slice(0, 500)}`,
    );
  let data: unknown;
  try {
    data = JSON.parse(body);
  } catch (error) {
    throw new ElevationError(
      "invalid_json",
      `${String(error)}\n${body.slice(0, 500)}`,
    );
  }
  const value =
    data && typeof data === "object" && "elevation" in data
      ? data.elevation
      : undefined;
  if (typeof value !== "number" || !Number.isFinite(value))
    throw new ElevationError(
      value === "-----" ? "no_data" : "invalid_value",
      body.slice(0, 500),
    );
  return value;
}
