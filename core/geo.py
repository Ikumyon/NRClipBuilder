import math

EARTH_RADIUS = 6378137.0

def latlon_to_mercator(lat: float, lon: float) -> tuple[float, float]:
    """Convert lat/lon (degrees) to Web Mercator (EPSG:3857) meters."""
    x = math.radians(lon) * EARTH_RADIUS
    y = math.log(math.tan(math.radians(lat) / 2.0 + math.pi / 4.0)) * EARTH_RADIUS
    return x, y

def merc_y_to_lat_rad(merc_y: float) -> float:
    """Inverse Mercator Y to latitude in radians."""
    return math.atan(math.sinh(merc_y / EARTH_RADIUS))

def inverse_geodesic(lat1: float, lon1: float, lat2: float, lon2: float) -> tuple[float, float]:
    """Compute the ground-meter offset (dx, dy) from center to node."""
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    
    # Spherical distance (haversine)
    a = math.sin(dlat / 2.0) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2.0) ** 2
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    dist = EARTH_RADIUS * c
    
    if dist < 0.001:
        return 0.0, 0.0
        
    y_comp = math.cos(lat2) * math.sin(dlon)
    x_comp = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    bearing = math.atan2(y_comp, x_comp)
    
    return dist * math.sin(bearing), dist * math.cos(bearing)
