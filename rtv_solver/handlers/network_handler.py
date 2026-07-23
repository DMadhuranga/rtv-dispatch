from rtv_solver.structure.node import Node
import requests
import time
import numpy as np
from multiprocessing.sharedctypes import RawArray, RawValue
import ctypes
import math

# ---- Globals predeclared to avoid NameError in worker processes ----
SERVER_BASED = None
EUCLIDEAN = None
routing_url = None
nearest_url = None
table_url = None
session = None
travel_time_matrix = None
no_of_nodes = None

class NetworkHandler:
    NODE_INDEX = 0
    node_data = []

    @staticmethod
    def init(server_based, server_url=None, tt_matrix=None, euclidean=False):
        global SERVER_BASED, routing_url, nearest_url, table_url, session
        global travel_time_matrix, no_of_nodes, EUCLIDEAN

        NetworkHandler.NODE_INDEX = 0
        NetworkHandler.node_data = []
        SERVER_BASED = RawValue(ctypes.c_bool, server_based)
        EUCLIDEAN = RawValue(ctypes.c_bool, euclidean)
        if server_based:
            routing_url = server_url + 'route/v1/driving/'
            nearest_url = server_url + 'nearest/v1/driving/'
            table_url = server_url + 'table/v1/driving/'
            session = requests.Session()
            return routing_url, nearest_url, session, table_url, SERVER_BASED
        elif euclidean:
            return None, None, None, None, SERVER_BASED, EUCLIDEAN
        else:
            travel_time_matrix = np.array(tt_matrix)
            no_of_nodes = RawValue(ctypes.c_uint, travel_time_matrix.shape[0])
            travel_time_matrix = RawArray(
                np.ctypeslib.as_ctypes_type(travel_time_matrix.dtype),
                travel_time_matrix.flatten()
            )
            return travel_time_matrix, no_of_nodes, SERVER_BASED, EUCLIDEAN

    @staticmethod
    def get_next_node_id(lat, lon):
        NetworkHandler.node_data.append({"lat": lat, "lon": lon})
        NetworkHandler.NODE_INDEX += 1
        return NetworkHandler.NODE_INDEX - 1

    @staticmethod
    def initialize_travel_time_matrix():
        global SERVER_BASED, EUCLIDEAN, travel_time_matrix, no_of_nodes

        num_nodes = len(NetworkHandler.node_data)
        travel_time_matrix = np.zeros((num_nodes, num_nodes), dtype=np.float64)
        MAX_NUM_COORD = 50

        if EUCLIDEAN.value:
            for i in range(num_nodes):
                for j in range(num_nodes):
                    travel_time_matrix[i, j] = np.linalg.norm(np.array([NetworkHandler.node_data[i]['lon'], NetworkHandler.node_data[i]['lat']]) - np.array([NetworkHandler.node_data[j]['lon'], NetworkHandler.node_data[j]['lat']]))
            SERVER_BASED = RawValue(ctypes.c_bool, False)
            no_of_nodes = RawValue(ctypes.c_uint, travel_time_matrix.shape[0])
            travel_time_matrix = RawArray(
                np.ctypeslib.as_ctypes_type(travel_time_matrix.dtype),
                travel_time_matrix.flatten()
            )
            return travel_time_matrix, no_of_nodes, SERVER_BASED, EUCLIDEAN

        coordinates = [
            f"{node['lon']},{node['lat']}" for node in NetworkHandler.node_data
        ]

        iterations = math.ceil(num_nodes / MAX_NUM_COORD)
        for i in range(iterations):
            for j in range(iterations):
                origins = coordinates[i * MAX_NUM_COORD:(i + 1) * MAX_NUM_COORD]
                destinations = coordinates[j * MAX_NUM_COORD:(j + 1) * MAX_NUM_COORD]
                origin_indices = [str(k) for k in range(len(origins))]
                destination_indices = [
                    str(len(origins) + k) for k in range(len(destinations))
                ]
                url = f"{table_url}{';'.join(origins + destinations)}" \
                      f"?sources={';'.join(origin_indices)}" \
                      f"&destinations={';'.join(destination_indices)}"
                data = NetworkHandler.get_response(url)
                matrix = np.array(data['durations'])
                travel_time_matrix[
                    i * MAX_NUM_COORD:(i + 1) * MAX_NUM_COORD,
                    j * MAX_NUM_COORD:(j + 1) * MAX_NUM_COORD
                ] = matrix

        no_of_nodes = RawValue(ctypes.c_uint, travel_time_matrix.shape[0])
        SERVER_BASED = RawValue(ctypes.c_bool, False)
        travel_time_matrix = RawArray(
            np.ctypeslib.as_ctypes_type(travel_time_matrix.dtype),
            travel_time_matrix.flatten()
        )
        return travel_time_matrix, no_of_nodes, SERVER_BASED, EUCLIDEAN

    @staticmethod
    def get_response(url):
        global session
        try_count = 0
        while True:
            try_count += 1
            try:
                resp = session.get(url)
                return resp.json()
            except requests.exceptions.RequestException as e:
                if try_count > 5:
                    raise e
                time.sleep(1)

    @staticmethod
    def get_simple_route_reponse(source, dest):
        url = f"{routing_url}{source.lon},{source.lat};{dest.lon},{dest.lat}"
        return NetworkHandler.get_response(url)

    @staticmethod
    def get_detailed_route_reponse(source, dest):
        url = f"{routing_url}{source.lon},{source.lat};{dest.lon},{dest.lat}" \
              "?steps=true&geometries=geojson"
        return NetworkHandler.get_response(url)

    @staticmethod
    def get_location(source, destination):
        return int(source.id * no_of_nodes.value + destination.id)

    @staticmethod
    def travel_time(source, destination):
        if SERVER_BASED is None:
            raise RuntimeError("NetworkHandler.init() must be called before travel_time()")
        if SERVER_BASED.value:
            response = NetworkHandler.get_simple_route_reponse(source, destination)
            return response['routes'][0]['duration']
        return travel_time_matrix[NetworkHandler.get_location(source, destination)]

    @staticmethod
    def travel_time_from_node_indices(source, destination):
        return travel_time_matrix[int(source * no_of_nodes.value + destination)]

    @staticmethod
    def travel_distance(source, destination):
        if SERVER_BASED is None:
            raise RuntimeError("NetworkHandler.init() must be called before travel_distance()")
        if SERVER_BASED.value:
            response = NetworkHandler.get_simple_route_reponse(source, destination)
            return response['routes'][0]['distance']
        return travel_time_matrix[NetworkHandler.get_location(source, destination)]

    @staticmethod
    def get_current_location_time(source, destination, starting_time, current_time):
        response = NetworkHandler.get_detailed_route_reponse(source, destination)
        current_location = None
        for step in response['routes'][0]['legs'][0]['steps']:
            duration = step['duration']
            starting_time += duration
            location = step['geometry']['coordinates'][-1]
            current_location = Node(location[1], location[0])
            if starting_time >= current_time:
                return starting_time, current_location
        return starting_time, current_location

    @staticmethod
    def get_nearest_node(lat, lon):
        url = f"{nearest_url}{lon},{lat}"
        data = NetworkHandler.get_response(url)
        nearest_node = data['waypoints'][0]['location']
        return nearest_node[1], nearest_node[0]

    @staticmethod
    def are_nodes_equal(node1, node2):
        return node1.lat == node2.lat and node1.lon == node2.lon

    @staticmethod
    def get_travel_time_matrix(nodes):
        if SERVER_BASED and SERVER_BASED.value:
            coordinates = []
            node_indices = {}
            index = 0
            for node in nodes:
                coordinates.append(f"{node.lon},{node.lat}")
                node_indices[(node.lon, node.lat)] = index
                index += 1
            url = f"{table_url}{';'.join(coordinates)}"
            data = NetworkHandler.get_response(url)
            return np.array(data['durations']), node_indices
        return None, None

    @staticmethod
    def travel_time_from_matrix(node1, node2, matrix, node_indices):
        if SERVER_BASED and SERVER_BASED.value:
            index1 = node_indices[(node1.lon, node1.lat)]
            index2 = node_indices[(node2.lon, node2.lat)]
            return matrix[index1, index2]
        return NetworkHandler.travel_time(node1, node2)

    @staticmethod
    def manifest_location(location, node_id=None):
        if 'node_id' in location and node_id is None:
            node_id = location['node_id']
        return Node(location["lat"], location["lon"], node_id)
