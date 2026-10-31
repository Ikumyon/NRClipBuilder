import type {
  Draft,
  Geometry,
  Position,
  Properties,
  Structure,
} from "./types.ts";

export function geometryVertices(geometry: Geometry): Position[] {
  switch (geometry.type) {
    case "Point":
      return [geometry.coordinates];
    case "MultiPoint":
    case "LineString":
      return geometry.coordinates;
    case "Polygon":
    case "MultiLineString":
      return geometry.coordinates.flat();
    case "MultiPolygon":
      return geometry.coordinates.flat(2);
    case "GeometryCollection":
      return geometry.geometries.flatMap(geometryVertices);
  }
}

export function draftSegments(
  points: Position[],
  structures: Structure[],
): Draft["segments"] {
  return points.slice(1).map((point, index) => ({
    structure: structures[index],
    coordinates: [points[index], point],
  }));
}

export function structureOf(props: Properties): Structure {
  const layer = props.layer;
  if (
    layer !== undefined &&
    layer !== null &&
    String(layer).trim() !== "" &&
    Number.isInteger(Number(layer))
  ) {
    return Number(layer) < 0
      ? "tunnel"
      : Number(layer) > 0
        ? "bridge"
        : "ground";
  }
  const enabled = (value: unknown) =>
    [true, "yes", "true", "1", 1].includes(value as string);
  return enabled(props.tunnel)
    ? "tunnel"
    : enabled(props.bridge)
      ? "bridge"
      : "ground";
}

export function lineStyle(props: Properties, history = false, osm = false) {
  const structure = structureOf(props);
  return {
    color:
      structure === "tunnel"
        ? "#7950f2"
        : structure === "bridge"
          ? "#e8590c"
          : history
            ? "#d69e00"
            : osm
              ? "#4c6ef5"
              : "#1971c2",
    weight: structure === "bridge" ? 7 : history ? 6 : 4,
    opacity: structure === "tunnel" ? 0.65 : 0.9,
    dashArray: structure === "tunnel" ? "5 8" : undefined,
  };
}
