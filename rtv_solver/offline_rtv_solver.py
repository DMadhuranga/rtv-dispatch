from .online_rtv_solver import OnlineRTVSolver
import logging
import os
import pickle


class OfflineRTVSolver:
    """Roll a full day of requests forward in fixed batches, solving each batch
    with the RTV assignment (or the insertion heuristic) and simulating the
    fleet forward between batches."""

    def __init__(self, server_url, SHAREABLE_COST_FACTOR=10, RTV_TIMEOUT=30, LARGEST_TSP=10, MAX_CARDINALITY=8, output_folder=None):
        self.ILP_SOLVER_TIMEOUT = 120  # seconds
        self.RTV_TIMEOUT = RTV_TIMEOUT  # seconds
        self.PENALTY = 1000000  # penalty for not serving a trip
        self.SHAREABLE_COST_FACTOR = SHAREABLE_COST_FACTOR
        self.MAX_CARDINALITY = MAX_CARDINALITY
        self.MAX_THREAD_CNT = 64
        self.REBALANCING = False
        self.RH_FACTOR = 1
        self.DWELL_PICKUP = 180
        self.DWELL_ALIGHT = 60
        self.LARGEST_TSP = LARGEST_TSP
        self.server_url = server_url
        self.output_folder = output_folder
        if output_folder is not None:
            if not os.path.exists(output_folder):
                os.makedirs(output_folder)
            log_file = os.path.join(output_folder, "rtv_solver.log")
            logging.basicConfig(filename=log_file, level=logging.DEBUG)

    def solve_pdptw(self, payload, interval, step_size, method="rtv", step_size_time=True, serve_asap=False):
        """Simulate a day of dispatching.

        Args:
            payload: dict with ``depot``, ``requests`` and ``driver_runs``.
            interval: look-ahead window (seconds) used to gather requests each batch.
            step_size: how far (seconds if ``step_size_time`` else request count) to
                advance the clock after each batch.
            method: ``"rtv"`` (RTV assignment ILP) or ``"heuristic"`` (insertion).
            step_size_time: advance the clock by ``step_size`` seconds when True,
                otherwise process ``step_size`` requests at a time.
            serve_asap: if a request is unserved by the batch solve, try to insert
                it at its earliest feasible time before giving up.

        Returns:
            (driver_runs, unserved_requests)
        """
        if method not in ("rtv", "heuristic"):
            raise ValueError("method must be 'rtv' or 'heuristic'")

        online_rtv_solver = OnlineRTVSolver(
            self.server_url,
            SHAREABLE_COST_FACTOR=self.SHAREABLE_COST_FACTOR,
            RTV_TIMEOUT=self.RTV_TIMEOUT,
            LARGEST_TSP=self.LARGEST_TSP,
            MAX_CARDINALITY=self.MAX_CARDINALITY,
        )

        # Determine the time span of the day from the request windows.
        start_time = 24 * 3600
        end_time = 0
        for request in payload["requests"]:
            if request["pickup_time_window_start"] < start_time:
                start_time = request["pickup_time_window_start"]
            if request["dropoff_time_window_end"] > end_time:
                end_time = request["dropoff_time_window_end"]

        current_time = max(0, start_time - interval)
        driver_runs = payload["driver_runs"]
        unserved_requests = []

        # Requests ordered by pickup window start (used when advancing by count).
        window_start_times = [
            (i, payload["requests"][i]["pickup_time_window_start"])
            for i in range(len(payload["requests"]))
        ]
        window_start_times.sort(key=lambda x: x[1])
        index = 0

        while current_time < end_time:
            # Select the requests to consider in the current interval.
            selected_requests = {}
            if step_size_time:
                for request in payload["requests"]:
                    if current_time <= request["pickup_time_window_start"] < current_time + interval:
                        selected_requests[request["booking_id"]] = request
            else:
                for _ in range(step_size):
                    if index < len(window_start_times):
                        request_index = window_start_times[index][0]
                        request = payload["requests"][request_index]
                        selected_requests[request["booking_id"]] = request
                        current_time = window_start_times[index][1]
                        index += 1
                    else:
                        current_time = end_time

            # Drop anything already committed to a manifest.
            for dr in driver_runs:
                for stop in dr["manifest"]:
                    if stop["booking_id"] in selected_requests:
                        del selected_requests[stop["booking_id"]]

            selected_requests = list(selected_requests.values())
            logging.debug("Current time: {0}".format(current_time))
            logging.debug("Selected requests: {0}".format(len(selected_requests)))

            if len(selected_requests) == 0:
                new_driver_runs = driver_runs
            else:
                new_payload = {
                    "depot": payload["depot"],
                    "requests": selected_requests,
                    "driver_runs": driver_runs,
                }
                if self.output_folder is not None:
                    output_file = os.path.join(self.output_folder, f"rtv_solver_{current_time}.pkl")
                    with open(output_file, "wb") as f:
                        pickle.dump(new_payload, f)

                if method == "rtv":
                    new_driver_runs, unserved_request_ids = online_rtv_solver.solve_pdptw_rtv(new_payload)
                else:  # heuristic
                    new_driver_runs, unserved_request_ids = online_rtv_solver.solve_pdptw_heuristic(new_payload)

                if serve_asap and len(unserved_request_ids) > 0:
                    new_unserved_requests = [
                        request for request in new_payload["requests"]
                        if request["booking_id"] in unserved_request_ids
                    ]
                    asap_payload = {
                        "depot": payload["depot"],
                        "requests": new_unserved_requests,
                        "driver_runs": new_driver_runs,
                    }
                    new_driver_runs_asap, asap_unserved = online_rtv_solver.serve_asap(asap_payload)
                    if len(asap_unserved) == 0:
                        new_driver_runs = new_driver_runs_asap
                        unserved_request_ids = []

                unserved_requests.extend(unserved_request_ids)

            if step_size_time:
                current_time += step_size

            # Advance the fleet to the new current time.
            driver_runs = online_rtv_solver.simulate_manifest(
                current_time, new_driver_runs, intermediate_location=False
            )

        return driver_runs, unserved_requests
