//! Reduce route control points in ground metres, with a two-sided shape check.
//! A radius match is only a pruning priority; it never exempts a curve from validation.
use crate::{
    dataset::Cancellation,
    geo::{inverse_geodesic, latlon_to_mercator, merc_y_to_lat_rad},
    hobby,
};
use anyhow::{Result, ensure};

type Point = (f64, f64);
const RADIUS: f64 = 6_378_137.;

#[derive(Clone, Copy)]
pub struct Projection {
    pub center: Point,
}
impl Projection {
    pub fn new(points: impl Iterator<Item = Point>) -> Self {
        let mut bounds = [
            f64::INFINITY,
            f64::INFINITY,
            f64::NEG_INFINITY,
            f64::NEG_INFINITY,
        ];
        for (x, y) in points {
            bounds[0] = bounds[0].min(x);
            bounds[1] = bounds[1].min(y);
            bounds[2] = bounds[2].max(x);
            bounds[3] = bounds[3].max(y);
        }
        Self {
            center: ((bounds[0] + bounds[2]) / 2., (bounds[1] + bounds[3]) / 2.),
        }
    }
    pub fn ground(&self, point: Point) -> Point {
        inverse_geodesic(
            merc_y_to_lat_rad(self.center.1),
            self.center.0 / RADIUS,
            merc_y_to_lat_rad(point.1),
            point.0 / RADIUS,
        )
    }
    pub fn mercator(&self, point: Point) -> Point {
        let distance = point.0.hypot(point.1);
        if distance < 1e-9 {
            return self.center;
        }
        let lat = merc_y_to_lat_rad(self.center.1);
        let lon = self.center.0 / RADIUS;
        let angle = distance / RADIUS;
        let bearing = point.0.atan2(point.1);
        let target_lat = (lat.sin() * angle.cos() + lat.cos() * angle.sin() * bearing.cos())
            .clamp(-1., 1.)
            .asin();
        let target_lon = lon
            + (bearing.sin() * angle.sin() * lat.cos())
                .atan2(angle.cos() - lat.sin() * target_lat.sin());
        latlon_to_mercator(target_lat.to_degrees(), target_lon.to_degrees())
    }
}

#[derive(Clone)]
struct Sample {
    point: Point,
    original: usize,
    protected: bool,
}

fn interpolate(a: Point, b: Point, t: f64) -> Point {
    (a.0 + (b.0 - a.0) * t, a.1 + (b.1 - a.1) * t)
}
fn distance(a: Point, b: Point) -> f64 {
    (a.0 - b.0).hypot(a.1 - b.1)
}
fn segment_distance(p: Point, a: Point, b: Point) -> f64 {
    let dx = b.0 - a.0;
    let dy = b.1 - a.1;
    let denominator = dx * dx + dy * dy;
    let t = if denominator > 0. {
        ((p.0 - a.0) * dx + (p.1 - a.1) * dy) / denominator
    } else {
        0.
    };
    distance(p, interpolate(a, b, t.clamp(0., 1.)))
}

fn flatten(
    curve: &hobby::BezierSegment,
    error: f64,
    output: &mut Vec<Point>,
    depth: usize,
) -> Result<()> {
    if segment_distance(curve.c0, curve.p0, curve.p1)
        .max(segment_distance(curve.c1, curve.p0, curve.p1))
        <= error
    {
        output.push(curve.p1);
        return Ok(());
    }
    ensure!(
        depth < 32,
        "Curve subdivision could not meet the requested precision"
    );
    let a = interpolate(curve.p0, curve.c0, 0.5);
    let b = interpolate(curve.c0, curve.c1, 0.5);
    let c = interpolate(curve.c1, curve.p1, 0.5);
    let d = interpolate(a, b, 0.5);
    let e = interpolate(b, c, 0.5);
    let middle = interpolate(d, e, 0.5);
    flatten(
        &hobby::BezierSegment {
            p0: curve.p0,
            c0: a,
            c1: d,
            p1: middle,
        },
        error,
        output,
        depth + 1,
    )?;
    flatten(
        &hobby::BezierSegment {
            p0: middle,
            c0: e,
            c1: c,
            p1: curve.p1,
        },
        error,
        output,
        depth + 1,
    )
}

fn linear_interval(value: f64, delta: f64, low: f64, high: f64, interval: &mut (f64, f64)) -> bool {
    if delta.abs() < 1e-15 {
        return value >= low && value <= high;
    }
    let a = (low - value) / delta;
    let b = (high - value) / delta;
    interval.0 = interval.0.max(a.min(b));
    interval.1 = interval.1.min(a.max(b));
    interval.0 <= interval.1
}

// Exact coverage of a source line segment by the union of target-segment capsules.
// Unlike sampling a few vertices, this also detects deviations between input nodes.
fn covered(a: Point, b: Point, target: &[Point], radius: f64) -> bool {
    let delta = (b.0 - a.0, b.1 - a.1);
    let length2 = delta.0 * delta.0 + delta.1 * delta.1;
    let mut intervals: Vec<(f64, f64)> = Vec::new();
    for line in target.windows(2) {
        let (c, d) = (line[0], line[1]);
        let axis = (d.0 - c.0, d.1 - c.1);
        let axis_length = axis.0.hypot(axis.1);
        if axis_length > 1e-12 {
            let u = (axis.0 / axis_length, axis.1 / axis_length);
            let relative = (a.0 - c.0, a.1 - c.1);
            let mut interval = (0., 1.);
            if linear_interval(
                relative.0 * u.0 + relative.1 * u.1,
                delta.0 * u.0 + delta.1 * u.1,
                0.,
                axis_length,
                &mut interval,
            ) && linear_interval(
                relative.0 * -u.1 + relative.1 * u.0,
                delta.0 * -u.1 + delta.1 * u.0,
                -radius,
                radius,
                &mut interval,
            ) {
                intervals.push(interval);
            }
        }
        for center in [c, d] {
            let relative = (a.0 - center.0, a.1 - center.1);
            let constant = relative.0 * relative.0 + relative.1 * relative.1 - radius * radius;
            if length2 < 1e-20 {
                if constant <= 0. {
                    return true;
                }
                continue;
            }
            let linear = relative.0 * delta.0 + relative.1 * delta.1;
            let discriminant = linear * linear - length2 * constant;
            if discriminant >= 0. {
                let root = discriminant.sqrt();
                let interval = (
                    ((-linear - root) / length2).max(0.),
                    ((-linear + root) / length2).min(1.),
                );
                if interval.0 <= interval.1 {
                    intervals.push(interval);
                }
            }
        }
    }
    intervals.sort_by(|a, b| a.0.total_cmp(&b.0));
    let mut end: f64 = 0.;
    for (low, high) in intervals {
        if low > end + 1e-9 {
            return false;
        }
        end = end.max(high);
        if end >= 1. - 1e-9 {
            return true;
        }
    }
    false
}

pub fn within(reference: &[Point], curve: &hobby::BezierSegment, tolerance: f64) -> Result<bool> {
    let flatten_error = tolerance / 8.;
    let mut polyline = vec![curve.p0];
    flatten(curve, flatten_error, &mut polyline, 0)?;
    let allowance = tolerance - flatten_error;
    Ok(reference
        .windows(2)
        .all(|line| covered(line[0], line[1], &polyline, allowance))
        && polyline
            .windows(2)
            .all(|line| covered(line[0], line[1], reference, allowance)))
}

fn arc_lengths(samples: &[Sample]) -> Vec<f64> {
    let mut result = vec![0.];
    for pair in samples.windows(2) {
        result.push(result.last().unwrap() + distance(pair[0].point, pair[1].point));
    }
    result
}

fn failures(
    samples: &[Sample],
    keep: &[bool],
    tolerance: f64,
    max_spacing: f64,
    cancel: &Cancellation,
) -> Result<Vec<(usize, usize)>> {
    let indices: Vec<_> = (0..samples.len()).filter(|&i| keep[i]).collect();
    let points: Vec<_> = indices.iter().map(|&i| samples[i].point).collect();
    let curves = if points.len() >= 4 && distance(points[0], *points.last().unwrap()) < 1e-6 {
        let mut closed = vec![points[points.len() - 2]];
        closed.extend_from_slice(&points);
        closed.push(points[1]);
        hobby::hobby_spline(&closed)
            .into_iter()
            .skip(1)
            .take(points.len() - 1)
            .collect()
    } else {
        hobby::hobby_spline(&points)
    };
    let lengths = arc_lengths(samples);
    let mut result = Vec::new();
    for (span, curve) in indices.windows(2).zip(curves) {
        cancel.check()?;
        let (a, b) = (span[0], span[1]);
        let reference: Vec<_> = samples[a..=b].iter().map(|p| p.point).collect();
        if lengths[b] - lengths[a] > max_spacing * (1. + 1e-9)
            || !within(&reference, &curve, tolerance)?
        {
            result.push((a, b));
        }
    }
    Ok(result)
}

pub fn reduce(
    coords: &[Point],
    protected: &[bool],
    projection: Projection,
    options: &crate::import::ExportOptions,
    cancel: &Cancellation,
) -> Result<Vec<(usize, f64, f64)>> {
    let tolerance = options.spline_tolerance;
    let max_spacing = options.max_spacing;
    ensure!(
        coords.len() >= 2 && protected.len() == coords.len(),
        "Invalid route samples"
    );
    let mut samples: Vec<_> = coords
        .iter()
        .enumerate()
        .map(|(i, &p)| Sample {
            point: projection.ground(p),
            original: i,
            protected: protected[i] || i == 0 || i + 1 == coords.len(),
        })
        .collect();
    // Seed spacing once, in ground metres. Later shape fitting is allowed to remove
    // these unprotected points; there is no second, unconditional subdivision pass.
    let mut expanded = Vec::new();
    for pair in samples.windows(2) {
        expanded.push(pair[0].clone());
        let count = (distance(pair[0].point, pair[1].point) / max_spacing).ceil() as usize;
        for part in 1..count {
            expanded.push(Sample {
                point: interpolate(pair[0].point, pair[1].point, part as f64 / count as f64),
                original: usize::MAX,
                protected: false,
            });
        }
    }
    expanded.push(samples.last().unwrap().clone());
    samples = expanded;
    let mut keep: Vec<_> = samples.iter().map(|p| p.protected).collect();
    let lengths = arc_lengths(&samples);
    let mut previous = 0;
    for i in 1..samples.len() {
        if keep[i] {
            previous = i;
        } else if lengths[i] - lengths[previous] >= max_spacing {
            keep[i - 1] = true;
            previous = i - 1;
        }
    }
    for _ in 0..40 {
        let invalid = failures(&samples, &keep, tolerance, max_spacing, cancel)?;
        if invalid.is_empty() {
            break;
        }
        for (a, b) in invalid.into_iter().rev() {
            if b > a + 1 {
                keep[(a + b) / 2] = true;
            } else {
                let middle = Sample {
                    point: interpolate(samples[a].point, samples[b].point, 0.5),
                    original: usize::MAX,
                    protected: false,
                };
                samples.insert(b, middle);
                keep.insert(b, true);
            }
        }
    }
    ensure!(
        failures(&samples, &keep, tolerance, max_spacing, cancel)?.is_empty(),
        "Route cannot meet the requested shape tolerance"
    );
    let lengths = arc_lengths(&samples);
    let mut changed = true;
    while changed {
        changed = false;
        let mut candidates: Vec<_> = (1..samples.len() - 1)
            .filter(|&i| keep[i] && !samples[i].protected)
            .collect();
        if options.enable_curve_r_merging {
            candidates.sort_by_key(|&i| !same_radius(&samples, i, options.curve_r_tolerance));
        }
        for index in candidates {
            cancel.check()?;
            let previous = (0..index).rev().find(|&i| keep[i]).unwrap();
            let next = (index + 1..samples.len()).find(|&i| keep[i]).unwrap();
            if lengths[next] - lengths[previous] > max_spacing * (1. + 1e-9) {
                continue;
            }
            keep[index] = false;
            // Removing a point changes neighbouring spline tangents too: validate
            // the entire reconstructed route, rather than only the merged chord.
            if failures(&samples, &keep, tolerance, max_spacing, cancel)?.is_empty() {
                changed = true;
            } else {
                keep[index] = true;
            }
        }
    }
    Ok(samples
        .iter()
        .zip(keep)
        .filter(|(_, keep)| *keep)
        .map(|(sample, _)| {
            let (x, y) = if sample.original == usize::MAX {
                projection.mercator(sample.point)
            } else {
                coords[sample.original]
            };
            (sample.original, x, y)
        })
        .collect())
}

fn same_radius(samples: &[Sample], index: usize, tolerance: f64) -> bool {
    if index < 2 {
        return false;
    }
    let circle = |a: Point, b: Point, c: Point| {
        let cross = (b.0 - a.0) * (c.1 - b.1) - (b.1 - a.1) * (c.0 - b.0);
        (
            distance(a, b) * distance(b, c) * distance(a, c) / (2. * cross.abs()),
            cross.signum(),
        )
    };
    let (a, d) = circle(
        samples[index - 2].point,
        samples[index - 1].point,
        samples[index].point,
    );
    let (b, e) = circle(
        samples[index - 1].point,
        samples[index].point,
        samples[index + 1].point,
    );
    a.is_finite() && b.is_finite() && d == e && (a - b).abs() <= a * tolerance / 100.
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn projection_round_trip() {
        let projection = Projection {
            center: latlon_to_mercator(35., 139.),
        };
        let p = latlon_to_mercator(35.01, 139.02);
        assert!(distance(projection.mercator(projection.ground(p)), p) < 1e-6);
    }
    #[test]
    fn between_node_overshoot_is_detected() {
        let curve = hobby::BezierSegment {
            p0: (0., 0.),
            c0: (0., 20.),
            c1: (100., 20.),
            p1: (100., 0.),
        };
        assert!(!within(&[(0., 0.), (100., 0.)], &curve, 2.).unwrap());
    }
    #[test]
    fn dense_straight_line_is_reduced_without_losing_a_boundary() {
        let projection = Projection {
            center: latlon_to_mercator(35., 139.),
        };
        let coords: Vec<_> = (0..101)
            .map(|i| projection.mercator((f64::from(i) * 5., 0.)))
            .collect();
        let mut protected = vec![false; coords.len()];
        protected[40] = true;
        let result = reduce(
            &coords,
            &protected,
            projection,
            &crate::import::ExportOptions::default(),
            &Cancellation::default(),
        )
        .unwrap();
        assert_eq!(result.len(), 3);
        assert_eq!(result[1].0, 40);
    }
    #[test]
    fn arcs_s_bends_and_transition_curves_keep_shape_with_fewer_nodes() {
        let projection = Projection {
            center: latlon_to_mercator(35., 139.),
        };
        for shape in 0..3 {
            let reference: Vec<_> = (0..=200)
                .map(|i| {
                    let t = f64::from(i) / 200.;
                    match shape {
                        0 => (
                            1000. * (t * std::f64::consts::FRAC_PI_2).cos(),
                            1000. * (t * std::f64::consts::FRAC_PI_2).sin(),
                        ),
                        1 => (1000. * t, 80. * (t * std::f64::consts::TAU).sin()),
                        _ => (1000. * t, 120. * t * t * t),
                    }
                })
                .collect();
            let coords: Vec<_> = reference.iter().map(|&p| projection.mercator(p)).collect();
            let result = reduce(
                &coords,
                &vec![false; coords.len()],
                projection,
                &crate::import::ExportOptions::default(),
                &Cancellation::default(),
            )
            .unwrap();
            assert!(result.len() < 100, "shape {shape}: {} nodes", result.len());
            println!(
                "shape {shape}: {} input vertices -> {} control nodes; two-sided error <= 2 m",
                coords.len(),
                result.len()
            );
            let controls: Vec<_> = result
                .iter()
                .map(|&(_, x, y)| projection.ground((x, y)))
                .collect();
            let mut reconstructed = vec![controls[0]];
            for curve in hobby::hobby_spline(&controls) {
                flatten(&curve, 0.02, &mut reconstructed, 0).unwrap();
            }
            assert!(
                reference
                    .windows(2)
                    .all(|line| covered(line[0], line[1], &reconstructed, 2.)),
                "source outside curve, shape {shape}"
            );
            assert!(
                reconstructed
                    .windows(2)
                    .all(|line| covered(line[0], line[1], &reference, 2.)),
                "curve outside source, shape {shape}"
            );
        }
    }
    #[test]
    fn spacing_uses_ground_metres_and_cancellation_is_honoured() {
        let projection = Projection {
            center: latlon_to_mercator(60., 139.),
        };
        let coords = vec![
            projection.mercator((0., 0.)),
            projection.mercator((1000., 0.)),
        ];
        let options = crate::import::ExportOptions {
            max_spacing: 200.,
            ..Default::default()
        };
        let result = reduce(
            &coords,
            &[false, false],
            projection,
            &options,
            &Cancellation::default(),
        )
        .unwrap();
        assert!(result.len() <= 7);
        for pair in result.windows(2) {
            assert!(
                distance(
                    projection.ground((pair[0].1, pair[0].2)),
                    projection.ground((pair[1].1, pair[1].2))
                ) <= 200.00001
            );
        }
        let cancel = Cancellation::default();
        cancel.0.store(true, std::sync::atomic::Ordering::Relaxed);
        assert!(reduce(&coords, &[false, false], projection, &options, &cancel).is_err());
    }
}
