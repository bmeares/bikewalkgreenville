import meerschaum as mrsm

def fetch(pipe: mrsm.Pipe, **kwargs):
    if pipe.metric_key == 'requirements':
        return fetch_requirements(pipe, **kwargs)

    if pipe.metric_key == 'inventory':
        return fetch_inventory(pipe, **kwargs)

def fetch_requirements(pipe: mrsm.Pipe, **kwargs):
    return [
            {"category":"Medical", "type":"General medical", "sqft_per_space":600},
            {"category":"Medical", "type":"Hospital", "sqft_per_space":400},
            {"category":"Medical", "type":"Medical collection", "sqft_per_space":600},
            {"category":"Medical", "type":"Medical laboratory", "sqft_per_space":600},
            {"category":"Office", "type":"General office", "sqft_per_space":600},
            {"category":"Office", "type":"Bail bond service", "sqft_per_space":600},
            {"category":"Personal service", "type":"General personal service", "sqft_per_space":500},
            {"category":"Personal service", "type":"Animal care, outdoor", "sqft_per_space":600},
            {"category":"Retail", "type":"General retail", "sqft_per_space":500},
            {"category":"Retail", "type":"Alternative financial service", "sqft_per_space":500},
            {"category":"Retail", "type":"Liquor store", "sqft_per_space":500},
            {"category":"Retail", "type":"Pawnshop", "sqft_per_space":500},
            {"category":"Retail", "type":"Sexually oriented business", "sqft_per_space":200},
            {"category":"Vehicle Sale/Service", "type":"Vehicle repair or service", "sqft_per_space":1000},
            {"category":"Vehicle Sale/Service", "type":"Vehicle sale or rental", "sqft_per_space":1000},
    ]

def fetch_inventory(pipe: mrsm.Pipe, **kwargs):
	return [
			{"year":2006, "public_works_vehicles":111, "miles_of_streets":224, "miles_of_sidewalks":125, "traffic_signals":200, "street_lights":6700, "parks_acreage":355, "park_facilities":33, "community_centers":5, "tennis_courts":19, "playgrounds":37, "parks_vehicles":0, "parking_garages":11, "parking_lots":6, "public_parking_spaces":6727},
			{"year":2007, "public_works_vehicles":116, "miles_of_streets":248, "miles_of_sidewalks":125, "traffic_signals":200, "street_lights":6900, "parks_acreage":355, "park_facilities":33, "community_centers":5, "tennis_courts":19, "playgrounds":39, "parks_vehicles":0, "parking_garages":11, "parking_lots":6, "public_parking_spaces":6827},
			{"year":2008, "public_works_vehicles":118, "miles_of_streets":249, "miles_of_sidewalks":243, "traffic_signals":195, "street_lights":6975, "parks_acreage":355, "park_facilities":33, "community_centers":5, "tennis_courts":19, "playgrounds":33, "parks_vehicles":0, "parking_garages":11, "parking_lots":6, "public_parking_spaces":6901},
			{"year":2009, "public_works_vehicles":118, "miles_of_streets":248, "miles_of_sidewalks":249, "traffic_signals":232, "street_lights":7145, "parks_acreage":330, "park_facilities":31, "community_centers":5, "tennis_courts":19, "playgrounds":33, "parks_vehicles":0, "parking_garages":11, "parking_lots":5, "public_parking_spaces":6901},
			{"year":2010, "public_works_vehicles":116, "miles_of_streets":249, "miles_of_sidewalks":249, "traffic_signals":235, "street_lights":7262, "parks_acreage":330, "park_facilities":31, "community_centers":5, "tennis_courts":19, "playgrounds":33, "parks_vehicles":0, "parking_garages":9, "parking_lots":5, "public_parking_spaces":6578},
			{"year":2011, "public_works_vehicles":105, "miles_of_streets":250, "miles_of_sidewalks":252, "traffic_signals":237, "street_lights":7278, "parks_acreage":351, "park_facilities":32, "community_centers":5, "tennis_courts":19, "playgrounds":27, "parks_vehicles":0, "parking_garages":9, "parking_lots":4, "public_parking_spaces":6536},
			{"year":2012, "public_works_vehicles":102, "miles_of_streets":252, "miles_of_sidewalks":255, "traffic_signals":237, "street_lights":7340, "parks_acreage":351, "park_facilities":39, "community_centers":5, "tennis_courts":19, "playgrounds":35, "parks_vehicles":0, "parking_garages":9, "parking_lots":4, "public_parking_spaces":6523},
			{"year":2013, "public_works_vehicles":106, "miles_of_streets":252, "miles_of_sidewalks":255, "traffic_signals":236, "street_lights":7664, "parks_acreage":271, "park_facilities":39, "community_centers":5, "tennis_courts":19, "playgrounds":37, "parks_vehicles":0, "parking_garages":9, "parking_lots":4, "public_parking_spaces":6519},
			{"year":2014, "public_works_vehicles":102, "miles_of_streets":250, "miles_of_sidewalks":265, "traffic_signals":236, "street_lights":7654, "parks_acreage":322, "park_facilities":39, "community_centers":5, "tennis_courts":19, "playgrounds":35, "parks_vehicles":0, "parking_garages":9, "parking_lots":4, "public_parking_spaces":6419},
			{"year":2015, "public_works_vehicles":68, "miles_of_streets":254, "miles_of_sidewalks":255, "traffic_signals":200, "street_lights":7770, "parks_acreage":322, "park_facilities":35, "community_centers":5, "tennis_courts":19, "playgrounds":35, "parks_vehicles":0, "parking_garages":8, "parking_lots":3, "public_parking_spaces":6335},
			{"year":2016, "public_works_vehicles":72, "miles_of_streets":254, "miles_of_sidewalks":255, "traffic_signals":201, "street_lights":7779, "parks_acreage":322, "park_facilities":35, "community_centers":6, "tennis_courts":19, "playgrounds":35, "parks_vehicles":20, "parking_garages":10, "parking_lots":5, "public_parking_spaces":7631},
			{"year":2017, "public_works_vehicles":73, "miles_of_streets":252, "miles_of_sidewalks":252, "traffic_signals":201, "street_lights":7805, "parks_acreage":329, "park_facilities":34, "community_centers":6, "tennis_courts":17, "playgrounds":31, "parks_vehicles":25, "parking_garages":10, "parking_lots":5, "public_parking_spaces":8102},
			{"year":2018, "public_works_vehicles":113, "miles_of_streets":257, "miles_of_sidewalks":252, "traffic_signals":202, "street_lights":7830, "parks_acreage":364, "park_facilities":40, "community_centers":6, "tennis_courts":17, "playgrounds":33, "parks_vehicles":32, "parking_garages":10, "parking_lots":5, "public_parking_spaces":7969},
			{"year":2019, "public_works_vehicles":99, "miles_of_streets":261, "miles_of_sidewalks":253, "traffic_signals":203, "street_lights":7844, "parks_acreage":387, "park_facilities":35, "community_centers":5, "tennis_courts":17, "playgrounds":33, "parks_vehicles":41, "parking_garages":10, "parking_lots":5, "public_parking_spaces":7969},
			{"year":2020, "public_works_vehicles":95, "miles_of_streets":261, "miles_of_sidewalks":308, "traffic_signals":203, "street_lights":8068, "parks_acreage":467, "park_facilities":46, "community_centers":5, "tennis_courts":17, "playgrounds":34, "parks_vehicles":53, "parking_garages":10, "parking_lots":5, "public_parking_spaces":7969},
			{"year":2021, "public_works_vehicles":109, "miles_of_streets":264, "miles_of_sidewalks":310, "traffic_signals":203, "street_lights":8510, "parks_acreage":467, "park_facilities":46, "community_centers":6, "tennis_courts":17, "playgrounds":34, "parks_vehicles":30, "parking_garages":11, "parking_lots":7, "public_parking_spaces":8053},
			{"year":2022, "public_works_vehicles":130, "miles_of_streets":265, "miles_of_sidewalks":310, "traffic_signals":203, "street_lights":7393, "parks_acreage":425, "park_facilities":46, "community_centers":7, "tennis_courts":15, "playgrounds":34, "parks_vehicles":40, "parking_garages":11, "parking_lots":7, "public_parking_spaces":8053},
			{"year":2023, "public_works_vehicles":92, "miles_of_streets":265, "miles_of_sidewalks":311, "traffic_signals":207, "street_lights":7352, "parks_acreage":425, "park_facilities":46, "community_centers":7, "tennis_courts":15, "playgrounds":34, "parks_vehicles":47, "parking_garages":11, "parking_lots":7, "public_parking_spaces":8229},
			{"year":2024, "public_works_vehicles":89, "miles_of_streets":268, "miles_of_sidewalks":311, "traffic_signals":205, "street_lights":7374, "parks_acreage":427, "park_facilities":47, "community_centers":8, "tennis_courts":13, "playgrounds":31, "parks_vehicles":48, "parking_garages":11, "parking_lots":7, "public_parking_spaces":8233},
			{"year":2025, "public_works_vehicles":83, "miles_of_streets":270, "miles_of_sidewalks":340, "traffic_signals":215, "street_lights":7418, "parks_acreage":443, "park_facilities":50, "community_centers":8, "tennis_courts":13, "playgrounds":30, "parks_vehicles":51, "parking_garages":11, "parking_lots":7, "public_parking_spaces":8233}
	]

