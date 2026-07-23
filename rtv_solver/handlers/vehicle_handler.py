import logging
import pandas as pd
from rtv_solver.structure.vehicle import Vehicle
from rtv_solver.structure.vehicle_stop import VehicleStop
from rtv_solver.structure.node import Node
from rtv_solver.handlers.network_handler import NetworkHandler
from rtv_solver.handlers.payload_parser import PayloadParser
from datetime import datetime
from datetime import timedelta
import pickle

START_TIME = 'start_time'
CAPACITY = 'capacity'
START_NODE = 'node'
ID = 'id'

TYPE_PICK_UP = 0
TYPE_DROP_OFF = 1

class VehicleHandler:
    MAX_AM_CAPACITY = 0
    MAX_VC_CAPACITY = 0
    LARGEST_TSP = 10
    def __init__(self, depot, driver_runs, output_directory, LARGEST_TSP=10):
        self.vehicles = {}
        self.count = 0
        self.earliest_start_time = None
        self.load_vehicles(depot, driver_runs)
        self.output_directory = output_directory
        VehicleHandler.LARGEST_TSP = LARGEST_TSP
        logging.info('Total No of vehicles: {0}'.format(self.count))

    def save_snapshot(self):
        with open(self.output_directory+"vehicle_snapshot.p", 'wb') as snapshot_file:
            pickle.dump(self, snapshot_file)

    def load_snapshot(snapshot_directory):
        snapshot = None
        with open(snapshot_directory+"vehicle_snapshot.p", 'rb') as snapshot_file:
            snapshot = pickle.load(snapshot_file)
        return snapshot

    def load_vehicles(self, depot, driver_runs):
        # nearest_lat,nearest_lon = NetworkHandler.get_nearest_node(float(depot['lat']),float(depot['lon']))
        start_loc = depot
        for driver_run in driver_runs:
            vehicle_data = driver_run
            if "state" in vehicle_data:
                vehicle_data = driver_run["state"]
            self.count+=1
            id = int(vehicle_data['run_id'])
            am_capacity = int(vehicle_data['am_capacity'])
            wc_capacity = int(vehicle_data['wc_capacity'])
            VehicleHandler.MAX_AM_CAPACITY = max(VehicleHandler.MAX_AM_CAPACITY,am_capacity)
            VehicleHandler.MAX_VC_CAPACITY = max(VehicleHandler.MAX_AM_CAPACITY,wc_capacity)
            start_time = vehicle_data['start_time']
            end_time = vehicle_data['end_time']
            vehicle = Vehicle(id, start_loc, am_capacity, wc_capacity, start_time, end_time, start_loc)
            self.vehicles[id] = vehicle
            if self.earliest_start_time == None or self.earliest_start_time > start_time:
                self.earliest_start_time = start_time

    def read_vehicles(self, filename,starting_date, max_number_of_vehicles):
        dateparse = lambda x: datetime.strptime(x, '%H:%M:%S')
        data = pd.read_csv(filename,parse_dates=[START_TIME],date_parser=dateparse).sort_values(by = [START_TIME])
        for _, row in data.iterrows():
            self.count+=1
            capacity = min(int(row[CAPACITY]),self.MAX_CAPACITY)
            id = int(row[ID])
            start_time = starting_date + timedelta(hours=row[START_TIME].hour,minutes=row[START_TIME].minute,seconds=row[START_TIME].second)
            nearest_lat,nearest_lon = NetworkHandler.get_nearest_node(float(row.lat),float(row.lon))
            vehicle = Vehicle(id,Node(nearest_lat,nearest_lon) , capacity, start_time)
            self.vehicles[id] = vehicle
            self.MAX_CAPACITY = max(capacity,self.MAX_CAPACITY)
            if self.count == max_number_of_vehicles:
                break

    def get_current_location_time(vehicle):
        next_immediate_node = vehicle.last_node
        time_at_next_immediate_node = vehicle.time_at_last
        if len(vehicle.stop_sequence)>0:
            time_at_next_immediate_node = vehicle.time_at_next_immediate_node
            next_immediate_node = vehicle.next_immediate_node
        return next_immediate_node,time_at_next_immediate_node

    def get_state(self,driver_run,start_of_the_day):
        new_state = driver_run[PayloadParser.DRIVER_STATE]
        current_order = new_state[PayloadParser.DRIVER_STATE_LOC_SERV]
        manifest = driver_run[PayloadParser.DRIVER_MANIFEST][:current_order]
        vehicle = self.vehicles[new_state[PayloadParser.DRIVER_STATE_RUN_ID]]
        next_immediate_node,time_at_next_immediate_node = VehicleHandler.get_current_location_time(vehicle)
        new_state[PayloadParser.DRIVER_STATE_LOC] = {"lat":next_immediate_node.lat,"lon":next_immediate_node.lon}
        new_state[PayloadParser.DRIVER_STATE_DT_SEC] = time_at_next_immediate_node
        manifest.extend(VehicleHandler.get_manifest(vehicle,current_order,start_of_the_day))
        new_driver_run = {PayloadParser.DRIVER_STATE:new_state,PayloadParser.DRIVER_MANIFEST:manifest}
        return new_driver_run
    
    def get_manifest(vehicle,current_order):
        manifest = []
        last_node, time_at_last_node = VehicleHandler.get_current_location_time(vehicle)
        for vehicle_stop in vehicle.stop_sequence:
            trip = vehicle.trips[vehicle_stop.trip_id]
            node = vehicle_stop.node
            action = "dropoff"
            time_window_start = trip.earliest_arrival_time
            time_window_end = trip.latest_arrival_time
            dwell = trip.dwell_alight
            if vehicle_stop.type == TYPE_PICK_UP:
                action = "pickup"
                time_window_start = trip.pick_up_time
                time_window_end = trip.latest_pick_up_time
                dwell = trip.dwell_pickup
            current_order+=1
            stop_time = time_at_last_node + NetworkHandler.travel_time(last_node,node)
            if stop_time <= time_window_start:
                stop_time = time_window_start
            stop = {'run_id': vehicle.id, 'booking_id': trip.request_id, 'order': current_order, 'action': action, 
                        "loc": {'lat': node.lat, 'lon': node.lon, 'node_id': node.id}, 'scheduled_time': stop_time, 
                        'am': trip.am_capacity, 'wc': trip.wc_capacity, 'time_window_start': time_window_start, 
                        'time_window_end':time_window_end}
            last_node, time_at_last_node = node, stop_time + dwell
            manifest.append(stop)
        return manifest

    def add_manifest_to_vehicle(self, vehicle, driver_run, boarded_requests, boarded_trips, dwell_alight, dwell_pickup):
        state = driver_run['state']
        current_order = state['locations_already_serviced']
        vehicle.started = True
        time_at_next_immediate_node = state["location_dt_seconds"]
        next_immediate_node = NetworkHandler.manifest_location(state['loc'],node_id=NetworkHandler.get_next_node_id(state['loc']['lat'],state['loc']['lon']))

        manifest = driver_run["manifest"]
        if len(manifest) > 0:
            for stop in manifest:
                if stop["order"] > current_order:
                    break
                if stop["action"] == "pickup":
                    vehicle.am_capacity -= stop["am"]
                    vehicle.wc_capacity -= stop["wc"]
                else:
                    vehicle.am_capacity += stop["am"]
                    vehicle.wc_capacity += stop["wc"]
            
            # Adding existing route to the vehicle
            filtered_manifest = []
            for stop in manifest:
                booking_id = stop['booking_id']
                if booking_id in boarded_requests and stop['action']=="dropoff":
                    filtered_manifest.append(stop)

            for stop in filtered_manifest:
                trip_of_stop = None
                for trip in boarded_trips:
                    if trip.request_id == stop['booking_id']:
                        trip_of_stop = trip
                        break
                vehicle.trips[trip_of_stop.id] = trip_of_stop
                vehicle.picked.append(trip_of_stop.id)
                vehicle_stop = VehicleStop(trip_of_stop.id, trip_of_stop.destination, TYPE_DROP_OFF, trip_of_stop.dwell_alight)
                vehicle.stop_sequence.append(vehicle_stop)

            # if len(vehicle.stop_sequence) > 0:
            #     next_stop = vehicle.stop_sequence[0]
            #     vehicle.time_at_next = time_at_next_immediate_node + NetworkHandler.travel_time(next_immediate_node,next_stop.node)
            #     next_trip = vehicle.trips[next_stop.trip_id]
            #     if next_stop.type == TYPE_DROP_OFF and vehicle.time_at_next < next_trip.earliest_arrival_time:
            #         vehicle.time_at_next = next_trip.earliest_arrival_time

        vehicle.next_immediate_node = next_immediate_node
        vehicle.time_at_next_immediate_node = time_at_next_immediate_node
        vehicle.last_node = next_immediate_node
        vehicle.time_at_last = time_at_next_immediate_node

    def add_manifest_to_vehicles(self,driver_runs,boarded_requests,boarded_trips,dwell_alight, dwell_pickup):
        for vehicle_id in self.vehicles:
            vehicle = self.vehicles[vehicle_id]
            driver_run = None
            for run in driver_runs:
                if int(run['state']['run_id']) == vehicle_id:
                    driver_run = run
                    break
            self.add_manifest_to_vehicle(vehicle, driver_run, boarded_requests, boarded_trips, dwell_alight, dwell_pickup)

    def simulate_vehicle(self,current_time, vehicle):
        completed_stops = []
        picked_requests = []
        completed_requests = []
        if current_time >= vehicle.start_time:
            if not vehicle.started:
                vehicle.started = True
        if vehicle.rebalancing and current_time >= vehicle.time_at_next:
            next_stop = vehicle.stop_sequence.pop(0)
            vehicle.last_node = next_stop.node
            vehicle.time_at_last = vehicle.time_at_next
            vehicle.final_stop_time = vehicle.time_at_last
            vehicle.rebalancing = False
            # logging the stop
            next_stop.stop_time = vehicle.time_at_next
            next_stop.vehicle_id = vehicle.id
            completed_stops.append(next_stop)
            return completed_stops, picked_requests, completed_requests
    
        if vehicle.dwelling and vehicle.time_at_last <= current_time:
            vehicle.dwelling = False
        
        if (not vehicle.dwelling) and len(vehicle.stop_sequence) == 0:
            vehicle.time_at_last = current_time
            vehicle.time_at_next_immediate_node = current_time
            vehicle.next_immediate_node = vehicle.last_node

        while len(vehicle.stop_sequence)>0 and current_time >= vehicle.time_at_next:
            next_stop = vehicle.stop_sequence.pop(0)
            # logging the stop
            next_stop.stop_time = vehicle.time_at_next
            trip = vehicle.trips[next_stop.trip_id]
            next_stop.request_id = trip.request_id
            next_stop.vehicle_id = vehicle.id
            completed_stops.append(next_stop)

            vehicle.last_node = next_stop.node
            vehicle.time_at_last = vehicle.time_at_next + next_stop.dwell
            vehicle.final_stop_time = vehicle.time_at_last
            if vehicle.time_at_last > current_time:
                vehicle.dwelling = True
            if next_stop.type == TYPE_PICK_UP:
                vehicle.picked.append(next_stop.trip_id)
                vehicle.am_capacity = vehicle.am_capacity - trip.am_capacity
                vehicle.wc_capacity = vehicle.wc_capacity - trip.wc_capacity
                picked_trip = vehicle.trips[next_stop.trip_id]
                picked_trip.picked = True
                picked_requests.append(picked_trip.request_id)
            else:
                vehicle.picked.remove(next_stop.trip_id)
                completed_requests.append(vehicle.trips[next_stop.trip_id].request_id)
                vehicle.am_capacity = vehicle.am_capacity + trip.am_capacity
                vehicle.wc_capacity = vehicle.wc_capacity + trip.wc_capacity
                del vehicle.trips[next_stop.trip_id]
            if len(vehicle.stop_sequence) > 0:
                next_stop = vehicle.stop_sequence[0]
                vehicle.time_at_next = vehicle.time_at_last + NetworkHandler.travel_time(vehicle.last_node,next_stop.node)
                next_trip = vehicle.trips[next_stop.trip_id]
                if next_stop.type == TYPE_PICK_UP and vehicle.time_at_next < next_trip.pick_up_time:
                    vehicle.time_at_next = next_trip.pick_up_time
                if next_stop.type == TYPE_DROP_OFF and vehicle.time_at_next < next_trip.earliest_arrival_time:
                    vehicle.time_at_next = next_trip.earliest_arrival_time
        
        if len(vehicle.stop_sequence)>0:
            ori,dest = vehicle.last_node,vehicle.stop_sequence[0].node
            next_immediate_node,time_at_next_immediate_node = vehicle.last_node,vehicle.time_at_last
            if not vehicle.dwelling:
                time_at_next_immediate_node,next_immediate_node = NetworkHandler.get_current_location_time(ori,dest,vehicle.time_at_last,current_time)
            vehicle.time_at_next_immediate_node = time_at_next_immediate_node
            vehicle.next_immediate_node = next_immediate_node
            vehicle.last_node,vehicle.time_at_last = next_immediate_node, time_at_next_immediate_node
            if not vehicle.rebalancing:
                updated_trip_list = {}
                trips_to_drop_off = []
                for trip_id in vehicle.trips:
                    if trip_id in vehicle.picked:
                        updated_trip_list[trip_id] = vehicle.trips[trip_id]
                        trips_to_drop_off.append(trip_id)
                vehicle.trips = updated_trip_list
                existing_sequence = []
                nodes = [vehicle.next_immediate_node]
                for stop in vehicle.stop_sequence:
                    if stop.trip_id in updated_trip_list and stop.type == TYPE_DROP_OFF:
                        existing_sequence.append(stop)
                        nodes.append(stop.node)
                vehicle.stop_sequence = existing_sequence
                if len(vehicle.picked) > 0:
                    tt_matrix, node_indices = NetworkHandler.get_travel_time_matrix(nodes)
                    best_sequence, _, feasible,_,_ = VehicleHandler.get_exact_stop_sequence(next_immediate_node,time_at_next_immediate_node,vehicle.am_capacity,vehicle.wc_capacity,updated_trip_list,[],trips_to_drop_off,[],tt_matrix, node_indices)
                    if feasible:
                        vehicle.stop_sequence = best_sequence
                    next_stop = vehicle.stop_sequence[0]
                    vehicle.time_at_next = time_at_next_immediate_node + NetworkHandler.travel_time(next_immediate_node,next_stop.node)
                    next_trip = vehicle.trips[next_stop.trip_id]
                    if next_stop.type == TYPE_DROP_OFF and vehicle.time_at_next < next_trip.earliest_arrival_time:
                        vehicle.time_at_next = next_trip.earliest_arrival_time
        return completed_stops, picked_requests, completed_requests

    def simulate_vehicles(self,current_time):
        completed_stops = []
        picked_requests = []
        completed_requests = []
        completed_vehicles = []
        for vehicle_id in self.vehicles:
            vehicle = self.vehicles[vehicle_id]
            if len(vehicle.stop_sequence) == 0 and vehicle.end_time <= current_time:
                stop = VehicleStop(None,vehicle.depot,3,0)
                stop.stop_time = vehicle.final_stop_time+NetworkHandler.travel_time(vehicle.last_node,vehicle.depot)
                stop.vehicle_id = vehicle.id
                completed_stops.append(stop)
                completed_vehicles.append(vehicle_id)
            else:
                veh_completed_stops, veh_picked_requests, veh_completed_requests = self.simulate_vehicle(current_time, vehicle)
                completed_stops.extend(veh_completed_stops)
                picked_requests.extend(veh_picked_requests)
                completed_requests.extend(veh_completed_requests)
        for vehicle_id in completed_vehicles:
            del self.vehicles[vehicle_id]
        return completed_stops, picked_requests, completed_requests

    def get_vehicle_exact_location(self,vehicle_id):
        vehicle = self.vehicles[vehicle_id]
        next_immediate_node = vehicle.last_node
        if len(vehicle.stop_sequence)>0:
            next_immediate_node = vehicle.next_immediate_node
        return next_immediate_node

    def get_vehicle_locations(self):
        locations = {}
        for vehicle_id in self.vehicles:
            locations[int(vehicle_id)] = self.get_vehicle_exact_location(vehicle_id)
        return locations

    def add_rebalancing_trip(vehicle,destination,current_time):
        time_at_destination = current_time + NetworkHandler.travel_time(vehicle.last_node,destination)
        if VehicleHandler.can_return_to_deport(vehicle,destination,time_at_destination):
            vehicle.rebalancing = True
            vehicle.time_at_last = current_time
            vehicle.stop_sequence = [VehicleStop(None,destination,2,0)]
            vehicle.time_at_next = time_at_destination

    def can_return_to_deport(vehicle,last_node,time_at_last_node):
        if time_at_last_node+NetworkHandler.travel_time(last_node,vehicle.depot) < vehicle.end_time:
            return True
        return False
    
    def add_new_trips(vehicle, new_trips, prev_sequence = [], add=False):
        feasible = False
        added_cost = -1
        if vehicle.started:
            next_immediate_node, time_at_next_immediate_node = VehicleHandler.get_current_location_time(vehicle)
        
            sequence, cost = None, None
            trips_to_pick_up = []
            trips_to_drop_off = []
            trips = vehicle.trips.copy()
            nodes = [next_immediate_node]
            for trip_id in trips:
                trips_to_drop_off.append(trip_id)
                nodes.append(trips[trip_id].destination)
            for trip in new_trips:
                trips[trip.id] = trip
                trips_to_drop_off.append(trip.id)
                trips_to_pick_up.append(trip.id)
                nodes.append(trip.origin)
                nodes.append(trip.destination)
            existing_sequence = vehicle.stop_sequence
            if len(prev_sequence) > 0:
                existing_sequence = prev_sequence.copy()
            if vehicle.rebalancing:
                existing_sequence = []
            tt_matrix, node_indices = NetworkHandler.get_travel_time_matrix(nodes)
            sequence, cost, feasible, last_node, time_at_last_node = VehicleHandler.get_optimal_stop_sequence(next_immediate_node,time_at_next_immediate_node,vehicle.am_capacity,vehicle.wc_capacity,trips,trips_to_pick_up,trips_to_drop_off,existing_sequence,tt_matrix, node_indices)
            added_cost = cost - VehicleHandler.cost_of_serving_sequence(next_immediate_node,vehicle,tt_matrix, node_indices)
            
            if feasible:
                feasible = VehicleHandler.can_return_to_deport(vehicle,last_node,time_at_last_node)
            if feasible and add:
                vehicle.rebalancing = False
                vehicle.last_node = next_immediate_node
                vehicle.time_at_last = time_at_next_immediate_node
                for trip in new_trips:
                    vehicle.trips[trip.id] = trip
                vehicle.stop_sequence = sequence
                next_stop = vehicle.stop_sequence[0]
                travel_time = NetworkHandler.travel_time_from_matrix(vehicle.last_node,next_stop.node,tt_matrix, node_indices)
                vehicle.time_at_next = vehicle.time_at_last + travel_time
                next_trip = vehicle.trips[next_stop.trip_id]
                if next_stop.type == TYPE_PICK_UP and vehicle.time_at_next < next_trip.pick_up_time:
                    vehicle.time_at_next = next_trip.pick_up_time
                if next_stop.type == TYPE_DROP_OFF and vehicle.time_at_next < next_trip.earliest_arrival_time:
                        vehicle.time_at_next = next_trip.earliest_arrival_time
    
        return added_cost,feasible,sequence

    def cost_of_serving_sequence(next_immediate_node,vehicle,tt_matrix, node_indices):
        if vehicle.rebalancing:
            return 0
        cost = 0
        last_node = next_immediate_node
        for stop in vehicle.stop_sequence:
            cost += NetworkHandler.travel_time_from_matrix(last_node,stop.node,tt_matrix, node_indices)
            last_node = stop.node
        return cost

    def cost_of_rebalancing(vehicle,destination):
        return NetworkHandler.travel_distance(vehicle.last_node,destination)
    
    def get_optimal_stop_sequence(last_node,time_at_last_node,max_am_capacity,max_wc_capacity,trips,trips_to_pick_up,trips_to_drop_off,existing_sequence,tt_matrix, node_indices):
        if len(trips_to_pick_up)+len(trips_to_drop_off) <= VehicleHandler.LARGEST_TSP:
            return VehicleHandler.get_exact_stop_sequence(last_node,time_at_last_node,max_am_capacity,max_wc_capacity,trips,trips_to_pick_up,trips_to_drop_off,[],0,tt_matrix, node_indices)
        else:
            return VehicleHandler.get_heuristic_stop_sequence(last_node,time_at_last_node,max_am_capacity,max_wc_capacity,trips,trips_to_pick_up,trips_to_drop_off,existing_sequence,tt_matrix, node_indices)
    
    def get_exact_stop_sequence(last_node,time_at_last_node,max_am_capacity,max_wc_capacity,trips,trips_to_pick_up,trips_to_drop_off,sequence,cost,tt_matrix, node_indices):
        if len(trips_to_pick_up) == 0 and len(trips_to_drop_off) == 0:
            return sequence, cost, True, last_node, time_at_last_node
        feasible = False
        best_sequence = None
        current_lowest_cost = -1
        best_last_node, best_time_at_last_node = None, None
        # if len(trips_to_drop_off) - len(trips_to_pick_up) < max_capacity:
        for trip_id in trips_to_pick_up:
            trip = trips[trip_id]
            new_am_capacity, new_wc_capacity = max_am_capacity-trip.am_capacity,max_wc_capacity-trip.wc_capacity
            if new_am_capacity < 0 or new_wc_capacity < 0:
                continue
            travel_time = NetworkHandler.travel_time_from_matrix(last_node,trip.origin,tt_matrix, node_indices)
            time_at_pick_up = time_at_last_node + travel_time
            if time_at_pick_up < trip.pick_up_time:
                time_at_pick_up = trip.pick_up_time

            if time_at_pick_up <= trip.latest_pick_up_time:
                time_at_pick_up = time_at_pick_up + trip.dwell_pickup
            
                new_cost = cost + NetworkHandler.travel_distance(last_node,trip.origin)
                new_trips_to_pick_up = trips_to_pick_up.copy()
                new_trips_to_pick_up.remove(trip_id)
                new_sequence = sequence.copy()
                new_sequence.append(VehicleStop(trip_id,trip.origin,TYPE_PICK_UP,trip.dwell_pickup))
                new_sequence, new_cost, new_feasible, new_last_node, new_time_at_last_node = VehicleHandler.get_exact_stop_sequence(trip.origin,time_at_pick_up,new_am_capacity, new_wc_capacity,trips,new_trips_to_pick_up,trips_to_drop_off,new_sequence,new_cost,tt_matrix, node_indices)
                if new_feasible:
                    if (not feasible) or (current_lowest_cost > new_cost):
                        current_lowest_cost = new_cost
                        feasible = new_feasible
                        best_sequence = new_sequence
                        best_last_node = new_last_node
                        best_time_at_last_node = new_time_at_last_node
        
        for trip_id in trips_to_drop_off:
            if trip_id not in trips_to_pick_up:
                trip = trips[trip_id]
                new_am_capacity, new_wc_capacity = max_am_capacity+trip.am_capacity,max_wc_capacity+trip.wc_capacity
                travel_time = NetworkHandler.travel_time_from_matrix(last_node,trip.destination,tt_matrix, node_indices)
                time_at_drop_off = time_at_last_node + travel_time
                if time_at_drop_off <= trip.latest_arrival_time:
                    if time_at_drop_off < trip.earliest_arrival_time:
                        time_at_drop_off = trip.earliest_arrival_time
                    time_at_drop_off = time_at_drop_off + trip.dwell_alight
                    new_cost = cost + NetworkHandler.travel_distance(last_node,trip.destination)
                    new_trips_to_drop_off = trips_to_drop_off.copy()
                    new_trips_to_drop_off.remove(trip_id)
                    new_sequence = sequence.copy()
                    new_sequence.append(VehicleStop(trip_id,trip.destination,TYPE_DROP_OFF,trip.dwell_alight))
                    new_sequence, new_cost, new_feasible, new_last_node,new_time_at_last_node = VehicleHandler.get_exact_stop_sequence(trip.destination,time_at_drop_off,new_am_capacity, new_wc_capacity,trips,trips_to_pick_up,new_trips_to_drop_off,new_sequence,new_cost,tt_matrix, node_indices)
                    if new_feasible:
                        if (not feasible) or (current_lowest_cost > new_cost):
                            current_lowest_cost = new_cost
                            feasible = new_feasible
                            best_sequence = new_sequence
                            best_last_node = new_last_node
                            best_time_at_last_node = new_time_at_last_node

        return best_sequence, current_lowest_cost, feasible, best_last_node, best_time_at_last_node

    def get_heuristic_stop_sequence(last_node,time_at_last_node,max_am_capacity,max_wc_capacity,trips,trips_to_pick_up,trips_to_drop_off,existing_sequence,tt_matrix, node_indices):
        feasible = False
        best_sequence = None
        current_lowest_cost = -1
        current_am_capacity, current_wc_capacity = max_am_capacity,max_wc_capacity
        best_last_node, best_time_at_last_node = None, None

        trips_not_in_sequence = set(trips_to_pick_up)
        for stop in existing_sequence:
            if stop.trip_id in trips_not_in_sequence:
                trips_not_in_sequence.remove(stop.trip_id)
        if len(trips_not_in_sequence) == 0:
            new_feasible, cost, current_last_node, current_last_time  = VehicleHandler.evaluate_stop_sequence(last_node,time_at_last_node,trips,existing_sequence, max_am_capacity, max_wc_capacity, tt_matrix, node_indices)
            return existing_sequence, cost, True, current_last_node, current_last_time

        for trip_id in trips_not_in_sequence:
            new_trip = trips[trip_id]
            feasible = False
            best_sequence = None
            current_lowest_cost = -1
            for pick_up_index in range(len(existing_sequence)+1):
                for drop_off_index in range(pick_up_index+1,len(existing_sequence)+2):
                    new_sequence = existing_sequence.copy()
                    trip_id = new_trip.id
                    new_sequence.insert(pick_up_index,VehicleStop(trip_id,new_trip.origin,TYPE_PICK_UP,new_trip.dwell_pickup))
                    new_sequence.insert(drop_off_index,VehicleStop(trip_id,new_trip.destination,TYPE_DROP_OFF,new_trip.dwell_alight))
                    
                    new_feasible, cost, current_last_node, current_last_time  = VehicleHandler.evaluate_stop_sequence(last_node,time_at_last_node,trips,new_sequence, max_am_capacity, max_wc_capacity, tt_matrix, node_indices)
                    if new_feasible:
                        if (not feasible) or (current_lowest_cost > cost):
                            current_lowest_cost = cost
                            feasible = new_feasible
                            best_sequence = new_sequence
                            best_last_node = current_last_node
                            best_time_at_last_node = current_last_time
            if feasible:
                existing_sequence = best_sequence
            else:
                break
        return best_sequence, current_lowest_cost, feasible, best_last_node, best_time_at_last_node

    def evaluate_stop_sequence(last_node, time_at_last_node, trips, new_sequence, max_am_capacity, max_wc_capacity, tt_matrix, node_indices):
        cost = 0
        new_feasible = True
        current_time = time_at_last_node
        current_node = last_node
        current_am_capacity, current_wc_capacity = max_am_capacity, max_wc_capacity
        for stop in new_sequence:
            trip = trips[stop.trip_id]
            travel_time = NetworkHandler.travel_time_from_matrix(current_node,stop.node,tt_matrix, node_indices)
            cost +=  NetworkHandler.travel_distance(current_node,stop.node)
            current_time = current_time + travel_time
            if stop.type == TYPE_PICK_UP:
                if current_time < trip.pick_up_time:
                    current_time = trip.pick_up_time
                current_am_capacity -= trip.am_capacity
                current_wc_capacity -= trip.wc_capacity
                if min(current_am_capacity,current_wc_capacity) < 0 or current_time > trip.latest_pick_up_time:
                    new_feasible = False
                    break
                current_time = current_time + trip.dwell_pickup
            else:
                current_am_capacity += trip.am_capacity
                current_wc_capacity += trip.wc_capacity
                if current_time > trip.latest_arrival_time:
                    new_feasible = False
                    break
                if current_time < trip.earliest_arrival_time:
                    current_time = trip.earliest_arrival_time
                current_time = current_time + trip.dwell_alight
            current_node = stop.node
        return new_feasible, cost, current_node, current_time

    def can_serve_trips(trips,new_trip,current_sequence):
        trips_to_pick_up = []
        trips_to_drop_off = []
        nodes = []
        for trip_id in trips:
            trips_to_pick_up.append(trip_id)
            trips_to_drop_off.append(trip_id)
            nodes.append(trips[trip_id].origin)
            nodes.append(trips[trip_id].destination)
        tt_matrix, node_indices = NetworkHandler.get_travel_time_matrix(nodes)
        best_cost = None
        feasible = False
        best_sequence = None
        starting_locations = []
        # if len(current_sequence) == 0:
        current_time = new_trip.pick_up_time
        for trip_id in trips:
            if trips[trip_id].pick_up_time < current_time:
                current_time = trips[trip_id].pick_up_time
            starting_locations.append(trips[trip_id].origin)
        # else:
        #     starting_locations.append(current_sequence[0].node)
        #     starting_locations.append(trips[new_trip].origin)
        for starting_location in starting_locations:
            sequence,cost,t_feasible = None,None,None
            if 2*len(trips) <= VehicleHandler.LARGEST_TSP:
                sequence,cost,t_feasible,_,_ = VehicleHandler.get_exact_stop_sequence(starting_location,current_time,VehicleHandler.MAX_AM_CAPACITY,VehicleHandler.MAX_VC_CAPACITY,trips,trips_to_pick_up,trips_to_drop_off,[],0,tt_matrix, node_indices)
            else:
                sequence,cost,t_feasible,_,_ = VehicleHandler.get_heuristic_stop_sequence(starting_location,current_time,VehicleHandler.MAX_AM_CAPACITY,VehicleHandler.MAX_VC_CAPACITY,trips,[new_trip.id],[new_trip.id],current_sequence,tt_matrix, node_indices)
            
            if t_feasible:
                if not feasible or best_cost > cost:
                    feasible = t_feasible
                    best_cost = cost
                    best_sequence = sequence
        return feasible,best_cost,best_sequence
