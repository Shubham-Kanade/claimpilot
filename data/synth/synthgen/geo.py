"""Indian cities used by the generator, with GST state codes, PIN prefixes and transport hubs.

Geography (city, locality, airport and station codes) is public information; every merchant
placed in these cities is fictional.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class City:
    name: str
    state_code: str  # GST state code, must match the first two GSTIN digits
    rto: str  # vehicle registration state prefix
    pin_prefix: str
    std_code: str
    airport: str  # IATA code
    station: str  # railway station name with code, as printed on tickets
    hindi_belt: bool  # Hindi item names are more likely here
    localities: tuple[str, ...]


CITIES: tuple[City, ...] = (
    City(
        "Mumbai",
        "27",
        "MH",
        "400",
        "022",
        "BOM",
        "Mumbai CSMT (CSMT)",
        False,
        ("Andheri East", "Powai", "Lower Parel", "Bandra West", "Fort", "Malad West"),
    ),
    City(
        "Pune",
        "27",
        "MH",
        "411",
        "020",
        "PNQ",
        "Pune Jn (PUNE)",
        False,
        ("Hinjewadi", "Kothrud", "Viman Nagar", "Baner", "Shivajinagar"),
    ),
    City(
        "Nagpur",
        "27",
        "MH",
        "440",
        "0712",
        "NAG",
        "Nagpur Jn (NGP)",
        True,
        ("Sitabuldi", "Dharampeth", "Sadar", "Civil Lines"),
    ),
    City(
        "Bengaluru",
        "29",
        "KA",
        "560",
        "080",
        "BLR",
        "KSR Bengaluru (SBC)",
        False,
        ("Koramangala", "Indiranagar", "Whitefield", "HSR Layout", "Jayanagar"),
    ),
    City(
        "Chennai",
        "33",
        "TN",
        "600",
        "044",
        "MAA",
        "Chennai Central (MAS)",
        False,
        ("T Nagar", "Adyar", "Guindy", "Nungambakkam", "Velachery"),
    ),
    City(
        "Hyderabad",
        "36",
        "TS",
        "500",
        "040",
        "HYD",
        "Secunderabad Jn (SC)",
        False,
        ("Gachibowli", "Banjara Hills", "Madhapur", "Begumpet", "Kondapur"),
    ),
    City(
        "New Delhi",
        "07",
        "DL",
        "110",
        "011",
        "DEL",
        "New Delhi (NDLS)",
        True,
        ("Connaught Place", "Karol Bagh", "Saket", "Lajpat Nagar", "Janakpuri"),
    ),
    City(
        "Kolkata",
        "19",
        "WB",
        "700",
        "033",
        "CCU",
        "Howrah Jn (HWH)",
        False,
        ("Park Street", "Salt Lake", "Ballygunge", "New Town", "Esplanade"),
    ),
    City(
        "Ahmedabad",
        "24",
        "GJ",
        "380",
        "079",
        "AMD",
        "Ahmedabad Jn (ADI)",
        True,
        ("Navrangpura", "Satellite", "Bodakdev", "Maninagar", "SG Highway"),
    ),
    City(
        "Jaipur",
        "08",
        "RJ",
        "302",
        "0141",
        "JAI",
        "Jaipur Jn (JP)",
        True,
        ("C Scheme", "Malviya Nagar", "Vaishali Nagar", "MI Road", "Raja Park"),
    ),
    City(
        "Lucknow",
        "09",
        "UP",
        "226",
        "0522",
        "LKO",
        "Lucknow NR (LKO)",
        True,
        ("Hazratganj", "Gomti Nagar", "Aliganj", "Indira Nagar"),
    ),
    City(
        "Indore",
        "23",
        "MP",
        "452",
        "0731",
        "IDR",
        "Indore Jn (INDB)",
        True,
        ("Vijay Nagar", "Palasia", "Rajwada", "Bhawarkuan"),
    ),
    City(
        "Chandigarh",
        "04",
        "CH",
        "160",
        "0172",
        "IXC",
        "Chandigarh (CDG)",
        True,
        ("Sector 17", "Sector 35", "Sector 8", "Industrial Area Phase I"),
    ),
    City(
        "Kochi",
        "32",
        "KL",
        "682",
        "0484",
        "COK",
        "Ernakulam Jn (ERS)",
        False,
        ("MG Road", "Kakkanad", "Edappally", "Fort Kochi"),
    ),
    City(
        "Panaji",
        "30",
        "GA",
        "403",
        "0832",
        "GOI",
        "Madgaon (MAO)",
        False,
        ("Miramar", "Panjim Market", "Altinho", "Campal"),
    ),
    City(
        "Bhubaneswar",
        "21",
        "OD",
        "751",
        "0674",
        "BBI",
        "Bhubaneswar (BBS)",
        False,
        ("Saheed Nagar", "Patia", "Jaydev Vihar", "Old Town"),
    ),
)

CITY_BY_NAME: dict[str, City] = {city.name: city for city in CITIES}

# City pairs commonly travelled by train (overnight or short-haul).
RAIL_NEIGHBOURS: dict[str, tuple[str, ...]] = {
    "Mumbai": ("Pune", "Ahmedabad", "Panaji", "Nagpur"),
    "Pune": ("Mumbai", "Hyderabad"),
    "Nagpur": ("Mumbai", "Indore"),
    "Bengaluru": ("Chennai", "Hyderabad", "Kochi"),
    "Chennai": ("Bengaluru", "Hyderabad", "Kochi"),
    "Hyderabad": ("Bengaluru", "Chennai", "Pune"),
    "New Delhi": ("Jaipur", "Lucknow", "Chandigarh"),
    "Kolkata": ("Bhubaneswar",),
    "Ahmedabad": ("Mumbai", "Jaipur"),
    "Jaipur": ("New Delhi", "Ahmedabad"),
    "Lucknow": ("New Delhi",),
    "Indore": ("Nagpur",),
    "Chandigarh": ("New Delhi",),
    "Kochi": ("Bengaluru", "Chennai"),
    "Panaji": ("Mumbai",),
    "Bhubaneswar": ("Kolkata",),
}
