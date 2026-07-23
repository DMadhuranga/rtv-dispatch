"""Visualize vehicle and passenger movements from a solved RTV output.

Reconstructs each vehicle's timed path by querying OSRM for the road geometry
between consecutive manifest stops and stamping every vertex with a real
timestamp. From those trajectories it can produce:

  * kepler.gl inputs  -- an animated Trip-layer GeoJSON of vehicle movement
                         plus a CSV of request pickups/dropoffs. Load them at
                         https://kepler.gl and press play (see README).
  * a video           -- a self-contained MP4 (or GIF) simulation of vehicles
                         moving along their routes, with a live clock, per-
                         vehicle occupancy, and waiting passengers.

Needs the OSRM server used to solve the payload.

Examples:
    python scripts/visualize.py --input outputs/sample1_output.pkl            # both
    python scripts/visualize.py --input outputs/sample1_output.pkl --mode kepler
    python scripts/visualize.py --input outputs/sample1_output.pkl --mode video --format gif
"""
import argparse
import json
import os
import pickle
from datetime import datetime, timezone

import numpy as np
import requests as http

# Colorblind-safe categorical palette (Okabe-Ito) for vehicles.
VEHICLE_COLORS = ["#0072B2", "#D55E00", "#009E73", "#CC79A7", "#E69F00",
                  "#56B4E9", "#F0E442", "#000000"]
DWELL = {"pickup": 180, "dropoff": 60}
# Arbitrary base date so seconds-since-midnight map to real wall-clock times.
BASE_EPOCH = int(datetime(2024, 6, 3, tzinfo=timezone.utc).timestamp())


def _session_get(session, url):
    return session.get(url).json()


def route_geometry(session, server_url, a, b):
    """Road geometry a->b as ([[lon,lat],...], [segment_durations]). a,b = (lat,lon)."""
    url = (f"{server_url}route/v1/driving/{a[1]},{a[0]};{b[1]},{b[0]}"
           f"?overview=full&geometries=geojson&annotations=duration")
    r = _session_get(session, url)["routes"][0]
    coords = r["geometry"]["coordinates"]
    durs = r["legs"][0]["annotation"]["duration"]
    return coords, durs


def build_trajectories(result, server_url):
    """Return (vehicles, events, bounds).

    vehicles: list of dicts with time-sorted arrays: times, lons, lats,
              load_times, loads, and the run id.
    events:   per-request pickup/dropoff records (loc + time + action).
    """
    session = http.Session()
    depot = (result["depot"]["pt"]["lat"], result["depot"]["pt"]["lon"])

    vehicles = []
    events = []
    all_lon, all_lat = [], []

    for run in result["driver_runs"]:
        state = run["state"]
        manifest = run["manifest"]
        if not manifest:
            continue

        t_way, lon_way, lat_way = [], [], []
        load_times, loads = [state["start_time"]], [0]
        load = 0
        cur_loc = depot
        cur_time = state["start_time"]

        def push(t, lon, lat):
            # keep timestamps strictly non-decreasing
            if t_way and t < t_way[-1]:
                t = t_way[-1]
            t_way.append(t); lon_way.append(lon); lat_way.append(lat)

        push(cur_time, cur_loc[1], cur_loc[0])

        for stop in manifest:
            stop_loc = (stop["loc"]["lat"], stop["loc"]["lon"])
            coords, durs = route_geometry(session, server_url, cur_loc, stop_loc)
            travel = sum(durs)
            sched = stop["scheduled_time"]
            depart = max(cur_time, sched - travel)   # wait at current loc if early

            if depart > cur_time:                    # idle segment
                push(depart, cur_loc[1], cur_loc[0])

            t = depart
            push(t, coords[0][0], coords[0][1])
            for k, d in enumerate(durs):
                t += d
                push(t, coords[k + 1][0], coords[k + 1][1])

            arrive = t
            pax = stop["am"] + stop["wc"]
            if stop["action"] == "pickup":
                load += pax
            else:
                load -= pax
            load_times.append(max(arrive, sched))
            loads.append(load)

            leave = max(arrive, sched) + DWELL[stop["action"]]
            push(leave, stop_loc[1], stop_loc[0])     # dwell in place

            events.append({"booking_id": stop["booking_id"], "action": stop["action"],
                           "lat": stop_loc[0], "lon": stop_loc[1], "time": sched})

            cur_loc = stop_loc
            cur_time = leave

        vehicles.append({
            "id": state["run_id"],
            "times": np.array(t_way, dtype=float),
            "lons": np.array(lon_way, dtype=float),
            "lats": np.array(lat_way, dtype=float),
            "load_times": np.array(load_times, dtype=float),
            "loads": np.array(loads, dtype=float),
        })
        all_lon.extend(lon_way); all_lat.extend(lat_way)

    bounds = (min(all_lon), min(all_lat), max(all_lon), max(all_lat)) if all_lon else None
    return vehicles, events, bounds


# --------------------------------------------------------------------------- #
# kepler.gl export
# --------------------------------------------------------------------------- #
def write_kepler(vehicles, events, out_dir, name):
    trips = {"type": "FeatureCollection", "features": []}
    for v in vehicles:
        coords = [[float(lon), float(lat), 0, BASE_EPOCH + float(t)]
                  for lon, lat, t in zip(v["lons"], v["lats"], v["times"])]
        trips["features"].append({
            "type": "Feature",
            "properties": {"vehicle": int(v["id"])},
            "geometry": {"type": "LineString", "coordinates": coords},
        })
    trips_path = os.path.join(out_dir, f"{name}_vehicles_trip.geojson")
    with open(trips_path, "w") as f:
        json.dump(trips, f)

    req_path = os.path.join(out_dir, f"{name}_requests.csv")
    with open(req_path, "w") as f:
        f.write("booking_id,action,latitude,longitude,time\n")
        for e in sorted(events, key=lambda e: e["time"]):
            iso = datetime.fromtimestamp(BASE_EPOCH + e["time"], timezone.utc).isoformat()
            f.write(f"{e['booking_id']},{e['action']},{e['lat']},{e['lon']},{iso}\n")
    return trips_path, req_path


# --------------------------------------------------------------------------- #
# video
# --------------------------------------------------------------------------- #
def render_video(vehicles, events, bounds, result, out_path, seconds, fps,
                 start=None, end=None):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import animation

    t0 = start if start is not None else min(v["times"][0] for v in vehicles)
    t1 = end if end is not None else max(v["times"][-1] for v in vehicles)
    n_frames = int(seconds * fps)
    frame_times = np.linspace(t0, t1, n_frames)

    # Precompute interpolated positions and occupancy per vehicle per frame.
    pos_lon = np.array([np.interp(frame_times, v["times"], v["lons"]) for v in vehicles])
    pos_lat = np.array([np.interp(frame_times, v["times"], v["lats"]) for v in vehicles])
    occ = np.array([v["loads"][np.clip(np.searchsorted(v["load_times"], frame_times, "right") - 1, 0, None)]
                    for v in vehicles])

    # Waiting-passenger arrays (window open, not yet picked up).
    pk = [e for e in events if e["action"] == "pickup"]
    pk_lon = np.array([e["lon"] for e in pk]); pk_lat = np.array([e["lat"] for e in pk])
    pk_sched = np.array([e["time"] for e in pk])
    booking_win = {r["booking_id"]: r["pickup_time_window_start"] for r in result["requests"]}
    pk_open = np.array([booking_win.get(e["booking_id"], e["time"]) for e in pk])
    dropoff_times = np.array([e["time"] for e in events if e["action"] == "dropoff"])

    minlon, minlat, maxlon, maxlat = bounds
    mx = (maxlon - minlon) * 0.05 or 0.01
    my = (maxlat - minlat) * 0.05 or 0.01
    mean_lat = np.radians((minlat + maxlat) / 2)

    plt.rcParams["figure.facecolor"] = "white"
    fig, ax = plt.subplots(figsize=(9, 9), dpi=110)
    ax.set_xlim(minlon - mx, maxlon + mx)
    ax.set_ylim(minlat - my, maxlat + my)
    ax.set_aspect(1 / np.cos(mean_lat))
    ax.set_xticks([]); ax.set_yticks([])
    for s in ax.spines.values():
        s.set_visible(False)

    # Static background: faint union of all driven routes ("road network").
    for v in vehicles:
        ax.plot(v["lons"], v["lats"], color="#d9d9d9", lw=1.0, zorder=1)
    ax.scatter([result["depot"]["pt"]["lon"]], [result["depot"]["pt"]["lat"]],
               marker="s", s=90, color="#333333", zorder=5, label="depot")

    tail = max(2, int(fps * 0.6))   # ~0.6s motion trail
    veh_colors = [VEHICLE_COLORS[i % len(VEHICLE_COLORS)] for i in range(len(vehicles))]
    waiting = ax.scatter([], [], s=22, facecolors="none", edgecolors="#E69F00",
                         linewidths=1.2, zorder=3, label="waiting")
    trails = [ax.plot([], [], color=c, lw=2.2, alpha=0.5, zorder=4)[0] for c in veh_colors]
    dots = ax.scatter([0] * len(vehicles), [0] * len(vehicles), s=140,
                      c=veh_colors, edgecolors="white", linewidths=1.3, zorder=6)
    labels = [ax.text(0, 0, "", fontsize=8, ha="center", va="center",
                      color="white", zorder=7, fontweight="bold") for _ in vehicles]
    title = ax.set_title("", fontsize=13, fontfamily="monospace")
    ax.legend(loc="upper right", frameon=False, fontsize=9)

    def update(f):
        t = frame_times[f]
        offsets = np.column_stack([pos_lon[:, f], pos_lat[:, f]])
        dots.set_offsets(offsets)
        sizes = 120 + 60 * occ[:, f]
        dots.set_sizes(sizes)
        f0 = max(0, f - tail)
        for i, (trail, lab) in enumerate(zip(trails, labels)):
            trail.set_data(pos_lon[i, f0:f + 1], pos_lat[i, f0:f + 1])
            lab.set_position((pos_lon[i, f], pos_lat[i, f]))
            lab.set_text(str(int(occ[i, f])) if occ[i, f] > 0 else "")
        wmask = (pk_open <= t) & (t < pk_sched)
        waiting.set_offsets(np.column_stack([pk_lon[wmask], pk_lat[wmask]])
                            if wmask.any() else np.empty((0, 2)))
        clock = "%02d:%02d:%02d" % (t // 3600, (t % 3600) // 60, t % 60)
        onboard = int(occ[:, f].sum())
        done = int((dropoff_times <= t).sum())
        title.set_text(f"{clock}   onboard: {onboard:2d}   waiting: {int(wmask.sum()):2d}   "
                       f"completed: {done:3d}")
        return [dots, waiting, title, *trails, *labels]

    anim = animation.FuncAnimation(fig, update, frames=n_frames, interval=1000 / fps, blit=False)
    if out_path.endswith(".gif"):
        anim.save(out_path, writer=animation.PillowWriter(fps=fps))
    else:
        anim.save(out_path, writer=animation.FFMpegWriter(fps=fps, bitrate=2400))
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", default="outputs/sample1_output.pkl",
                        help="Solved output pickle from run_solver.py.")
    parser.add_argument("--server-url", default="http://127.0.0.1:50000/")
    parser.add_argument("--mode", default="both", choices=["kepler", "video", "both"])
    parser.add_argument("--out-dir", default="viz")
    parser.add_argument("--format", default="mp4", choices=["mp4", "gif"],
                        help="Video container.")
    parser.add_argument("--seconds", type=float, default=30.0, help="Video length (s).")
    parser.add_argument("--fps", type=int, default=20)
    parser.add_argument("--start-hour", type=float, default=None,
                        help="Clip the animation to start at this hour (e.g. 7.5).")
    parser.add_argument("--end-hour", type=float, default=None)
    args = parser.parse_args()

    server_url = args.server_url if args.server_url.endswith("/") else args.server_url + "/"
    with open(args.input, "rb") as f:
        result = pickle.load(f)

    os.makedirs(args.out_dir, exist_ok=True)
    name = os.path.splitext(os.path.basename(args.input))[0]

    print("Reconstructing timed trajectories from OSRM ...")
    vehicles, events, bounds = build_trajectories(result, server_url)
    if not vehicles:
        raise SystemExit("No vehicle movement to visualize (empty manifests).")
    print(f"  {len(vehicles)} vehicles, {len(events)} stop events")

    if args.mode in ("kepler", "both"):
        trips_path, req_path = write_kepler(vehicles, events, args.out_dir, name)
        print(f"kepler.gl: {trips_path}\n           {req_path}")

    if args.mode in ("video", "both"):
        out_path = os.path.join(args.out_dir, f"{name}.{args.format}")
        start = args.start_hour * 3600 if args.start_hour is not None else None
        end = args.end_hour * 3600 if args.end_hour is not None else None
        print(f"Rendering video -> {out_path} ...")
        render_video(vehicles, events, bounds, result, out_path,
                     args.seconds, args.fps, start, end)
        print(f"video: {out_path}")


if __name__ == "__main__":
    main()
