from .handlers.request_handler import RequestHandler
from .handlers.network_handler import NetworkHandler
from .handlers.vehicle_handler import VehicleHandler
from .handlers.trip_handler import TripHandler
from .handlers.payload_parser import PayloadParser
from .structure.node import Node
import copy
import multiprocessing
import sys
from multiprocessing import Pool
import time
import logging

class OnlineRTVSolver:

    def __init__(self,server_url,SHAREABLE_COST_FACTOR=10,RTV_TIMEOUT=30, LARGEST_TSP = 8, MAX_CARDINALITY = 8, ):
        self.ILP_SOLVER_TIMEOUT = 120 # seconds
        self.RTV_TIMEOUT = RTV_TIMEOUT #seconds
        self.PENALTY = 1000000 # penalty for not serving a trip
        self.SHAREABLE_COST_FACTOR = SHAREABLE_COST_FACTOR
        self.MAX_CARDINALITY = MAX_CARDINALITY
        self.MAX_THREAD_CNT = 64
        self.REBALANCING = False
        self.RH_FACTOR = 1
        self.DWELL_PICKUP = 180
        self.DWELL_ALIGHT = 60
        self.LARGEST_TSP = LARGEST_TSP
        self.server_url = server_url
        if sys.platform == "darwin":
            multiprocessing.set_start_method("fork")

    def check_feasibility(self, payload):
        NetworkHandler.init(True, self.server_url)
        feasible_time_slots = []
        request = payload["requests"][0]
        origin = Node(request["pickup_pt"]["lat"],request["pickup_pt"]["lon"])
        destination = Node(request["dropoff_pt"]["lat"],request["dropoff_pt"]["lon"])
        request_travel_time = NetworkHandler.travel_time(origin,destination)
        for time_window in request["time_windows"]:
            request_copy = copy.deepcopy(request)
            request_copy["pickup_time_window_start"] = time_window["pickup_time_window_start"]
            request_copy["pickup_time_window_end"] = time_window["pickup_time_window_end"]
            request_copy["dropoff_time_window_start"] = time_window["dropoff_time_window_start"]
            request_copy["dropoff_time_window_end"] = time_window["dropoff_time_window_end"]
            best_cost = float("inf")
            for driver_run in payload["driver_runs"]:
                cost, _ = self.insert_request_to_driver_run(payload["depot"], driver_run, request_copy)
                if cost >= 0 and cost < best_cost:
                    best_cost = cost
            if best_cost < float("inf"):
                feasible_time_slots.append((time_window,best_cost/request_travel_time))

        return feasible_time_slots

    def resolve_pdptw_rtv(self, payload):
        updated_driver_runs, unserved_requests = self.solve_pdptw_rtv(payload)
        if len(unserved_requests) == 0:
            return updated_driver_runs
        else:
            return payload

    def solve_pdptw_rtv(self, payload):
        NetworkHandler.init(True, self.server_url)
        payload_object = PayloadParser.get_payload_object(payload)
        request_handler = RequestHandler(payload_object.requests, self.DWELL_PICKUP, self.DWELL_ALIGHT)
        temp_batch = request_handler.get_all_requests()
        batch = []
        active_requests = {}
        boarded_requests = {}
        for req in temp_batch:
            req_id = req.id
            if req_id in payload_object.boarded_requests:
                boarded_requests[req_id] = req
            else:
                if req_id in payload_object.active_requests:
                    active_requests[req_id] = req
                batch.append(req)

        iteration = 0
        boarded_trips = TripHandler.create_trip_for_picked_requests(boarded_requests,iteration)

        vehicle_handler = VehicleHandler(payload_object.depot, payload_object.driver_runs,None,LARGEST_TSP=self.LARGEST_TSP)
        vehicle_handler.add_manifest_to_vehicles(payload_object.driver_runs,boarded_requests,boarded_trips,self.DWELL_ALIGHT, self.DWELL_PICKUP)

        NetworkHandler.initialize_travel_time_matrix()
        iteration+=1
        unserved_requests = set([req.id for req in batch]) - set(active_requests.keys())
        try:
            trip_handler = TripHandler(vehicle_handler.vehicles,batch, active_requests, iteration, self.ILP_SOLVER_TIMEOUT,self.PENALTY,self.MAX_CARDINALITY,self.MAX_THREAD_CNT,self.SHAREABLE_COST_FACTOR,self.REBALANCING,self.RTV_TIMEOUT)
        except Exception as e:
            logging.error("Error in TripHandler: %s", e)
            return self.solve_pdptw_heuristic(payload)
        for vehicle_id in trip_handler.vehicle_assignment:
            vehicle = vehicle_handler.vehicles[vehicle_id]
            trips, prev_sequence = trip_handler.vehicle_assignment[vehicle_id]
            for trip in trips:
                if trip.request_id in unserved_requests:
                    unserved_requests.remove(trip.request_id)
            VehicleHandler.add_new_trips(vehicle, trips, prev_sequence=prev_sequence, add=True)

        # create updated driver runs
        updated_driver_runs = []
        for driver_run in payload_object.driver_runs:
            state = driver_run[PayloadParser.DRIVER_STATE]
            manifest = driver_run[PayloadParser.DRIVER_MANIFEST]
            current_order = state[PayloadParser.DRIVER_STATE_LOC_SERV]
            new_manifest = manifest[:current_order]
            vehicle = vehicle_handler.vehicles[state[PayloadParser.DRIVER_STATE_RUN_ID]]
            new_manifest.extend(VehicleHandler.get_manifest(vehicle,current_order))
            state[PayloadParser.DRIVER_STATE_T_LOCS] = len(new_manifest)
            new_driver_run = {PayloadParser.DRIVER_STATE:state,PayloadParser.DRIVER_MANIFEST:new_manifest}
            updated_driver_runs.append(new_driver_run)

        self.check_consistency_of_manifests(payload["driver_runs"], updated_driver_runs, unserved_requests, payload.get("requests", []))
        return updated_driver_runs, list(unserved_requests) #,trip_handler,vehicle_handler,request_handler,payload_object

    def check_consistency_of_manifests(self, prev_driver_runs, new_driver_runs, unserved_requests, new_requests):
        picked_requests = set([req["booking_id"] for req in new_requests])
        dropped_requests = set([req["booking_id"] for req in new_requests])
        for driver_run in prev_driver_runs:
            for stop in driver_run[PayloadParser.DRIVER_MANIFEST]:
                if stop["action"] == "pickup":
                    picked_requests.add(stop["booking_id"])
                else:
                    dropped_requests.add(stop["booking_id"])
        
        new_served_requests = set()
        for driver_run in new_driver_runs:
            for stop in driver_run[PayloadParser.DRIVER_MANIFEST]:
                if stop["action"] == "pickup":
                    picked_requests.remove(stop["booking_id"])
                else:
                    dropped_requests.remove(stop["booking_id"])
        for req_id in unserved_requests:
            picked_requests.remove(req_id)
            dropped_requests.remove(req_id)
        if len(picked_requests) > 0 or len(dropped_requests) > 0:
            print("Missing requests:", picked_requests, dropped_requests)
            raise Exception("Error: Some requests were removed")
        return True

    def simulate_manifest(self, current_time, driver_runs, intermediate_location=True):
        NetworkHandler.init(True, self.server_url)
        new_driver_runs = []
        for driver_run in driver_runs:
            state = driver_run[PayloadParser.DRIVER_STATE]
            current_order = state[PayloadParser.DRIVER_STATE_LOC_SERV]
            manifest = driver_run[PayloadParser.DRIVER_MANIFEST]
            next_immediate_time = state[PayloadParser.DRIVER_STATE_DT_SEC]
            next_immediate_loc = state[PayloadParser.DRIVER_STATE_LOC]
            
            if len(manifest) == current_order and next_immediate_time < current_time:
                next_immediate_time = current_time

            while len(manifest) > current_order and current_time >= manifest[current_order]["scheduled_time"]:
                next_stop = manifest[current_order]
                next_immediate_time = next_stop["scheduled_time"]
                next_immediate_loc = next_stop["loc"]

                if next_stop["action"] == "pickup":
                    next_immediate_time += self.DWELL_PICKUP
                else:
                    next_immediate_time += self.DWELL_ALIGHT
                current_order+=1
                if next_immediate_time > current_time:
                    break
                
            
            if len(manifest) > current_order and next_immediate_time < current_time and intermediate_location:
                next_immediate_node = NetworkHandler.manifest_location(next_immediate_loc)
                target_node = NetworkHandler.manifest_location(manifest[current_order]["loc"])
                next_immediate_time, next_immediate_node = NetworkHandler.get_current_location_time(next_immediate_node,target_node,next_immediate_time,current_time)
                next_immediate_loc = {"lat":next_immediate_node.lat,"lon":next_immediate_node.lon}
            state[PayloadParser.DRIVER_STATE_DT_SEC] = next_immediate_time
            state[PayloadParser.DRIVER_STATE_LOC] = next_immediate_loc
            state[PayloadParser.DRIVER_STATE_LOC_SERV] = current_order
            new_driver_runs.append({PayloadParser.DRIVER_STATE:state,PayloadParser.DRIVER_MANIFEST:manifest})
        
        self.check_consistency_of_manifests(driver_runs, new_driver_runs, [], [])
        return new_driver_runs

    def solve_pdptw_heuristic(self, payload, return_added_vmt=False):
        updated_driver_runs = copy.deepcopy(payload["driver_runs"])
        total_cost = 0
        unserved_requests = []
        for request in payload.get("requests", []):
            cheapest_vehicle = None
            cheapest_cost = float("inf")
            cheapest_vehicle_index = -1
            for vehicle_index in range(len(updated_driver_runs)):
                driver_run = updated_driver_runs[vehicle_index]
                cost, new_driver_run = self.insert_request_to_driver_run(payload["depot"], driver_run, request)
                if cost >=0 and cost < cheapest_cost:
                    cheapest_cost = cost
                    cheapest_vehicle = new_driver_run
                    cheapest_vehicle_index = vehicle_index
            if cheapest_vehicle is not None:
                updated_driver_runs[cheapest_vehicle_index] = cheapest_vehicle
                total_cost += cheapest_cost
            else:
                unserved_requests.append(request["booking_id"])
        
        self.check_consistency_of_manifests(payload["driver_runs"], updated_driver_runs, unserved_requests, payload.get("requests", []))
        if return_added_vmt:
            return updated_driver_runs, unserved_requests, total_cost
        return updated_driver_runs, unserved_requests

    def evaluate_insertion(args):
        i, j, remaining_stops, pickup_stop, dropoff_stop, start_time, start_node, load, state, objective, depot, end_time, dwell_pickup, dwell_alight = args
        new_manifest = copy.deepcopy(remaining_stops[:i] + [pickup_stop] + remaining_stops[i:j] + [dropoff_stop] + remaining_stops[j:])
        current_time = start_time
        current_node = start_node
        current_load = load
        cost = 0
        order = state[PayloadParser.DRIVER_STATE_LOC_SERV]
        index = 0
        for stop in new_manifest:
            next_node = Node(stop["loc"]["lat"], stop["loc"]["lon"], id=stop["loc"]["node_id"])
            travel_time = NetworkHandler.travel_time(current_node, next_node)
            cost += travel_time
            current_node = next_node
            current_time += travel_time
            if current_time < stop["time_window_start"]:
                current_time = stop["time_window_start"]
            stop["scheduled_time"] = current_time
            if objective == "pick_up_time" and (i == index or j == index):
                stop["time_window_end"] = current_time + 30
            if current_time > stop["time_window_end"]:
                return float("inf"), None
            if stop["action"] == "pickup":
                current_load += stop["am"]
                current_time += dwell_pickup
            else:
                current_load -= stop["am"]
                current_time += dwell_alight
            if current_load > state["am_capacity"]:
                return float("inf"), None
            order += 1
            stop["order"] = order
            index += 1

        if current_time+NetworkHandler.travel_time(current_node,depot) > end_time:
            return float("inf"), None
        if objective == "pick_up_time":
            return new_manifest[i]["scheduled_time"], new_manifest
        return cost, new_manifest

    def insert_request_to_driver_run(self, depot, driver_run, request, objective="vmt"):
        NetworkHandler.init(True, self.server_url)
        driver_run_c = copy.deepcopy(driver_run)

        depot_node_id = NetworkHandler.get_next_node_id(depot["pt"]["lat"],depot["pt"]["lon"])
        depot_node = Node(depot["pt"]["lat"],depot["pt"]["lon"], id=depot_node_id)

        pickup_stop = {'run_id': None, 'booking_id': request['booking_id'], 'order': -1, 'action': "pickup", 
            "loc": request["pickup_pt"], 'scheduled_time': -1, 
            'am': request["am"], 'wc': request["wc"], 'time_window_start': request['pickup_time_window_start'],
            'time_window_end': request['pickup_time_window_end']}
        dropoff_stop = {'run_id': None, 'booking_id': request['booking_id'], 'order': -1, 'action': "dropoff",
            "loc": request["dropoff_pt"], 'scheduled_time': -1, 
            'am': request["am"], 'wc': request["wc"], 'time_window_start': request['dropoff_time_window_start'],
            'time_window_end': request['dropoff_time_window_end']}
        
        node_id = NetworkHandler.get_next_node_id(pickup_stop["loc"]["lat"],pickup_stop["loc"]["lon"])
        pickup_stop["loc"]["node_id"] = node_id
        node_id = NetworkHandler.get_next_node_id(dropoff_stop["loc"]["lat"],dropoff_stop["loc"]["lon"])
        dropoff_stop["loc"]["node_id"] = node_id

        load = 0
        state = driver_run_c[PayloadParser.DRIVER_STATE]
        pickup_stop["run_id"] = state[PayloadParser.DRIVER_STATE_RUN_ID]
        dropoff_stop["run_id"] = state[PayloadParser.DRIVER_STATE_RUN_ID]
        manifest = driver_run_c[PayloadParser.DRIVER_MANIFEST]
        state_loc = state[PayloadParser.DRIVER_STATE_LOC]
        node_id = NetworkHandler.get_next_node_id(state_loc["lat"],state_loc["lon"])
        state_loc["node_id"] = node_id
        start_node = Node(state_loc["lat"],state_loc["lon"],id=node_id)
        start_time = state[PayloadParser.DRIVER_STATE_DT_SEC]
        completed_stops = []
        remaining_stops = []
        for stop in manifest:
            if stop["order"] <= state[PayloadParser.DRIVER_STATE_LOC_SERV]:
                if stop["action"] == "pickup":
                    load += stop["am"]
                else:
                    load -= stop["am"]
                completed_stops.append(stop)
            else:
                remaining_stops.append(stop)
                node_id = NetworkHandler.get_next_node_id(stop["loc"]["lat"],stop["loc"]["lon"])
                stop["loc"]["node_id"] = node_id
        
        NetworkHandler.initialize_travel_time_matrix()

        prev_cost = 0
        current_node = start_node
        for stop in remaining_stops:
            next_node = Node(stop["loc"]["lat"],stop["loc"]["lon"],id=stop["loc"]["node_id"])
            prev_cost += NetworkHandler.travel_time(current_node,next_node)
            current_node = next_node

        end_time = state["end_time"]
        st_th = time.time()
        pool = Pool(processes=max(1,min(len(remaining_stops), 8)))
        args_list = [(i, j, remaining_stops, pickup_stop, dropoff_stop, start_time, start_node, load, state, objective, depot_node, end_time, self.DWELL_PICKUP, self.DWELL_ALIGHT) 
                     for i in range(len(remaining_stops) + 1) 
                     for j in range(i + 1, len(remaining_stops) + 2)]
        results = pool.map(OnlineRTVSolver.evaluate_insertion, args_list)
        pool.close()
        pool.join()

        best_cost = float("inf")
        best_insertion = None
        for cost, new_manifest in results:
            if cost < best_cost:
                best_cost = cost
                best_insertion = new_manifest


        if best_insertion is None:
            return -1,None

        new_driver_run = copy.deepcopy(driver_run)
        new_driver_run[PayloadParser.DRIVER_MANIFEST] = completed_stops + best_insertion
        new_driver_run[PayloadParser.DRIVER_STATE][PayloadParser.DRIVER_STATE_T_LOCS] = len(new_driver_run[PayloadParser.DRIVER_MANIFEST])
        if objective == "pick_up_time":
            return best_cost,new_driver_run
        return best_cost-prev_cost,new_driver_run


    def serve_asap(self, payload):
        unserved_requests = []
        updated_driver_runs = copy.deepcopy(payload["driver_runs"])
        for request in payload["requests"]:
            earliest_vehicle = None
            earliest_time = float("inf")
            earliest_vehicle_index = -1
            for vehicle_index in range(len(updated_driver_runs)):
                driver_run = updated_driver_runs[vehicle_index]
                pick_up_time, new_driver_run = self.insert_request_to_driver_run(payload["depot"], driver_run, request, objective = "pick_up_time")
                if pick_up_time >=0 and pick_up_time < earliest_time:
                    earliest_time = pick_up_time
                    earliest_vehicle = new_driver_run
                    earliest_vehicle_index = vehicle_index
            if earliest_vehicle_index == -1:
                unserved_requests.append(request["booking_id"])
            else:
                updated_driver_runs[earliest_vehicle_index] = earliest_vehicle
        return updated_driver_runs, unserved_requests

    def get_stats(self, depot, manifest, travel_time_error_margin=5):
        feasible = True
        stats = {}
        stats["vmt"] = 0
        stats["pmt"] = 0
        stats["vmt/pmt"] = 0
        stats["serviced"] = 0
        stats["average_wait_time"] = 0
        stats["average_detour"] = 0
        stats["wait_time"] = []
        stats["detour"] = []

        NetworkHandler.init(True, self.server_url)
        request_stops = {}
        for driver_run in manifest:
            load = 0
            current_node = Node(depot["pt"]["lat"],depot["pt"]["lon"])
            current_time = driver_run["state"]["start_time"]
            for stop in driver_run["manifest"]:
                booking_id = stop["booking_id"]
                if booking_id not in request_stops:
                    request_stops[booking_id] = {}
                action = stop["action"]
                served_time = stop["scheduled_time"]
                next_node = Node(stop["loc"]["lat"],stop["loc"]["lon"])
                duration = NetworkHandler.travel_time(current_node,next_node)
                stats["vmt"] += duration
                current_time += duration
                if current_time > served_time + travel_time_error_margin:  # Allow a small margin of error
                    feasible = False
                    print("Error: Scheduled time is impossible ", current_time-served_time)
                    if duration > 0:
                        print(100*(current_time - served_time)/duration)
                    print("Current time: ",current_time)
                    print("Scheduled time: ",served_time)
                    print(stop)
                if current_time < served_time:
                    current_time = served_time
                
                if served_time < stop["time_window_start"]:
                    feasible = False
                    print("Error: Served before window start")
                if served_time > stop["time_window_end"]:
                    feasible = False
                    print("Error: Served after window end")
                if action == "pickup":
                    load += stop["am"]
                    current_time += 180
                    if "pick_up" in request_stops[booking_id]:
                        print("Error: Pick up already exists")
                    request_stops[booking_id]["pick_up"] = stop
                else:
                    current_time += 60
                    load -= stop["am"]
                    if "drop_off" in request_stops[booking_id]:
                        feasible = False
                        print("Error: Drop off already exists")
                    if "pick_up" not in request_stops[booking_id]:
                        feasible = False
                        print("Error: Drop off before pick up")
                    request_stops[booking_id]["drop_off"] = stop
                    stats["serviced"] += 1
                if load > driver_run["state"]["am_capacity"]:
                    feasible = False
                    print("Error: Over capacity")
                current_node = next_node

        for served in request_stops:
            if "drop_off" not in request_stops[served]:
                feasible = False
                print("Error: Request not dropped off")
            origin = Node(request_stops[served]["pick_up"]["loc"]["lat"],request_stops[served]["pick_up"]["loc"]["lon"])
            destination = Node(request_stops[served]["drop_off"]["loc"]["lat"],request_stops[served]["drop_off"]["loc"]["lon"])
            travel_time = NetworkHandler.travel_time(origin,destination)
            stats["pmt"] += travel_time
            stats["wait_time"].append(request_stops[served]["pick_up"]["scheduled_time"]-request_stops[served]["pick_up"]["time_window_start"])
            stats["detour"].append(request_stops[served]["drop_off"]["scheduled_time"]-request_stops[served]["pick_up"]["scheduled_time"]-travel_time)
        if stats["pmt"] > 0:
            stats["vmt/pmt"] = stats["vmt"] / stats["pmt"]
        if stats["serviced"] > 0:
            stats["average_wait_time"] = sum(stats["wait_time"]) / stats["serviced"]
            stats["average_detour"] = sum(stats["detour"]) / stats["serviced"]
        return feasible, stats
