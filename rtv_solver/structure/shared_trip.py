class SharedTrip:
    def __init__(self, prev_trip_number, number, trips,cost,sequence):
        self.number = number
        self.trips = trips
        self.cardinality = len(trips)
        self.cost = cost
        self.sequence = sequence
        self.prev_trip_number = prev_trip_number
