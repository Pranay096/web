# Cell 1: imports
import pandas as pd
import numpy as np
import os
import sys
from math import radians, sin, cos, asin, sqrt
from sklearn.preprocessing import LabelEncoder
from sklearn.model_selection import train_test_split
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import r2_score, mean_absolute_error, mean_squared_error
import joblib

# Cell 2: config
DATA_PATH = 'coastal_port_mandi_recommendation_dataset.csv'  # <-- your dataset filename
TRANSPORT_COST_PER_KM_PER_KG = 2.5  # configurable multiplier used if transport_cost missing
MODEL_OUTPUT_PATH = 'rf_netprice_model.joblib'
ENCODERS_OUTPUT_PATH = 'encoders.joblib'

# Cell 3: helper functions
def _normalize_colname(name: str) -> str:
    return ''.join(ch.lower() for ch in name if ch.isalnum())


def map_columns(df, expected_names):
    """Try to map expected column names (keys) to actual df columns by normalization.
    Returns dict: expected_name -> actual_column_name (or None if not found).
    """
    col_map = {}
    norm_to_actual = {_normalize_colname(c): c for c in df.columns}
    for k in expected_names:
        nk = _normalize_colname(k)
        col_map[k] = norm_to_actual.get(nk, None)
    return col_map


def haversine_km(lat1, lon1, lat2, lon2):
    # all args in degrees
    # returns distance in kilometers
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    c = 2 * asin(sqrt(a))
    return 6371.0 * c

# Cell 4: load dataset and validate
print('\nLoading dataset:', DATA_PATH)
if not os.path.exists(DATA_PATH):
    raise FileNotFoundError(f"Dataset file not found at '{DATA_PATH}'. Please upload it to the notebook environment.")

raw = pd.read_csv(DATA_PATH)
print('Dataset loaded. Rows:', len(raw))

# expected logical columns
expected = [
    'port', 'port_lat', 'port_lon', 'port_state',
    'mandi', 'mandi_lat', 'mandi_lon', 'mandi_state',
    'fish_type', 'fish_size',
    'mandi_price_inr_per_kg', 'distance_km',
    'transport_cost_inr_per_kg', 'net_price_inr_per_kg'
]
col_map = map_columns(raw, expected)

# check presence of essential columns
essential = ['port', 'mandi', 'fish_type', 'fish_size', 'mandi_price_inr_per_kg']
missing_essentials = [k for k in essential if col_map[k] is None]
if missing_essentials:
    print('Missing essential columns. Found these missing (names expected):')
    for m in missing_essentials:
        print(' -', m)
    raise SystemExit('Please provide the dataset with the required columns and re-run.')

print('\nColumn mapping (expected -> actual):')
for k, v in col_map.items():
    print(f"  {k} -> {v}")

# Cell 5: compute distance_km if missing
if col_map['distance_km'] is None:
    if None in (col_map['port_lat'], col_map['port_lon'],
                col_map['mandi_lat'], col_map['mandi_lon']):
        raise SystemExit(
            'distance_km missing and lat/lon columns required to compute it are not present.\n'
            'Please include port_lat, port_lon, mandi_lat, mandi_lon or distance_km in your CSV.'
        )
    print('\nComputing distance_km using haversine formula...')
    raw['distance_km'] = raw.apply(
        lambda r: haversine_km(
            float(r[col_map['port_lat']]), float(r[col_map['port_lon']]),
            float(r[col_map['mandi_lat']]), float(r[col_map['mandi_lon']])
        ), axis=1
    )
    col_map['distance_km'] = 'distance_km'
else:
    raw[col_map['distance_km']] = pd.to_numeric(raw[col_map['distance_km']], errors='coerce')
    print('\ndistance_km present; converted to numeric where possible.')

# Cell 6: compute transport_cost_inr_per_kg if missing
if col_map['transport_cost_inr_per_kg'] is None:
    print('\nComputing transport_cost_inr_per_kg = distance_km *', TRANSPORT_COST_PER_KM_PER_KG)
    raw['transport_cost_inr_per_kg'] = raw[col_map['distance_km']] * TRANSPORT_COST_PER_KM_PER_KG
    col_map['transport_cost_inr_per_kg'] = 'transport_cost_inr_per_kg'
else:
    raw[col_map['transport_cost_inr_per_kg']] = pd.to_numeric(
        raw[col_map['transport_cost_inr_per_kg']], errors='coerce'
    )

# Cell 7: compute net_price_inr_per_kg if missing
if col_map['net_price_inr_per_kg'] is None:
    print('\nComputing net_price_inr_per_kg = mandi_price_inr_per_kg - transport_cost_inr_per_kg')
    raw['net_price_inr_per_kg'] = (
        pd.to_numeric(raw[col_map['mandi_price_inr_per_kg']], errors='coerce')
        - raw[col_map['transport_cost_inr_per_kg']]
    )
    col_map['net_price_inr_per_kg'] = 'net_price_inr_per_kg'
else:
    raw[col_map['net_price_inr_per_kg']] = pd.to_numeric(
        raw[col_map['net_price_inr_per_kg']], errors='coerce'
    )

# ✅ Rest of the script continues unchanged, with corrected indentation for:
# - fish_size normalization
# - encoder creation
# - training
# - recommend_best_mandi() function
