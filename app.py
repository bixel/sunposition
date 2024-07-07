from logging import captureWarnings
from textwrap import dedent
import pytz
from datetime import date, timedelta, datetime

import streamlit as st
import pandas as pd
import pvlib
from pvlib import clearsky
import numpy as np


def direction_vec(elevation, azimuth):
    se = np.sin(np.radians(elevation))
    ce = np.cos(np.radians(elevation))
    sa = np.sin(np.radians(azimuth))
    ca = np.cos(np.radians(azimuth))
    return np.array([sa * ce, ca * ce, se])


def irradiation_factor(sun_vec, window_vec):
    return np.max([sun_vec @ window_vec, 0])


if "areas" not in st.session_state:
    st.session_state.areas = []


input_columns = st.columns(4)

with input_columns[0]:
    long = st.number_input(label="long", value=7.6547815)
with input_columns[1]:
    lat = st.number_input(label="lat", value=51.5751116)
with input_columns[2]:
    altitude = st.number_input(label="altitude", value=60)
with input_columns[3]:
    timezone = st.selectbox(
        label="timezone",
        options=pytz.common_timezones,
        index=pytz.common_timezones.index("Europe/Berlin"),
    )

time_columns = st.columns(5)
with time_columns[0]:
    min_date = st.date_input(label="from")
with time_columns[1]:
    max_date = st.date_input(label="to", value=date.today() + timedelta(days=1))
with time_columns[2]:
    plot_date = st.date_input(label="plot_date", value=date.today())
with time_columns[3]:
    time_of_day = st.time_input(label="time_of_day", value=datetime.now())
with time_columns[4]:
    freq = st.text_input(label="freq", value="1min")


@st.cache_data
def calculate_pressure(altitude):
    return pvlib.atmosphere.alt2pres(altitude)


@st.cache_data
def calculate_data(
    min_date, max_date, freq, latitude, longitude, tz, pressure
) -> pd.DataFrame:
    times = pd.date_range(start=min_date, end=max_date, freq=freq, tz=tz)
    solpos = pvlib.solarposition.get_solarposition(times, latitude, longitude)
    apparent_zenith = solpos["apparent_zenith"]
    airmass = pvlib.atmosphere.get_relative_airmass(apparent_zenith)
    airmass = pvlib.atmosphere.get_absolute_airmass(airmass, pressure)
    linke_turbidity = pvlib.clearsky.lookup_linke_turbidity(times, latitude, longitude)
    dni_extra = pvlib.irradiance.get_extra_radiation(times)
    ineichen = clearsky.ineichen(
        apparent_zenith, airmass, linke_turbidity, altitude, dni_extra
    )
    return ineichen


@st.cache_data
def plot_data_subset(data: pd.DataFrame, plot_date: date):
    return data.loc[plot_date.isoformat() : plot_date.isoformat()]


pressure = calculate_pressure(altitude)
data = calculate_data(min_date, max_date, freq, lat, long, timezone, pressure)
plot_data = plot_data_subset(data, plot_date=plot_date)

st.markdown(
    body=dedent(
        f"""
        Calculation for {long=:.2f} {lat=:.2f} at {altitude=:.0f}m in {timezone=}

        Values between {min_date=:%Y-%m-%d} and {max_date=:%Y-%m-%d} with {freq=}

        Dataset contains {data.shape[0]} rows. Using {data.memory_usage().sum() / 1e6:.1f}MB of RAM.

        Pressure at given coordinates is {pressure / 1_000:.2f}kPa
        """
    ).strip()
)

st.header(f"Ineichen Irradiation on {plot_date:%Y-%m-%d}")
st.line_chart(plot_data)


def add_area():
    st.session_state.areas.append({})


def remove_area(idx: int):
    st.session_state.areas.pop(idx)


def set_label(idx):
    value = st.session_state[f"area_text_{idx}"]
    if value:
        st.session_state.areas[idx]["label"] = value
    else:
        st.session_state.areas[idx].pop("label")


st.button("Add Area", on_click=add_area)
st.text(f"{st.session_state.areas=}")

st.markdown("## Areas")
for i, area in enumerate(st.session_state.areas):
    st.markdown(f"### Area {area.get('label', i)}")
    label_col, azimuth_col, elevation_col, delete_col = st.columns(4)
    with label_col:
        label = st.text_input(
            "label", key=f"area_text_{i}", on_change=set_label, args=(i,)
        )
    with azimuth_col:
        azimuth = st.number_input("azimuth", key=f"area_azimuth_{i}", step=1)
    with elevation_col:
        elevation = st.number_input("elevation", key=f"area_elevation_{i}", step=1)
    with delete_col:
        st.button("Delete", on_click=remove_area, args=(i,), key=f"area_delete_{i}")

    area_direction = direction_vec(elevation, azimuth)
    solar_position = pvlib.solarposition.get_solarposition(
        datetime.combine(plot_date, time_of_day), long, lat
    )
    print(solar_position)
    plot_sun_vec = direction_vec(
        *solar_position.loc[
            datetime.combine(plot_date, time_of_day) :,
            ["apparent_elevation", "azimuth"],
        ]
        .iloc[0]
        .values
    )
    factor = irradiation_factor(plot_sun_vec, area_direction)
    st.text(f"Area vector {area_direction=}")
    st.text(f"Irradiation {factor=}")
