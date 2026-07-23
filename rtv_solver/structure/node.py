class Node:
    def __init__(self, lat, lon, id = None):
        self.lat = lat
        self.lon = lon
        self.id = id

    def __str__(self):
        return "{{lat: {0}, lon: {1}, id: {2}}}".format(self.lat,self.lon,self.id)
