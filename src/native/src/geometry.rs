use anyhow::{Result, ensure};
use geo::{BooleanOps, Coord, LineString, Polygon, Rect};
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};

pub type BBox = [f64; 4];
pub type Position = Vec<f64>;

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(tag = "type")]
pub enum Geometry {
    Point {
        coordinates: Position,
    },
    MultiPoint {
        coordinates: Vec<Position>,
    },
    LineString {
        coordinates: Vec<Position>,
    },
    MultiLineString {
        coordinates: Vec<Vec<Position>>,
    },
    Polygon {
        coordinates: Vec<Vec<Position>>,
    },
    MultiPolygon {
        coordinates: Vec<Vec<Vec<Position>>>,
    },
    GeometryCollection {
        geometries: Vec<Geometry>,
    },
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Feature {
    #[serde(rename = "type", default = "feature_type")]
    pub kind: String,
    pub id: String,
    #[serde(default)]
    pub properties: Map<String, Value>,
    pub geometry: Geometry,
}

fn feature_type() -> String {
    "Feature".into()
}

pub fn validate_bbox(b: BBox) -> Result<BBox> {
    ensure!(
        b.iter().all(|v| v.is_finite()),
        "BBox must contain finite numbers"
    );
    ensure!(
        b[0] >= -180. && b[2] <= 180. && b[1] >= -90. && b[3] <= 90.,
        "BBox is outside WGS84"
    );
    ensure!(
        b[0] < b[2] && b[1] < b[3],
        "BBox must be west,south,east,north with positive area"
    );
    Ok(b)
}

impl Geometry {
    pub fn positions(&self, visit: &mut impl FnMut(&Position)) {
        match self {
            Self::Point { coordinates } => visit(coordinates),
            Self::MultiPoint { coordinates } | Self::LineString { coordinates } => {
                coordinates.iter().for_each(visit)
            }
            Self::MultiLineString { coordinates } | Self::Polygon { coordinates } => {
                for line in coordinates {
                    for p in line {
                        visit(p);
                    }
                }
            }
            Self::MultiPolygon { coordinates } => {
                for polygon in coordinates {
                    for ring in polygon {
                        for p in ring {
                            visit(p);
                        }
                    }
                }
            }
            Self::GeometryCollection { geometries } => {
                for g in geometries {
                    g.positions(visit);
                }
            }
        }
    }

    pub fn validate(&self) -> Result<()> {
        let mut valid = true;
        self.positions(&mut |p| {
            valid &= p.len() >= 2
                && p.iter().all(|v| v.is_finite())
                && (-180. ..=180.).contains(&p[0])
                && (-90. ..=90.).contains(&p[1])
        });
        ensure!(valid, "Invalid WGS84 coordinates");
        Ok(())
    }

    pub fn bounds(&self) -> Option<BBox> {
        let mut b = [
            f64::INFINITY,
            f64::INFINITY,
            f64::NEG_INFINITY,
            f64::NEG_INFINITY,
        ];
        self.positions(&mut |p| {
            b[0] = b[0].min(p[0]);
            b[1] = b[1].min(p[1]);
            b[2] = b[2].max(p[0]);
            b[3] = b[3].max(p[1]);
        });
        b[0].is_finite().then_some(b)
    }

    pub fn lines<'a>(&'a self, lines: &mut Vec<&'a Vec<Position>>) {
        match self {
            Self::LineString { coordinates } => lines.push(coordinates),
            Self::MultiLineString { coordinates } => lines.extend(coordinates),
            Self::GeometryCollection { geometries } => {
                for g in geometries {
                    g.lines(lines);
                }
            }
            _ => {}
        }
    }

    pub fn is_line(&self) -> bool {
        let mut lines = Vec::new();
        self.lines(&mut lines);
        !lines.is_empty()
    }

    pub fn clip(&self, bbox: BBox) -> Option<Self> {
        match self {
            Self::Point { coordinates } => inside(coordinates, bbox).then(|| self.clone()),
            Self::MultiPoint { coordinates } => {
                let points: Vec<_> = coordinates
                    .iter()
                    .filter(|p| inside(p, bbox))
                    .cloned()
                    .collect();
                (!points.is_empty()).then_some(Self::MultiPoint {
                    coordinates: points,
                })
            }
            Self::LineString { coordinates } => line_geometry(clip_line(coordinates, bbox)),
            Self::MultiLineString { coordinates } => line_geometry(
                coordinates
                    .iter()
                    .flat_map(|line| clip_line(line, bbox))
                    .collect(),
            ),
            Self::GeometryCollection { geometries } => {
                let geometries: Vec<_> = geometries.iter().filter_map(|g| g.clip(bbox)).collect();
                (!geometries.is_empty()).then_some(Self::GeometryCollection { geometries })
            }
            Self::Polygon { coordinates } => clip_polygons(std::slice::from_ref(coordinates), bbox),
            Self::MultiPolygon { coordinates } => clip_polygons(coordinates, bbox),
        }
    }
}

pub fn overlaps(a: BBox, b: BBox) -> bool {
    a[0] <= b[2] && a[2] >= b[0] && a[1] <= b[3] && a[3] >= b[1]
}
fn inside(p: &[f64], b: BBox) -> bool {
    p[0] >= b[0] && p[0] <= b[2] && p[1] >= b[1] && p[1] <= b[3]
}
fn line_geometry(lines: Vec<Vec<Position>>) -> Option<Geometry> {
    match lines.len() {
        0 => None,
        1 => Some(Geometry::LineString {
            coordinates: lines.into_iter().next().unwrap(),
        }),
        _ => Some(Geometry::MultiLineString { coordinates: lines }),
    }
}

pub fn clip_line(points: &[Position], b: BBox) -> Vec<Vec<Position>> {
    let mut result = Vec::new();
    let mut current: Vec<Position> = Vec::new();
    for pair in points.windows(2) {
        let (a, z) = (&pair[0], &pair[1]);
        let (dx, dy) = (z[0] - a[0], z[1] - a[1]);
        let mut t0: f64 = 0.;
        let mut t1: f64 = 1.;
        let mut hit = true;
        for (p, q) in [
            (-dx, a[0] - b[0]),
            (dx, b[2] - a[0]),
            (-dy, a[1] - b[1]),
            (dy, b[3] - a[1]),
        ] {
            if p.abs() < 1e-15 {
                if q < 0. {
                    hit = false;
                    break;
                }
            } else {
                let t = q / p;
                if p < 0. {
                    t0 = t0.max(t);
                } else {
                    t1 = t1.min(t);
                }
            }
        }
        if hit && t0 <= t1 {
            let interpolate =
                |t: f64| -> Position { a.iter().zip(z).map(|(x, y)| x + (y - x) * t).collect() };
            let first = interpolate(t0);
            let last = interpolate(t1);
            if first == last {
                continue;
            }
            if current.last().is_some_and(|p| p != &first) && current.len() > 1 {
                result.push(std::mem::take(&mut current));
            }
            if current.is_empty() {
                current.push(first);
            }
            current.push(last);
            if t1 < 1. && current.len() > 1 {
                result.push(std::mem::take(&mut current));
            }
        } else if current.len() > 1 {
            result.push(std::mem::take(&mut current));
        }
    }
    if current.len() > 1 {
        result.push(current);
    }
    result
}

fn clip_polygons(polygons: &[Vec<Vec<Position>>], b: BBox) -> Option<Geometry> {
    let rect = Rect::new(Coord { x: b[0], y: b[1] }, Coord { x: b[2], y: b[3] }).to_polygon();
    let ring =
        |r: &Vec<Position>| LineString::from(r.iter().map(|p| (p[0], p[1])).collect::<Vec<_>>());
    let mut output = Vec::new();
    for rings in polygons {
        if rings.is_empty() {
            continue;
        }
        let polygon = Polygon::new(ring(&rings[0]), rings[1..].iter().map(ring).collect());
        for p in polygon.intersection(&rect) {
            output.push(
                std::iter::once(p.exterior())
                    .chain(p.interiors())
                    .map(|r| r.0.iter().map(|p| vec![p.x, p.y]).collect())
                    .collect(),
            );
        }
    }
    (!output.is_empty()).then_some(Geometry::MultiPolygon {
        coordinates: output,
    })
}
