import type { Feature, FeatureCollection, Geometry, Position } from "geojson";
export type { Geometry, Position };

export type Properties = Record<string, unknown>;
export type MapFeature = Feature<Geometry, Properties> & { id: string };
export type Collection = FeatureCollection<Geometry, Properties> & {
  features: MapFeature[];
};
export type BBox = [number, number, number, number];
export type Structure = "ground" | "bridge" | "tunnel";
export type Strings = Record<string, string | string[]>;
export interface Draft {
  points: Position[];
  segments: { structure: Structure; coordinates: Position[] }[];
}
export interface View {
  lat: number;
  lng: number;
  zoom: number;
}
export interface LayerPayload {
  id: string;
  name: string;
  kind: "data" | "tile" | "history";
  visible: boolean;
  opacity: number;
  revision: number;
  url: string;
  attribution: string;
  geojson: string | Collection | null;
}
export interface MapPayload {
  layers: LayerPayload[];
  selected: string | null;
  bbox: BBox | null;
  history: { id: string; name: string; bbox: BBox }[];
}
export type Command =
  | { type: "update"; payload: MapPayload }
  | { type: "draw"; active: boolean }
  | { type: "structure"; value: Structure }
  | { type: "draft"; draft: Draft }
  | { type: "fit"; bbox: BBox | null }
  | { type: "locale"; language: string; strings: Strings }
  | { type: "view"; view: View };
export type MapEvent =
  | { type: "ready" | "rendered" | "fetch_osm" | "finish_draft" }
  | { type: "edit"; layer_id: string; feature: MapFeature }
  | { type: "history"; id: string }
  | { type: "bbox"; bbox: BBox }
  | { type: "draft"; draft: Draft }
  | { type: "view"; view: View };
export interface ControlsState {
  strings: Strings;
  selecting: boolean;
  hint: string;
  elevation: string;
  elevationError: string;
}
export interface QtSignal {
  connect(callback: (raw: string) => void): void;
  disconnect(callback: (raw: string) => void): void;
}
export interface QtBridge {
  receive(raw: string): void;
  command: QtSignal;
}

declare global {
  interface Window {
    qt?: { webChannelTransport: unknown };
    QWebChannel?: new (
      transport: unknown,
      ready: (channel: { objects: { bridge: QtBridge } }) => void,
    ) => unknown;
    NRCLIP_PREVIEW?: MapPayload;
    nrclipMap?: {
      inspect(): {
        points: Position[];
        segments: Draft["segments"];
        featureCount: number;
      };
    };
  }
}
