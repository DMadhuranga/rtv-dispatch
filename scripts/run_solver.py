"""Run the RTV solver over input payloads and save the solved outputs.

For each input payload the whole service day is rolled forward with
``OfflineRTVSolver`` (batch -> RTV assign -> simulate), and the result is
written as a single pickle containing everything ``scripts/analyze_output.py``
needs: the depot, the original requests, the solved driver runs, and the list
of unserved booking ids.

Examples:
    # Solve every bundled sample and write outputs/sample1_output.pkl, ...
    python scripts/run_solver.py --server-url http://127.0.0.1:50000/

    # Solve a single payload
    python scripts/run_solver.py --input inputs/wilson/sample1.pkl
"""
import argparse
import glob
import os
import pickle
import time

from rtv_solver import OfflineRTVSolver


def solve_file(solver, in_path, out_dir, interval, step_size, method):
    with open(in_path, "rb") as f:
        payload = pickle.load(f)

    start = time.time()
    driver_runs, unserved = solver.solve_pdptw(
        payload, interval=interval, step_size=step_size, method=method,
    )
    elapsed = time.time() - start

    result = {
        "depot": payload["depot"],
        "requests": payload["requests"],
        "driver_runs": driver_runs,
        "unserved_requests": unserved,
    }

    os.makedirs(out_dir, exist_ok=True)
    name = os.path.splitext(os.path.basename(in_path))[0]
    out_path = os.path.join(out_dir, f"{name}_output.pkl")
    with open(out_path, "wb") as f:
        pickle.dump(result, f)

    served = len(payload["requests"]) - len(unserved)
    print(f"{in_path} -> {out_path} | served {served}/{len(payload['requests'])}, "
          f"unserved {len(unserved)}, {elapsed:.1f}s")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--server-url", default="http://127.0.0.1:50000/",
                        help="OSRM server URL.")
    parser.add_argument("--input", default=None,
                        help="Single input payload to solve.")
    parser.add_argument("--input-glob", default="inputs/wilson/sample*.pkl",
                        help="Glob of input payloads (used when --input is omitted).")
    parser.add_argument("--out-dir", default="outputs",
                        help="Directory to write solved outputs into.")
    parser.add_argument("--method", default="rtv", choices=["rtv", "heuristic"],
                        help="Solver method.")
    parser.add_argument("--interval", type=int, default=1800,
                        help="Look-ahead window (s) for gathering each batch.")
    parser.add_argument("--step-size", type=int, default=1800,
                        help="Seconds to advance the clock after each batch.")
    args = parser.parse_args()

    inputs = [args.input] if args.input else sorted(glob.glob(args.input_glob))
    if not inputs:
        raise SystemExit(f"No inputs matched: {args.input or args.input_glob}")

    solver = OfflineRTVSolver(args.server_url)
    for in_path in inputs:
        solve_file(solver, in_path, args.out_dir, args.interval, args.step_size, args.method)


if __name__ == "__main__":
    main()
