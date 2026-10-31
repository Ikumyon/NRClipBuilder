import { test } from "node:test";
import assert from "node:assert/strict";
import {
  draftSegments,
  geometryVertices,
  lineStyle,
  structureOf,
} from "../src/geometry.ts";

test("all nested point, line and polygon vertices are available to snapping", () => {
  const points = geometryVertices({
    type: "GeometryCollection",
    geometries: [
      { type: "Point", coordinates: [1, 2] },
      {
        type: "MultiPoint",
        coordinates: [
          [3, 4],
          [5, 6],
        ],
      },
      {
        type: "MultiPolygon",
        coordinates: [
          [
            [
              [7, 8],
              [9, 10],
              [7, 8],
            ],
          ],
        ],
      },
    ],
  });
  assert.deepEqual(points, [
    [1, 2],
    [3, 4],
    [5, 6],
    [7, 8],
    [9, 10],
    [7, 8],
  ]);
});

test("removing or moving a draft vertex cannot leave stale segment coordinates", () => {
  const points = [
    [0, 0],
    [1, 1],
    [2, 2],
    [3, 3],
  ];
  points.splice(1, 1);
  points[1] = [2, 4];
  assert.deepEqual(draftSegments(points, ["bridge", "tunnel"]), [
    {
      structure: "bridge",
      coordinates: [
        [0, 0],
        [2, 4],
      ],
    },
    {
      structure: "tunnel",
      coordinates: [
        [2, 4],
        [3, 3],
      ],
    },
  ]);
});

test("structure precedence and line styles distinguish tunnels, bridges and history", () => {
  assert.equal(structureOf({ layer: "", bridge: true }), "bridge");
  assert.equal(structureOf({ layer: "invalid", tunnel: "1" }), "tunnel");
  assert.equal(structureOf({ layer: 0, bridge: "yes" }), "ground");
  assert.equal(lineStyle({ tunnel: true }).dashArray, "5 8");
  assert.equal(lineStyle({ bridge: true }).weight, 7);
  assert.notEqual(lineStyle({}, true).color, lineStyle({}).color);
});
