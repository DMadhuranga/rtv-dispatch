import logging
import time

class OutputHandler:
    def __init__(self, output_directory):
        self.output_directory = output_directory
        self.request_count = 0
        self.unassigned_trip_count = 0
        self.taxi_only_trip_count = 0
        self.with_bus_trip_count = 0
        self.added_distance = 0
        with open(self.output_directory+"summary.csv", 'a+') as summary_file:
            summary_file.write("timestamp,exe_time,request_count,unassigned_trip_count,taxi_only_trip_count,with_one_bus_trip_count,with_two_bus_trip_count,added_distance\n")
        with open(self.output_directory+"completed_stops.csv", 'a+') as location_file:
            location_file.write("lat,lon,action,scheduled_time,booking_id,run_id\n")

    def record_output(self,current_time,requests,trip_handler,total_time):
        with open(self.output_directory+"shareability.csv", 'a+') as shareability_file:
            shareability_file.write(",".join([str(i) for i in trip_handler.trip_sizes])+"\n")

        request_count = trip_handler.unassigned_trip_count + trip_handler.taxi_only_trip_count + trip_handler.with_one_bus_trip_count + trip_handler.with_two_bus_trip_count
        with open(self.output_directory+"summary.csv", 'a+') as summary_file:
            summary_file.write('{0},{1},{2},{3},{4},{5},{6},{7}\n'.format(current_time,total_time,request_count,trip_handler.unassigned_trip_count,trip_handler.taxi_only_trip_count,trip_handler.with_one_bus_trip_count,trip_handler.with_two_bus_trip_count,trip_handler.added_distance/1000))

        with open(self.output_directory+"assignment.csv", 'a+') as assignment_file:
            assignment_file.write('{0}\n'.format(current_time))
            for request in requests:
                request_id = request.id
                if request_id in trip_handler.request_assignment:
                    vehicle_id = trip_handler.request_assignment[request_id]
                    assignment_file.write('{0},{1}\n'.format(request_id,vehicle_id))
                else:
                    assignment_file.write('{0}\n'.format(request_id))

    def record_vehicles(self,vehicle_locations,current_time):
        with open(self.output_directory+"vehicles.csv", 'a+') as location_file:
            sorted_vehicle_ids = list(vehicle_locations.keys())
            sorted_vehicle_ids.sort()
            location_file.write(",".join([str(vehicle_locations[vehicle_id]) for vehicle_id in sorted_vehicle_ids])+"\n")
        timestamp = time.mktime(current_time.timetuple())
        with open(self.output_directory+"vehicles_timestamp.csv", 'a+') as timestamp_file:
            timestamp_file.write(str(timestamp)+"\n")

    def record_completed_stops(self,completed_stops):
        with open(self.output_directory+"completed_stops.csv", 'a+') as location_file:
            for completed_stop in completed_stops:
                location_file.write(completed_stop.get_log()+"\n")

    def record_rebalancing_trips(self,rebalancing_trips,current_time):
        with open(self.output_directory+"rebalancing.csv", 'a+') as location_file:
            for trip in rebalancing_trips:
                location_file.write('{0},{1},{2},{3},{4}\n'.format(trip[0],trip[1],trip[2],trip[3],current_time))

    def record_bus_usage(self,busslines):
        with open(self.output_directory+"bus_usage.csv", 'a+') as bus_file:
            for bus_line_name in busslines:
                for bus_run in busslines[bus_line_name].bus_runs:
                    bus_file.write("{0}:{1}\n".format(bus_line_name,",".join([str(load) for load in bus_run.load])))

    def convert_seconds_to_timestamp(self, seconds):
        return time.strftime('%H:%M:%S', time.gmtime(seconds))
