"""Static reference list of cities the pipeline ingests weather for.

Kept separate from the fetch logic so adding a new city is a one-line change
and doesn't touch ingestion code.
"""

CITIES = [
    {"city": "New York", "country": "US", "latitude": 40.7128, "longitude": -74.0060},
    {"city": "Los Angeles", "country": "US", "latitude": 34.0522, "longitude": -118.2437},
    {"city": "Chicago", "country": "US", "latitude": 41.8781, "longitude": -87.6298},
    {"city": "Miami", "country": "US", "latitude": 25.7617, "longitude": -80.1918},
    {"city": "London", "country": "GB", "latitude": 51.5074, "longitude": -0.1278},
    {"city": "Tokyo", "country": "JP", "latitude": 35.6762, "longitude": 139.6503},
    {"city": "Sydney", "country": "AU", "latitude": -33.8688, "longitude": 151.2093},
    {"city": "Sao Paulo", "country": "BR", "latitude": -23.5505, "longitude": -46.6333},
]
