#!/usr/bin/env python3
"""
Generate universes/eod2_sectors.csv from eod2 curated sector data.

Embeds KNOWN_SECTORS from eod2/src/defs/stock_info.py so this can run
without importing from the eod2 package.

Run: python generate_eod2_sectors.py
"""
from pathlib import Path
import csv

KNOWN_SECTORS: dict = {
    # Banking
    "HDFCBANK": "Banking", "ICICIBANK": "Banking", "SBIN": "Banking", "AXISBANK": "Banking",
    "KOTAKBANK": "Banking", "INDUSINDBK": "Banking", "BANKBARODA": "Banking", "PNB": "Banking",
    "CANBK": "Banking", "UNIONBANK": "Banking", "IDFCFIRSTB": "Banking", "FEDERALBNK": "Banking",
    "AUBANK": "Banking", "BANDHANBNK": "Banking", "YESBANK": "Banking", "MAHABANK": "Banking",
    "IOB": "Banking", "UCOBANK": "Banking", "CENTRALBK": "Banking", "KARURVYSYA": "Banking",
    "CUB": "Banking", "RBLBANK": "Banking", "SOUTHBANK": "Banking", "JKBANK": "Banking",
    "PSB": "Banking", "TMB": "Banking", "UTKARSHBNK": "Banking",
    # Information Technology
    "TCS": "Information Technology", "INFY": "Information Technology", "HCLTECH": "Information Technology",
    "WIPRO": "Information Technology", "TECHM": "Information Technology", "LTIM": "Information Technology",
    "PERSISTENT": "Information Technology", "COFORGE": "Information Technology", "MPHASIS": "Information Technology",
    "LTTS": "Information Technology", "TATAELXSI": "Information Technology", "KPITTECH": "Information Technology",
    "CYIENT": "Information Technology", "ZENSARTECH": "Information Technology", "BSOFT": "Information Technology",
    "SONATSOFTW": "Information Technology", "MASTEK": "Information Technology", "LATENTVIEW": "Information Technology",
    "HAPPSTMNDS": "Information Technology", "RATEGAIN": "Information Technology", "TATATECH": "Information Technology",
    # Automobile
    "TATAMOTORS": "Automobile", "MARUTI": "Automobile", "M&M": "Automobile", "BAJAJ-AUTO": "Automobile",
    "EICHERMOT": "Automobile", "HEROMOTOCO": "Automobile", "TVSMOTOR": "Automobile", "ASHOKLEY": "Automobile",
    "ZFCVINDIA": "Automobile and Auto Components",
    # Auto Ancillary
    "BHARATFORG": "Auto Ancillary", "BALKRISIND": "Auto Ancillary", "MRF": "Auto Ancillary",
    "APOLLOTYRE": "Auto Ancillary", "CEATLTD": "Auto Ancillary", "EXIDEIND": "Auto Ancillary",
    "AMARAJABAT": "Auto Ancillary", "BOSCHLTD": "Auto Ancillary", "MOTHERSON": "Auto Ancillary",
    "SONACOMS": "Auto Ancillary", "UNOMINDA": "Auto Ancillary", "TIINDIA": "Auto Ancillary",
    "CRAFTSMAN": "Auto Ancillary", "SANSERA": "Auto Ancillary", "GABRIEL": "Auto Ancillary",
    "TALBROAUTO": "Auto Ancillary",
    # Healthcare & Pharma
    "SUNPHARMA": "Healthcare & Pharma", "CIPLA": "Healthcare & Pharma", "DRREDDY": "Healthcare & Pharma",
    "DIVISLAB": "Healthcare & Pharma", "LUPIN": "Healthcare & Pharma", "AUROPHARMA": "Healthcare & Pharma",
    "TORNTPHARM": "Healthcare & Pharma", "ZYDUSLIFE": "Healthcare & Pharma", "MANKIND": "Healthcare & Pharma",
    "ALKEM": "Healthcare & Pharma", "BIOCON": "Healthcare & Pharma", "GLENMARK": "Healthcare & Pharma",
    "IPCALAB": "Healthcare & Pharma", "LAURUSLABS": "Healthcare & Pharma", "AJANTPHARM": "Healthcare & Pharma",
    "NATCOPHARM": "Healthcare & Pharma", "ABBOTINDIA": "Healthcare & Pharma", "PFIZER": "Healthcare & Pharma",
    "SANOFI": "Healthcare & Pharma", "SYNGENE": "Healthcare & Pharma", "GRANULES": "Healthcare & Pharma",
    "WOCKPHARMA": "Healthcare & Pharma", "ACUTAAS": "Healthcare & Pharma",
    # Healthcare & Hospitals
    "APOLLOHOSP": "Healthcare & Hospitals", "MAXHEALTH": "Healthcare & Hospitals",
    "FORTIS": "Healthcare & Hospitals", "MEDANTA": "Healthcare & Hospitals",
    "NH": "Healthcare & Hospitals", "KIMS": "Healthcare & Hospitals",
    # FMCG
    "ITC": "FMCG", "HINDUNILVR": "FMCG", "NESTLEIND": "FMCG", "BRITANNIA": "FMCG",
    "DABUR": "FMCG", "GODREJCP": "FMCG", "MARICO": "FMCG", "COLPAL": "FMCG",
    "TATACONSUM": "FMCG", "EMAMILTD": "FMCG", "PATANJALI": "FMCG", "BIKAJI": "FMCG",
    "AWL": "FMCG", "ZYDUSWELL": "FMCG", "VBL": "FMCG & Beverages",
    "RADICO": "Beverages & Distilleries", "UBL": "Beverages & Distilleries", "MCDOWELL-N": "Beverages & Distilleries",
    # Metals & Mining
    "TATASTEEL": "Metals & Mining", "JSWSTEEL": "Metals & Mining", "HINDALCO": "Metals & Mining",
    "JINDALSTEL": "Metals & Mining", "VEDL": "Metals & Mining", "COALINDIA": "Metals & Mining",
    "NMDC": "Metals & Mining", "SAIL": "Metals & Mining", "NATIONALUM": "Metals & Mining",
    "APLAPOLLO": "Metals & Mining", "RATNAMANI": "Metals & Mining", "JSL": "Metals & Mining",
    "HINDZINC": "Metals & Mining", "ADANIENT": "Metals & Mining", "WELCORP": "Metals & Mining",
    # Energy & Oil/Gas
    "RELIANCE": "Energy & Petrochemicals", "ONGC": "Energy & Oil", "IOC": "Energy & Oil",
    "BPCL": "Energy & Oil", "HPCL": "Energy & Oil", "OIL": "Energy & Oil",
    "MRPL": "Energy & Oil", "CHENNPETRO": "Energy & Oil",
    "GAIL": "Energy & Gas", "IGL": "Energy & Gas", "MGL": "Energy & Gas",
    "GUJGASLTD": "Energy & Gas", "PETRONET": "Energy & Gas", "ATGL": "Energy & Gas",
    "AEGISLOG": "Energy & Gas", "AEGISVOPAK": "Energy & Gas",
    # Power & Utilities
    "NTPC": "Power & Utilities", "POWERGRID": "Power & Utilities", "TATAPOWER": "Power & Utilities",
    "ADANIPOWER": "Power & Utilities", "ADANIGREEN": "Power & Utilities", "ADANIENSOL": "Power & Utilities",
    "TORNTPOWER": "Power & Utilities", "NHPC": "Power & Utilities", "SJVN": "Power & Utilities",
    "CESC": "Power & Utilities", "JSWENERGY": "Power & Utilities", "IREDA": "Power & Utilities",
    "ACMESOLAR": "Power & Utilities",
    # Financial Services
    "BAJFINANCE": "Financial Services", "BAJAJFINSV": "Financial Services", "CHOLAFIN": "Financial Services",
    "SHRIRAMFIN": "Financial Services", "MUTHOOTFIN": "Financial Services", "MANAPPURAM": "Financial Services",
    "M&MFIN": "Financial Services", "POONAWALLA": "Financial Services", "ABCAPITAL": "Financial Services",
    "360ONE": "Financial Services", "AADHARHFC": "Financial Services", "AAVAS": "Financial Services",
    "MOTILALOFS": "Financial Services",
    "ANGELONE": "Stock Broking", "GEOJITFSL": "Stock Broking", "5PAISA": "Stock Broking",
    "HDFCAMC": "Asset Management", "NAM-INDIA": "Asset Management", "UTIAMC": "Asset Management",
    "ABSLAMC": "Asset Management",
    "KFINTECH": "Financial Technology", "CAMS": "Financial Technology", "PAYTM": "Financial Technology",
    "POLICYBZR": "Financial Technology",
    "BSE": "Financial Exchanges", "MCX": "Financial Exchanges", "IEX": "Financial Exchanges",
    "CDSL": "Financial Infrastructure",
    "ICICIPRULI": "Insurance", "HDFCLIFE": "Insurance", "SBILIFE": "Insurance",
    "GICRE": "Insurance", "NIACL": "Insurance", "STARHEALTH": "Insurance", "ICICIGI": "Insurance",
    # Real Estate
    "DLF": "Real Estate", "GODREJPROP": "Real Estate", "OBEROIRLTY": "Real Estate",
    "LODHA": "Real Estate", "PRESTIGE": "Real Estate", "BRIGADE": "Real Estate",
    "SOBHA": "Real Estate", "SIGNATURE": "Real Estate", "SUNTECK": "Real Estate",
    "ABREL": "Real Estate", "PHOENIXLTD": "Real Estate & Retail",
    # Cement & Building Materials
    "ULTRACEMCO": "Cement & Building Materials", "AMBUJACEM": "Cement & Building Materials",
    "ACC": "Cement & Building Materials", "SHREECEM": "Cement & Building Materials",
    "DALBHARAT": "Cement & Building Materials", "JKCEMENT": "Cement & Building Materials",
    "RAMCOCEM": "Cement & Building Materials", "HEIDELBERG": "Cement & Building Materials",
    "ASTRAL": "Building Materials & Pipes", "SUPREMEIND": "Building Materials & Plastics",
    "FINPIPE": "Building Materials & Pipes", "PRINCEPIPE": "Building Materials & Pipes",
    "KAJARIACER": "Ceramics & Sanitaryware", "CERA": "Ceramics & Sanitaryware",
    # Capital Goods & Defense
    "LT": "Infrastructure & Capital Goods", "SIEMENS": "Capital Goods & Engineering",
    "ABB": "Capital Goods & Engineering", "BHEL": "Capital Goods & Heavy Electricals",
    "HAL": "Aerospace & Defense", "BEL": "Aerospace & Defense", "BDL": "Aerospace & Defense",
    "MAZDOCK": "Defense & Shipbuilding", "COCHINSHIP": "Defense & Shipbuilding",
    "GRSE": "Defense & Shipbuilding", "DATAPATTNS": "Aerospace & Defense",
    "POLYCAB": "Capital Goods & Cables", "KEI": "Capital Goods & Cables",
    "HAVELLS": "Consumer Electricals", "VOLTAS": "Consumer Electricals",
    "BLUESTARCO": "Consumer Electricals", "CROMPTON": "Consumer Electricals",
    "WHIRLPOOL": "Consumer Electricals",
    "AMBER": "Electronics Manufacturing", "DIXON": "Electronics Manufacturing",
    "KAYNES": "Electronics Manufacturing", "SYRMA": "Electronics Manufacturing",
    "PNCINFRA": "Infrastructure", "KNRCON": "Infrastructure", "GRINFRA": "Infrastructure",
    "RVNL": "Railway Infrastructure", "IRFC": "Railway Finance",
    "IRCON": "Railway Infrastructure", "RITES": "Railway Infrastructure",
    "RAILTEL": "Telecom & Rail Infra", "TITAGARH": "Railway Wagons",
    "JWL": "Railway Wagons", "TEXRAIL": "Railway Wagons",
    "AIAENG": "Capital Goods & Engineering", "ACE": "Capital Goods & Engineering",
    "CPPLUS": "Capital Goods & Engineering", "ZENTEC": "Capital Goods & Engineering",
    # Specialty Chemicals
    "SRF": "Specialty Chemicals", "PIDILITIND": "Adhesives & Chemicals",
    "AARTIIND": "Specialty Chemicals", "DEEPAKNTR": "Specialty Chemicals",
    "TATACHEM": "Chemicals", "GUJALKALI": "Chemicals", "ATUL": "Specialty Chemicals",
    "NAVINFLUOR": "Specialty Chemicals", "FLUOROCHEM": "Specialty Chemicals",
    "FINEORG": "Specialty Chemicals", "CLEAN": "Specialty Chemicals",
    "VINATIORGA": "Specialty Chemicals", "ALKYLAMINE": "Specialty Chemicals",
    "BALAMINES": "Specialty Chemicals",
    "COROMANDEL": "Fertilizers & Agrochem", "PIIND": "Agrochemicals",
    "UPL": "Agrochemicals", "BAYERCROP": "Agrochemicals", "SUMICHEM": "Agrochemicals",
    # Telecom, Media & Internet
    "BHARTIARTL": "Telecommunications", "IDEA": "Telecommunications",
    "INDUSTOWER": "Telecom Infrastructure", "TATACOMM": "Telecommunications",
    "HFCL": "Telecom Equipment", "STLTECH": "Telecom Cables",
    "ZEEL": "Media & Entertainment", "SUNTV": "Media & Entertainment",
    "PVRINOX": "Media & Entertainment", "TV18BRDCST": "Media & Entertainment",
    "NETWORK18": "Media & Entertainment", "SAREGAMA": "Media & Entertainment",
    "NAZARA": "Gaming & Tech", "ZOMATO": "Consumer Internet", "SWIGGY": "Consumer Internet",
    "NYKAA": "E-Commerce & Retail", "DELHIVERY": "Logistics & Supply Chain",
    "BLUEDART": "Logistics & Express", "ALLCARGO": "Logistics", "TCIEXP": "Logistics",
    "ADANIPORTS": "Logistics & Ports",
    # Consumer Retail & Jewelry
    "TITAN": "Consumer Jewelry & Watches", "KALYANKJIL": "Consumer Jewelry",
    "SENCO": "Consumer Jewelry", "TRENT": "Retail & Fashion", "DMART": "Retail & Supermarkets",
    "ABFRL": "Retail & Apparel", "ABLBL": "Retail & Apparel",
    "PAGEIND": "Apparel & Innerwear", "VEDANTFASH": "Apparel & Ethnic",
    "METROBRAND": "Footwear", "CAMPUS": "Footwear", "BATAINDIA": "Footwear", "RELAXO": "Footwear",
    # Textiles & Diversified
    "WELSPUNLIV": "Textiles", "3MINDIA": "Diversified",
    "ECLERX": "Services",
}

KEYWORD_SECTORS = [
    (("BANK", "FINANCE", "FINANCIAL", "FINSERV", "NBFC", "HOUSING", "CREDIT", "SECURITIES"), "Financial Services"),
    (("PHARMA", "DRUG", "HEALTH", "MEDIC", "BIO", "LAB", "HOSPITAL", "CLINIC", "DIAGN"), "Healthcare & Pharma"),
    (("TECH", "SOFT", "INFO", "DIGITAL", "SOLUTION", "SYSTEM", "DATA", "CYBER"), "Information Technology"),
    (("AUTO", "MOTOR", "TYRE", "WHEEL", "GEAR", "CLUTCH", "BRAKE", "ENGINE", "FORG"), "Automobile & Ancillary"),
    (("STEEL", "IRON", "METAL", "ALUM", "COPPER", "ZINC", "MINING", "MINERAL"), "Metals & Mining"),
    (("POWER", "SOLAR", "WIND", "RENEW", "NHPC", "SJVN", "NTPC"), "Power & Utilities"),
    (("OIL", "GAS", "PETRO", "REFIN", "FUEL"), "Energy & Oil/Gas"),
    (("CHEM", "ORGANIC", "SPECIALTY", "FERT", "AGRO", "PEST", "PIGMENT"), "Chemicals & Fertilizers"),
    (("REALTY", "INFRA", "BUILD", "CONSTRUCT", "ESTATE", "DEVELOP", "PROP"), "Real Estate & Infra"),
    (("TEXTILE", "FABRIC", "COTTON", "YARN", "SPIN", "GARMENT", "APPAREL", "WEAR"), "Textiles & Apparel"),
    (("FOOD", "SUGAR", "TEA", "COFFEE", "BEVERAGE", "BREW", "DISTILL", "DAIRY"), "FMCG & Food Products"),
    (("PAPER", "PACK", "PRINT", "CONTAINER"), "Paper & Packaging"),
    (("CEMENT", "CERAMIC", "PIPE", "GLASS", "TILES"), "Building Materials"),
    (("LOGISTIC", "TRANSPORT", "SHIPPING", "PORT", "FREIGHT", "EXPRESS", "CARGO"), "Logistics & Ports"),
    (("MEDIA", "ENTERTAIN", "FILM", "BROADCAST", "CABLE"), "Media & Entertainment"),
    (("RETAIL", "MART", "STORE", "JEWEL", "WATCH", "FASHION"), "Consumer & Retail"),
    (("HOTEL", "RESORT", "TRAVEL", "TOUR", "RESTAUR", "HOSPITALITY"), "Hotels & Tourism"),
    (("TELECOM", "COMMUNICATION", "TOWER", "ANTENNA"), "Telecommunications"),
    (("DEFENCE", "DEFENSE", "ARMOUR", "WEAPON", "ORDNANCE"), "Aerospace & Defense"),
    (("INSURANCE", "INSUR", "ASSURANCE"), "Insurance"),
]


def classify_by_keyword(symbol: str) -> str:
    s = symbol.upper()
    for keywords, sector in KEYWORD_SECTORS:
        if any(kw in s for kw in keywords):
            return sector
    return "Diversified"


def main():
    out_path = Path(__file__).resolve().parent / "universes" / "eod2_sectors.csv"
    out_path.parent.mkdir(exist_ok=True)

    rows = []
    for sym, sector in sorted(KNOWN_SECTORS.items()):
        rows.append({"Symbol": sym, "Sector": sector})

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["Symbol", "Sector"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"Written {len(rows)} entries to {out_path}")


if __name__ == "__main__":
    main()
