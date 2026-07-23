from rtv_solver.structure.payload import Payload
from rtv_solver.handlers.network_handler import NetworkHandler

class PayloadParser:
    DRIVER_STATE = "state"
    DRIVER_STATE_T_LOCS = "total_locations"
    DRIVER_STATE_LOC = "loc"
    DRIVER_STATE_DT_SEC = "location_dt_seconds"
    DRIVER_STATE_ORDER = "order"
    DRIVER_STATE_RUN_ID = "run_id"
    DRIVER_STATE_LOC_SERV = "locations_already_serviced"
    DRIVER_MANIFEST = "manifest"
    MANIFEST_ACTION = "action"
    BOOKING_ID = "booking_id"


    def get_payload_object(payload, online=True):

        travel_time_matrix = None
        if "time_matrix" in payload:
            travel_time_matrix = payload["time_matrix"]

        driver_runs = payload["driver_runs"]

        current_time = 3600*24
        if online:
            earliest_start_time = current_time
            for driver_run in driver_runs:
                state = driver_run[PayloadParser.DRIVER_STATE]
                last_recorded_time = state['location_dt_seconds']
                start_time = state['start_time']
                earliest_start_time = min(earliest_start_time,start_time)
                if last_recorded_time > start_time:
                    current_time = min(current_time,last_recorded_time)
            if current_time == 3600*24:
                current_time = earliest_start_time


        active_requests_data = {}
        boarded_requests_data = {}

        for driver_run in driver_runs:
            i = 0
            added_active_requests = []
            if PayloadParser.DRIVER_MANIFEST in driver_run:
                driver_state = driver_run[PayloadParser.DRIVER_STATE]
                driver_manifest = driver_run[PayloadParser.DRIVER_MANIFEST]
                while i < len(driver_manifest):
                    stop = driver_manifest[i]
                    stop_order = stop['order']
                    booking_id = stop[PayloadParser.BOOKING_ID]
                    if stop[PayloadParser.MANIFEST_ACTION] == 'pickup':
                        request = PayloadParser.build_request_from_driver_manifest(driver_manifest,i)
                        if stop_order <= driver_state[PayloadParser.DRIVER_STATE_LOC_SERV]:
                            boarded_requests_data[booking_id] = request
                        else:
                            added_active_requests.append(booking_id)
                            active_requests_data[booking_id] = request
                    else:
                        if stop_order <= driver_state[PayloadParser.DRIVER_STATE_LOC_SERV] and booking_id in boarded_requests_data:
                            del boarded_requests_data[booking_id]
                    i+=1

        requests = []

        if 'requests' in payload:
            for request_data in payload["requests"]:
                request = PayloadParser.build_request(request_data)
                requests.append(request)
            
        for req_id in active_requests_data:
            requests.append(active_requests_data[req_id])
        active_requests = list(active_requests_data.keys())

        for req_id in boarded_requests_data:
            requests.append(boarded_requests_data[req_id])
        boarded_requests = list(boarded_requests_data.keys())

        depot = None
        if 'loc' in payload['depot']:
            lat,lon = payload['depot']['loc']['lat'],payload['depot']['loc']['lon']
            depot = NetworkHandler.manifest_location(payload['depot']['loc'],NetworkHandler.get_next_node_id(lat,lon))
        else:
            lat,lon = payload['depot']['pt']['lat'],payload['depot']['pt']['lon']
            depot = NetworkHandler.manifest_location(payload['depot']['pt'],NetworkHandler.get_next_node_id(lat,lon))

        return Payload(travel_time_matrix, current_time, requests, boarded_requests, active_requests, driver_runs, depot)

    
    def build_request_from_driver_manifest(manifest,pick_up_index):
        stop = manifest[pick_up_index]
        booking_id = stop[PayloadParser.BOOKING_ID]
        for drop_off_stop in manifest[pick_up_index+1:]:
            if drop_off_stop[PayloadParser.BOOKING_ID] == booking_id:
                return PayloadParser.build_request_from_stops(stop,drop_off_stop)
            
    def build_request_from_manifest(manifest,drop_off_stop):
        booking_id = drop_off_stop[PayloadParser.BOOKING_ID]
        for pick_up_stop in manifest:
            if pick_up_stop[PayloadParser.BOOKING_ID] == booking_id and pick_up_stop[PayloadParser.MANIFEST_ACTION] == 'pickup':
                return PayloadParser.build_request_from_stops(pick_up_stop,drop_off_stop)
            
    def build_request_from_stops(pick_up_stop,drop_off_stop):
        request = {'am': pick_up_stop['am'], 'wc': pick_up_stop['wc'], 'pickup_time_window_start': pick_up_stop['time_window_start'], 
        'pickup_time_window_end': pick_up_stop['time_window_end'], 'pickup_pt': pick_up_stop['loc'], PayloadParser.BOOKING_ID: pick_up_stop[PayloadParser.BOOKING_ID],
        'dropoff_time_window_start': drop_off_stop['time_window_start'], 'dropoff_time_window_end': drop_off_stop['time_window_end'],
        'dropoff_pt': drop_off_stop['loc']
        }
        return request

    def build_request(request_data):
        request = {'am': request_data['am'], 'wc': request_data['wc'], 'pickup_time_window_start': request_data['pickup_time_window_start'], 
        'pickup_time_window_end': request_data['pickup_time_window_end'], 'pickup_pt': request_data['pickup_pt'], PayloadParser.BOOKING_ID: request_data[PayloadParser.BOOKING_ID],
        'dropoff_time_window_start': request_data['dropoff_time_window_start'], 'dropoff_time_window_end': request_data['dropoff_time_window_end'],
        'dropoff_pt': request_data['dropoff_pt']
        }
        return request
