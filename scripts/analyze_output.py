"""Compute key service metrics from solved RTV outputs.

Reads the pickles written by ``scripts/run_solver.py`` (each holding the depot,
the original requests, the solved driver runs, and the unserved booking ids)
and reports fleet-level metrics: service rate, VMT, PMT, occupancy, wait time,
detour, and more. Distances come from the OSRM server, so it must be running.

Examples:
    python scripts/analyze_output.py --server-url http://127.0.0.1:50000/
    python scripts/analyze_output.py --input outputs/sample1_output.pkl --json
"""
import argparse
import glob
import json
import statistics

from rtv_solver.handlers.network_handler import NetworkHandler
from rtv_solver.structure.node import Node

METERS_PER_MILE = 1609.344


def _node(loc):
    return Node(loc["lat"], loc["lon"])


def _route(a, b):
    """Return (duration_seconds, distance_meters) for a->b from OSRM."""
    resp = NetworkHandler.get_simple_route_reponse(a, b)["routes"][0]
    return resp["duration"], resp["distance"]


def analyze(result, server_url):
    NetworkHandler.init(True, server_url)

    depot = _node(result["depot"]["pt"])
    driver_runs = result["driver_runs"]
    total_requests = len(result["requests"])
    unserved = list(result.get("unserved_requests", []))

    # --- Walk each vehicle route: distance, and occupancy per moving segment ---
    vmt_m = 0.0
    move_seconds = 0.0
    occ_time_weighted = 0.0        # sum(load * segment_duration)
    loaded_seconds = 0.0           # moving time with >=1 passenger
    deadhead_m = 0.0               # distance with 0 passengers
    shared_m = 0.0                 # distance with >=2 passengers
    max_occupancy = 0
    vehicles_used = 0
    requests_per_vehicle = []
    service_spans_h = []

    # Per-request pickup/dropoff stops, gathered from the manifests.
    pickups, dropoffs = {}, {}

    for run in driver_runs:
        manifest = run["manifest"]
        if not manifest:
            continue
        vehicles_used += 1

        served_here = set()
        current = depot
        load = 0
        first_pickup_t, last_dropoff_t = None, None
        for stop in manifest:
            nxt = _node(stop["loc"])
            dur, dist = _route(current, nxt)
            # Load below is what the vehicle carries while traveling to this stop.
            vmt_m += dist
            move_seconds += dur
            occ_time_weighted += load * dur
            if load >= 1:
                loaded_seconds += dur
            if load == 0:
                deadhead_m += dist
            if load >= 2:
                shared_m += dist
            max_occupancy = max(max_occupancy, load)

            passengers = stop["am"] + stop["wc"]
            if stop["action"] == "pickup":
                load += passengers
                pickups[stop["booking_id"]] = stop
                if first_pickup_t is None:
                    first_pickup_t = stop["scheduled_time"]
            else:
                load -= passengers
                dropoffs[stop["booking_id"]] = stop
                last_dropoff_t = stop["scheduled_time"]
            served_here.add(stop["booking_id"])
            current = nxt

        requests_per_vehicle.append(len(served_here))
        if first_pickup_t is not None and last_dropoff_t is not None:
            service_spans_h.append((last_dropoff_t - first_pickup_t) / 3600.0)

    # --- Per-request metrics: wait, ride, direct, detour, PMT ---
    pmt_m = 0.0
    waits, rides, directs, detours = [], [], [], []
    served_requests = 0
    for booking_id, p in pickups.items():
        d = dropoffs.get(booking_id)
        if d is None:
            continue  # pickup without dropoff should not happen in feasible output
        served_requests += 1
        direct_dur, direct_dist = _route(_node(p["loc"]), _node(d["loc"]))
        pmt_m += direct_dist
        waits.append(p["scheduled_time"] - p["time_window_start"])
        ride = d["scheduled_time"] - p["scheduled_time"]
        rides.append(ride)
        directs.append(direct_dur)
        detours.append(ride - direct_dur)

    def mean(xs):
        return statistics.mean(xs) if xs else 0.0

    vmt_mi = vmt_m / METERS_PER_MILE
    pmt_mi = pmt_m / METERS_PER_MILE

    return {
        "requests_total": total_requests,
        "requests_served": served_requests,
        "requests_unserved": len(unserved),
        "service_rate": served_requests / total_requests if total_requests else 0.0,

        "vehicles_total": len(driver_runs),
        "vehicles_used": vehicles_used,
        "avg_requests_per_used_vehicle": mean(requests_per_vehicle),
        "avg_service_hours_per_used_vehicle": mean(service_spans_h),

        "vmt_miles": vmt_mi,
        "pmt_miles": pmt_mi,
        "vmt_km": vmt_m / 1000.0,
        "pmt_km": pmt_m / 1000.0,
        "vmt_per_pmt": vmt_mi / pmt_mi if pmt_mi else 0.0,
        "deadhead_rate": deadhead_m / vmt_m if vmt_m else 0.0,
        "shared_distance_rate": shared_m / vmt_m if vmt_m else 0.0,

        "avg_occupancy_moving": occ_time_weighted / move_seconds if move_seconds else 0.0,
        "avg_occupancy_when_loaded": occ_time_weighted / loaded_seconds if loaded_seconds else 0.0,
        "max_occupancy": max_occupancy,

        "avg_wait_min": mean(waits) / 60.0,
        "max_wait_min": (max(waits) / 60.0) if waits else 0.0,
        "avg_ride_min": mean(rides) / 60.0,
        "avg_direct_min": mean(directs) / 60.0,
        "avg_detour_min": mean(detours) / 60.0,
    }


def format_report(name, m):
    lines = [
        f"=== {name} ===",
        "Service",
        f"  requests served / total      : {m['requests_served']} / {m['requests_total']}",
        f"  service rate                 : {m['service_rate']*100:.1f}%",
        f"  unserved                     : {m['requests_unserved']}",
        "Fleet",
        f"  vehicles used / total        : {m['vehicles_used']} / {m['vehicles_total']}",
        f"  requests per used vehicle    : {m['avg_requests_per_used_vehicle']:.1f}",
        f"  service hours per vehicle    : {m['avg_service_hours_per_used_vehicle']:.1f}",
        "Distance",
        f"  VMT                          : {m['vmt_miles']:.1f} mi ({m['vmt_km']:.1f} km)",
        f"  PMT                          : {m['pmt_miles']:.1f} mi ({m['pmt_km']:.1f} km)",
        f"  VMT / PMT                    : {m['vmt_per_pmt']:.2f}",
        f"  deadhead (empty) distance    : {m['deadhead_rate']*100:.1f}%",
        f"  shared (2+ pax) distance     : {m['shared_distance_rate']*100:.1f}%",
        "Occupancy",
        f"  avg occupancy (moving)       : {m['avg_occupancy_moving']:.2f}",
        f"  avg occupancy (when loaded)  : {m['avg_occupancy_when_loaded']:.2f}",
        f"  max occupancy                : {m['max_occupancy']}",
        "Service quality",
        f"  avg wait                     : {m['avg_wait_min']:.1f} min (max {m['max_wait_min']:.1f})",
        f"  avg in-vehicle time          : {m['avg_ride_min']:.1f} min",
        f"  avg direct time              : {m['avg_direct_min']:.1f} min",
        f"  avg detour                   : {m['avg_detour_min']:.1f} min",
    ]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--server-url", default="http://127.0.0.1:50000/",
                        help="OSRM server URL.")
    parser.add_argument("--input", default=None,
                        help="Single solved output to analyze.")
    parser.add_argument("--input-glob", default="outputs/*_output.pkl",
                        help="Glob of solved outputs (used when --input is omitted).")
    parser.add_argument("--json", action="store_true",
                        help="Emit metrics as JSON instead of a text report.")
    args = parser.parse_args()

    import os
    import pickle

    inputs = [args.input] if args.input else sorted(glob.glob(args.input_glob))
    if not inputs:
        raise SystemExit(f"No outputs matched: {args.input or args.input_glob}")

    all_metrics = {}
    for path in inputs:
        with open(path, "rb") as f:
            result = pickle.load(f)
        name = os.path.basename(path)
        metrics = analyze(result, args.server_url)
        all_metrics[name] = metrics
        if not args.json:
            print(format_report(name, metrics))
            print()

    if args.json:
        print(json.dumps(all_metrics, indent=2))


if __name__ == "__main__":
    main()
