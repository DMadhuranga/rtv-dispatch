import logging
import pandas as pd
from rtv_solver.structure.request import Request
from rtv_solver.structure.node import Node
from rtv_solver.handlers.network_handler import NetworkHandler
from dateutil import parser
from multiprocessing.pool import ThreadPool

PICKUP_TIME = 'pickup_time_window_start'
ID = 'id'
PICKUP_LAT = 'pickup_latitude'
PICKUP_LON = 'pickup_longitude'
DROPOFF_LAT = 'dropoff_latitude'
DROPOFF_LON = 'dropoff_longitude'
DWELL_PICKUP = 'dwell_pickup'
DWELL_ALIGHT = 'dwell_alight'
PICKUP_WINDOW_END = 'pickup_time_window_end'
ARRIVAL_WINDOW_START = 'dropoff_time_window_start'
ARRIVAL_WINDOW_END = 'dropoff_time_window_end'
PICKUP_NODE_ID = 'pickup_node_id'
DROPOFF_NODE_ID = 'dropoff_node_id'

class RequestHandler:
    def __init__(self, request_data, dwell_pickup, dwell_alight):
        requests = []
        for req in request_data:
            pickup_time_window_start = req[PICKUP_TIME]
            pickup_time_window_end = req[PICKUP_WINDOW_END]
            dropoff_time_window_start = req[ARRIVAL_WINDOW_START]
            dropoff_time_window_end = req[ARRIVAL_WINDOW_END]
            origin = req['pickup_pt']
            dest = req['dropoff_pt']
            pick_up_lat,pick_up_lon = origin['lat'],origin['lon']
            drop_lat,drop_lon = dest['lat'],dest['lon']
            pickup_node_id = NetworkHandler.get_next_node_id(pick_up_lat,pick_up_lon)
            # if 'node_id' in origin:
            #     pickup_node_id = origin['node_id']
            dropoff_node_id = NetworkHandler.get_next_node_id(drop_lat,drop_lon)
            # if 'node_id' in dest:
            #     dropoff_node_id = dest['node_id']
            # pick_up_lat,pick_up_lon = NetworkHandler.get_nearest_node(pick_up_lat,pick_up_lon)
            # drop_lat,drop_lon = NetworkHandler.get_nearest_node(drop_lat,drop_lon)
            t_req = {'am':req['am'],'wc':req['wc'],ID:req['booking_id'],
                PICKUP_LAT: pick_up_lat,PICKUP_LON: pick_up_lon,
                DROPOFF_LAT: drop_lat,DROPOFF_LON: drop_lon,
                PICKUP_TIME: pickup_time_window_start,
                PICKUP_WINDOW_END: pickup_time_window_end,
                ARRIVAL_WINDOW_START: dropoff_time_window_start,
                ARRIVAL_WINDOW_END: dropoff_time_window_end,
                DWELL_PICKUP: dwell_pickup,
                DWELL_ALIGHT: dwell_alight,
                PICKUP_NODE_ID: pickup_node_id,
                DROPOFF_NODE_ID: dropoff_node_id
                }
            requests.append(t_req)
            
        self.requests = pd.DataFrame(requests).astype({ID: 'string'}).sort_values(by = [PICKUP_TIME])
        self.requests.drop_duplicates(subset=ID, keep="first")
        # with ThreadPool(10) as pool:
        #     for index,_ in self.requests.iterrows():
        #         pool.apply_async(self.update_request_location,args=(index,))
        #     pool.close()
        #     pool.join()

        self.count = self.requests.shape[0]
        self.next_index = 0
        logging.info('Total No of requests: {0}'.format(self.count))

    def update_request_location(self,index):
        row = self.requests.iloc[index]
        lat,lon = NetworkHandler.get_nearest_node(row[PICKUP_LAT],row[PICKUP_LON])
        self.requests.at[index,PICKUP_LAT] = lat
        self.requests.at[index,PICKUP_LON] = lon

        lat,lon = NetworkHandler.get_nearest_node(row[DROPOFF_LAT],row[DROPOFF_LON])
        self.requests.at[index,DROPOFF_LAT] = lat
        self.requests.at[index,DROPOFF_LON] = lon

    def earliest_start_time(self):
        start_time = self.get_request_by_iloc(0).pick_up_time
        logging.debug('Start time of first request: {0}'.format(start_time))
        return start_time

    def latest_start_time(self):
        start_time = self.get_request_by_iloc(self.count-1).pick_up_time
        logging.debug('Start time of last request: {0}'.format(start_time))
        return start_time

    def get_request(self,request_data):
        pickup_node_id = None
        dropoff_node_id = None
        if PICKUP_NODE_ID in request_data:
            pickup_node_id = request_data[PICKUP_NODE_ID]
        if DROPOFF_NODE_ID in request_data:
            dropoff_node_id = request_data[DROPOFF_NODE_ID]
        origin = Node(request_data[PICKUP_LAT],request_data[PICKUP_LON],pickup_node_id)
        destination = Node(request_data[DROPOFF_LAT],request_data[DROPOFF_LON],dropoff_node_id)
        id = request_data[ID]
        pick_up_time = request_data[PICKUP_TIME]
        latest_pick_up_time = request_data[PICKUP_WINDOW_END]
        earliest_arrival_time = request_data[ARRIVAL_WINDOW_START]
        latest_arrival_time = request_data[ARRIVAL_WINDOW_END]
        dwell_pickup = int(request_data[DWELL_PICKUP])
        dwell_alight = int(request_data[DWELL_ALIGHT])
        am_capacity = request_data['am']
        wc_capacity = request_data['wc']
        return Request(id,am_capacity,wc_capacity,pick_up_time,latest_pick_up_time,earliest_arrival_time,latest_arrival_time,origin,destination,dwell_pickup,dwell_alight)

    def get_request_by_iloc(self,iloc):
        request_data = self.requests.iloc[iloc]
        return self.get_request(request_data)

    def get_batch(self,end_time,max_batch_size):
        batch = []
        ending_index = min(self.next_index+max_batch_size,self.requests.shape[0])
        for _, row in self.requests.iloc[self.next_index:ending_index].iterrows():
            request = self.get_request(row)
            if request.pick_up_time > end_time:
                break
            batch.append(request)
            self.next_index+=1
        time_of_next_request = self.requests.iloc[min(self.next_index,self.requests.shape[0]-1)][PICKUP_TIME]
        if time_of_next_request <= end_time and len(batch) > 0:
            end_time = min(end_time,batch[-1].pick_up_time)
        print(end_time,len(batch))
        return batch,end_time
    
    def get_lookahead_trips(self,end_time,rh_factor,batch_interval):
        batch = []
        horizen_end_time = end_time + rh_factor*batch_interval
        for _, row in self.requests.iloc[self.next_index:].iterrows():
            request = self.get_request(row)
            if request.pick_up_time > horizen_end_time or request.pick_up_time < end_time:
                break
            batch.append(request)
        return batch

    def get_all_requests(self):
        batch = []
        for _, row in self.requests.iterrows():
            request = self.get_request(row)
            batch.append(request)
        return batch
    
    def unique_nodes(self):
        return self.requests.origin.unique()
    
    def get_all_nodes(self,round_at):
        coordinates = {}
        nodes = []
        for _,request_data in self.requests.iterrows():
            lat,lon = round(request_data['pickup_latitude'],round_at),round(request_data['pickup_longitude'],round_at)
            coordinates[(lat,lon)] = None
            lat,lon = round(request_data['dropoff_latitude'],round_at),round(request_data['dropoff_longitude'],round_at)
            coordinates[(lat,lon)] = None
        for key in coordinates:
            nodes.append(Node(key[0],key[1]))
        return nodes
