class Payload:
    def __init__(self, travel_time_matrix, current_time, requests, boarded_requests, active_requests, driver_runs, depot):
        self.travel_time_matrix = travel_time_matrix
        self.current_time = current_time
        self.requests = requests
        self.boarded_requests = boarded_requests
        self.active_requests = active_requests
        self.driver_runs = driver_runs
        self.depot = depot


    def __str__(self):
        return "{{lat: {0}, lon: {1}}}".format(self.lat,self.lon)
