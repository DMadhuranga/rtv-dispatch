class Trip:
    def __init__(self, request_id, number, am_capacity, wc_capacity, pick_up_time, latest_pick_up_time, earliest_arrival_time,latest_arrival_time, origin, destination, cost, dwell_pickup, dwell_alight, iteration, bus_combination = None, first_last_mile_type = 0, vehicle=None):
        self.origin = origin
        self.destination = destination
        self.pick_up_time = pick_up_time
        self.number = number
        self.bus_combination = bus_combination
        self.first_last_mile_type = first_last_mile_type
        self.vehicle = vehicle
        self.picked = False
        self.shared_trips = {}
        self.request_id = request_id
        self.cost = cost
        self.dwell_pickup = dwell_pickup
        self.dwell_alight = dwell_alight
        self.latest_pick_up_time = latest_pick_up_time
        self.earliest_arrival_time = earliest_arrival_time
        self.latest_arrival_time = latest_arrival_time
        self.am_capacity = am_capacity
        self.wc_capacity = wc_capacity
        if bus_combination == None:
            self.id = "{0}-{1}".format(iteration,request_id)
        else:
            self.id = "{0}:{1}-{2}".format(request_id,bus_combination,first_last_mile_type)

    def __str__(self):
        return "{{ID: {0}, time: {1}, origin: {2}, destination: {3}}}".format(self.id,self.pick_up_time,self.origin,self.destination)

    def get_shared_trips(self):
        trips = []
        for cardinality in self.shared_trips:
            trips.extend(self.shared_trips[cardinality])
        return trips
    
    def get_shared_trips_of_cardinality(self,cardinality):
        return self.shared_trips[cardinality]

    def add_shared_trip(self,cardinality,trip_id):
        if cardinality not in self.shared_trips:
            self.shared_trips[cardinality] = []
        self.shared_trips[cardinality].append(trip_id)
