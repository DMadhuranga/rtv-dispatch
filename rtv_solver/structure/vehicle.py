class Vehicle:
    def __init__(self, id, start_node, am_capacity, wc_capacity, start_time, end_time, depot):
        self.id = id
        self.start_time = start_time
        self.end_time = end_time
        self.depot = depot
        self.time_at_last = start_time
        self.time_at_next = start_time
        self.am_capacity = am_capacity
        self.wc_capacity = wc_capacity
        self.trips = {}
        self.picked = []
        self.served_trips = []
        self.last_node = start_node
        self.stop_sequence = []
        self.started = False
        self.rebalancing = False
        self.dwelling = False
        self.final_stop_time = start_time

    def __str__(self):
        return "{{Vehicle ID: {0}, capacity: {1}, last node: {2}, time at last: {3}}}".format(self.id, self.am_capacity+self.wc_capacity, self.last_node, self.time_at_last)
