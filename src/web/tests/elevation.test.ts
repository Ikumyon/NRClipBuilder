import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import {
  ElevationError,
  fetchElevation,
  formatElevationError,
} from "../src/elevation.ts";

function response(body: string, status = 200): typeof fetch {
  return async () => new Response(body, { status });
}

test("every supported language translates elevation failures without missing placeholders", () => {
  const root = new URL(
    "../../python/nrclip_builder/resources/localisation/",
    import.meta.url,
  );
  const keys = [
    "map_elevation_timeout",
    "map_elevation_http_error",
    "map_elevation_invalid_json",
    "map_elevation_invalid_value",
    "map_elevation_network_error",
    "map_elevation_error_detail",
    "map_elevation_no_data",
  ];
  for (const file of readdirSync(root).filter((file) =>
    file.endsWith(".json"),
  )) {
    const strings = JSON.parse(readFileSync(new URL(file, root), "utf8"));
    for (const key of keys) {
      assert.equal(typeof strings[key], "string", `${file}: ${key}`);
      assert.ok(strings[key].trim(), `${file}: ${key} is empty`);
      assert.deepEqual(
        strings[key].match(/\{[^}]*\}/g) || [],
        key === "map_elevation_timeout" ? ["{seconds}"] : [],
        `${file}: ${key} placeholders`,
      );
    }
    const timeout = formatElevationError(new Error("original"), true, strings);
    assert.ok(timeout.includes("5"), file);
    assert.ok(!timeout.includes("{seconds}"), file);
    for (const [code, key] of [
      ["http", "map_elevation_http_error"],
      ["invalid_json", "map_elevation_invalid_json"],
      ["no_data", "map_elevation_no_data"],
      ["invalid_value", "map_elevation_invalid_value"],
    ] as const) {
      assert.ok(
        formatElevationError(
          new ElevationError(code, "original"),
          false,
          strings,
        ).startsWith(strings[key]),
        `${file}: ${code}`,
      );
    }
  }
});

test("elevation accepts sea level and negative heights", async () => {
  for (const height of [0, -2.5, 123.4])
    assert.equal(
      await fetchElevation(
        139,
        35,
        new AbortController().signal,
        response(JSON.stringify({ elevation: height })),
      ),
      height,
    );
});

test("elevation errors retain HTTP status and response details", async () => {
  await assert.rejects(
    fetchElevation(
      139,
      35,
      new AbortController().signal,
      response("Service unavailable", 503),
    ),
    /HTTP 503.*Service unavailable/,
  );
});

test("missing GSI coverage is an error, never a height or zero", async () => {
  await assert.rejects(
    fetchElevation(
      0,
      0,
      new AbortController().signal,
      response('{"elevation":"-----","hsrc":"-----"}'),
    ),
    (error: unknown) =>
      error instanceof ElevationError &&
      error.code === "no_data" &&
      error.message.includes("-----"),
  );
});

test("malformed JSON and invalid elevation values retain response details", async () => {
  await assert.rejects(
    fetchElevation(
      139,
      35,
      new AbortController().signal,
      response("<html>maintenance</html>"),
    ),
    (error: unknown) =>
      error instanceof ElevationError &&
      error.code === "invalid_json" &&
      error.message.includes("maintenance"),
  );
  for (const body of ["{}", "null", '{"elevation":null}', '{"elevation":"12"}'])
    await assert.rejects(
      fetchElevation(139, 35, new AbortController().signal, response(body)),
      (error: unknown) =>
        error instanceof ElevationError && error.code === "invalid_value",
    );
});

test("Japanese error explanations are translated and keep original diagnostics", () => {
  const strings = JSON.parse(
    readFileSync(
      new URL(
        "../../python/nrclip_builder/resources/localisation/ja-jp.json",
        import.meta.url,
      ),
      "utf8",
    ),
  );
  for (const [code, expected] of [
    ["http", "標高サーバーがHTTPエラーを返しました。"],
    ["invalid_json", "標高サーバーの応答をJSONとして読み取れませんでした。"],
    ["no_data", "この地点の標高データがありません。"],
    ["invalid_value", "応答に有効な標高値が含まれていません。"],
  ] as const) {
    const text = formatElevationError(
      new ElevationError(code, "original response"),
      false,
      strings,
    );
    assert.ok(text.startsWith(expected));
    assert.ok(text.includes("エラー・応答の原文: original response"));
  }
  assert.ok(
    formatElevationError(new Error("Aborted"), true, strings).startsWith(
      "標高の取得がタイムアウトしました（5秒）。",
    ),
  );
  assert.ok(
    formatElevationError(
      new TypeError("Failed to fetch"),
      false,
      strings,
    ).startsWith("標高サーバーとの通信に失敗しました。"),
  );
});

test("network errors and cancellation propagate without hiding the reason", async () => {
  const controller = new AbortController();
  const failure = new TypeError("Failed to fetch");
  const fetcher: typeof fetch = async (_url, options) => {
    assert.equal(options?.signal, controller.signal);
    throw failure;
  };
  await assert.rejects(
    fetchElevation(139, 35, controller.signal, fetcher),
    failure,
  );
});
