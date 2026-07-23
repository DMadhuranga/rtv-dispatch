"""Generate synthetic sample payloads for the RTV solver.

Pickup and dropoff locations are sampled uniformly at random inside a
bounding box (a city) and snapped to the OSRM road network so they are
routable. Request times are randomized over the service day. The output is
fully synthetic and contains no data from any private dataset.

The bounding box and depot default to Wilson, NC but can be pointed at any
city — supply its bounding box and a central depot location and make sure the
OSRM server covers that region.

Examples:
    # Reproduce the bundled Wilson, NC samples (sample1.pkl ... sample3.pkl)
    python scripts/generate_sample_data.py --server-url http://127.0.0.1:50000/

    # Generate samples for another city
    python scripts/generate_sample_data.py \\
        --server-url http://127.0.0.1:50000/ \\
        --prefix durham --out-dir inputs/durham \\
        --lat-min 35.94 --lat-max 36.07 --lon-min -78.98 --lon-max -78.83 \\
        --depot-lat 36.00 --depot-lon -78.90

Each output is a payload dict of {"depot", "requests", "driver_runs"} matching
the format documented in the README.
"""
import argparse
import os
import pickle
import random

import requests as http

# --- Defaults: city of Wilson, NC ---
WILSON = dict(
    lat_min=35.685, lat_max=35.765,
    lon_min=-77.955, lon_max=-77.875,
    depot_lat=35.7213, depot_lon=-77.9156,
)

# Service day and windows (seconds since midnight).
SERVICE_START = 18000   # 05:00:00 — vehicle shift start
SERVICE_END = 72000     # 20:00:00 — vehicle shift end
DEMAND_START = 21600    # 06:00:00 — earliest pickup
DEMAND_END = 68400      # 19:00:00 — latest pickup
PICKUP_WINDOW = 1800    # 30 min pickup window
DWELL_PICKUP = 180      # matches the solver default

# Default profile: (index, seed, num_requests, num_vehicles).
DEFAULT_SAMPLES = [
    (1, 1, 100, 4),
    (2, 2, 150, 4),
    (3, 3, 200, 5),
]


def make_client(server_url):
    session = http.Session()

    def snap(lat, lon):
        """Snap a coordinate to the nearest routable point."""
        url = f"{server_url}nearest/v1/driving/{lon},{lat}"
        loc = session.get(url).json()["waypoints"][0]["location"]
        return loc[1], loc[0]  # lat, lon

    def travel_time(origin, dest):
        url = f"{server_url}route/v1/driving/{origin[1]},{origin[0]};{dest[1]},{dest[0]}"
        return session.get(url).json()["routes"][0]["duration"]

    return snap, travel_time


def sample_point(rng, box, snap):
    lat = rng.uniform(box["lat_min"], box["lat_max"])
    lon = rng.uniform(box["lon_min"], box["lon_max"])
    return snap(lat, lon)


def build_payload(seed, num_requests, num_vehicles, box, snap, travel_time):
    rng = random.Random(seed)

    depot_lat, depot_lon = snap(box["depot_lat"], box["depot_lon"])
    depot = {"pt": {"lat": depot_lat, "lon": depot_lon}}

    requests = []
    for i in range(num_requests):
        pickup = sample_point(rng, box, snap)
        dropoff = sample_point(rng, box, snap)
        tt = travel_time(pickup, dropoff)

        pickup_start = rng.randint(DEMAND_START, DEMAND_END)
        pickup_end = pickup_start + PICKUP_WINDOW
        # Shift the dropoff window by the direct ride time + pickup dwell so a
        # direct trip is always feasible at the earliest pickup.
        shift = tt + DWELL_PICKUP
        requests.append({
            "booking_id": str(i + 1),
            "am": 1 if rng.random() > 0.1 else 2,
            "wc": 0 if rng.random() > 0.1 else 1,
            "pickup_pt": {"lat": pickup[0], "lon": pickup[1]},
            "dropoff_pt": {"lat": dropoff[0], "lon": dropoff[1]},
            "pickup_time_window_start": pickup_start,
            "pickup_time_window_end": pickup_end,
            "dropoff_time_window_start": round(pickup_start + shift, 1),
            "dropoff_time_window_end": round(pickup_end + shift, 1),
        })

    driver_runs = []
    for v in range(num_vehicles):
        driver_runs.append({
            "state": {
                "run_id": v,
                "start_time": SERVICE_START,
                "end_time": SERVICE_END,
                "am_capacity": 8,
                "wc_capacity": 3,
                "locations_already_serviced": 0,
                "location_dt_seconds": 0,
                "loc": {"lat": depot_lat, "lon": depot_lon},
            },
            "manifest": [],
        })

    return {"depot": depot, "requests": requests, "driver_runs": driver_runs}


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--server-url", default="http://127.0.0.1:50000/",
                        help="OSRM server URL used to snap coordinates.")
    parser.add_argument("--out-dir", default="inputs/wilson",
                        help="Directory to write the sample payloads into.")
    parser.add_argument("--prefix", default="sample",
                        help="Output filename prefix, e.g. 'sample' -> sample1.pkl.")
    # City bounding box + depot (default Wilson, NC).
    parser.add_argument("--lat-min", type=float, default=WILSON["lat_min"])
    parser.add_argument("--lat-max", type=float, default=WILSON["lat_max"])
    parser.add_argument("--lon-min", type=float, default=WILSON["lon_min"])
    parser.add_argument("--lon-max", type=float, default=WILSON["lon_max"])
    parser.add_argument("--depot-lat", type=float, default=WILSON["depot_lat"])
    parser.add_argument("--depot-lon", type=float, default=WILSON["depot_lon"])
    # Optional uniform override of the default profile.
    parser.add_argument("--num-samples", type=int, default=None,
                        help="Generate this many samples (uniform size) instead "
                             "of the built-in 100/150/200-request profile.")
    parser.add_argument("--num-requests", type=int, default=150,
                        help="Requests per sample when --num-samples is given.")
    parser.add_argument("--num-vehicles", type=int, default=4,
                        help="Vehicles per sample when --num-samples is given.")
    parser.add_argument("--seed", type=int, default=1,
                        help="Base random seed when --num-samples is given.")
    args = parser.parse_args()

    server_url = args.server_url if args.server_url.endswith("/") else args.server_url + "/"
    box = dict(lat_min=args.lat_min, lat_max=args.lat_max,
               lon_min=args.lon_min, lon_max=args.lon_max,
               depot_lat=args.depot_lat, depot_lon=args.depot_lon)

    if args.num_samples is not None:
        samples = [(i + 1, args.seed + i, args.num_requests, args.num_vehicles)
                   for i in range(args.num_samples)]
    else:
        samples = DEFAULT_SAMPLES

    snap, travel_time = make_client(server_url)
    os.makedirs(args.out_dir, exist_ok=True)

    for index, seed, num_requests, num_vehicles in samples:
        payload = build_payload(seed, num_requests, num_vehicles, box, snap, travel_time)
        out_path = os.path.join(args.out_dir, f"{args.prefix}{index}.pkl")
        with open(out_path, "wb") as f:
            pickle.dump(payload, f)
        print(f"wrote {out_path}: {len(payload['requests'])} requests, "
              f"{len(payload['driver_runs'])} vehicles")


if __name__ == "__main__":
    main()
