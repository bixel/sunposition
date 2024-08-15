from textwrap import dedent
import pytz
from datetime import date, timedelta, datetime
from logging import getLogger

import streamlit as st
import pandas as pd
import pvlib
from pvlib import clearsky
import numpy as np
from scipy import integrate


logging = getLogger()


def direction_vec(elevation, azimuth):
    se = np.sin(np.radians(elevation))
    ce = np.cos(np.radians(elevation))
    sa = np.sin(np.radians(azimuth))
    ca = np.cos(np.radians(azimuth))
    return np.array([sa * ce, ca * ce, se])


def irradiation_factor(sun_vec, window_vec):
    return np.max([sun_vec @ window_vec, 0])


def calculate_pressure(altitude):
    return pvlib.atmosphere.alt2pres(altitude)


@st.cache_data(persist=True, max_entries=30)
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
    ineichen[["apparent_elevation", "azimuth"]] = solpos[
        ["apparent_elevation", "azimuth"]
    ]
    return ineichen


@st.cache_data(
    hash_funcs={
        "areas": lambda areas: [
            f"{a['azimuth']}{a['elevation']}{a['size']}" for a in areas
        ]
    }
)
def join_areas(_base_data: pd.DataFrame, areas: list[dict]) -> pd.DataFrame:
    # areas = st.session_state.get("areas", [])
    for i, area in enumerate(areas):
        area_direction = direction_vec(area.get("elevation", 0), area.get("azimuth", 0))
        area_direction_factors = _base_data[["apparent_elevation", "azimuth"]].apply(
            lambda row: irradiation_factor(direction_vec(*row), area_direction), axis=1
        )
        _base_data[f"area_{i}_direction_factor"] = area_direction_factors
        _base_data[f"area_{i}_irradiation"] = area.get("size", 0) * (
            area_direction_factors * _base_data["dni"] + _base_data["dhi"]
        )

    if areas:
        _base_data["irradiation_sum"] = _base_data[
            [f"area_{ii}_irradiation" for ii in range(len(areas))]
        ].sum(axis=1)

    return _base_data


def integrate_joined_data(joined_data: pd.DataFrame) -> pd.DataFrame | None:
    irradiation_cols = [
        f"area_{i}_irradiation" for i in range(len(st.session_state.get("areas", [])))
    ]
    all_cols = []
    grouper = pd.Grouper(freq="D")
    dx = joined_data.index[:2].diff()[-1]
    assert type(dx) == pd.Timedelta
    data = {}
    for i, col in enumerate(irradiation_cols):
        cname = f"area_{i}_integrated"
        data[cname] = joined_data.groupby(grouper)[col].apply(
            lambda g: integrate.trapezoid(g, dx=dx.seconds)
        )
        all_cols.append(cname)

    pdf = pd.DataFrame(data)

    if len(irradiation_cols) > 1:
        pdf["integrated_sum"] = pdf.sum(axis=1)
        all_cols.append("integrated_sum")

    return pdf[all_cols].iloc[:-1] / 3_600_000
    # if "irradiation_sum" in joined_data.columns:
    #     integrated_data = joined_data.groupby(grouper)[
    #         "irradiation_sum"
    #     ].apply(lambda g: integrate.trapezoid(g, dx=dx.seconds))
    #     return integrated_data.iloc[:-1] / 3600 / 1000


def daily_data(data: pd.DataFrame, plot_date: date):
    return data.loc[plot_date.isoformat() : plot_date.isoformat()]


def add_area():
    st.session_state.areas.append({})


def remove_area(idx: int):
    st.session_state.areas.pop(idx)


def sync_i(idx):
    label = st.session_state[f"area_text_{idx}"]
    if label:
        st.session_state.areas[idx]["label"] = label
    else:
        if "label" in st.session_state.areas[idx]:
            st.session_state.areas[idx].pop("label")

    st.session_state.areas[idx]["azimuth"] = st.session_state[f"area_azimuth_{idx}"]
    st.session_state.areas[idx]["elevation"] = st.session_state[f"area_elevation_{idx}"]
    st.session_state.areas[idx]["size"] = st.session_state[f"area_size_{idx}"]


@st.cache_data(persist=True, max_entries=30)
def get_solar_vector(plot_date, time_of_day, long, lat):
    solar_position = pvlib.solarposition.get_solarposition(
        datetime.combine(plot_date, time_of_day), long, lat
    )
    plot_sun_vec = direction_vec(
        *solar_position.loc[
            datetime.combine(plot_date, time_of_day) :,
            ["apparent_elevation", "azimuth"],
        ]
        .iloc[0]
        .values
    )
    return plot_sun_vec


if "areas" not in st.session_state:
    st.session_state.areas = []
else:
    logging.info(f"Loaded existing areas:\n{st.session_state.areas}")


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
    min_date = st.date_input(
        label="from",
        value=st.session_state.get("min_date", date.today()),
        key="min_date",
    )
with time_columns[1]:
    max_date = st.date_input(
        label="to",
        value=st.session_state.get("max_date", date.today() + timedelta(days=1)),
        key="max_date",
    )
with time_columns[2]:
    plot_date = st.date_input(
        label="plot_date",
        value=st.session_state.get("plot_date", date.today()),
        min_value=st.session_state.get("min_date", date.today()),
        max_value=st.session_state.get("max_date", date.today() + timedelta(days=1)),
    )
with time_columns[3]:
    time_of_day = st.time_input(
        label="time_of_day",
        value=st.session_state.get("time_of_day", datetime.now()),
        key="time_of_day",
    )
with time_columns[4]:
    freq = st.selectbox(label="freq", options=["10min", "30min", "1h"], index=1)


pressure = calculate_pressure(altitude)
data = calculate_data(min_date, max_date, freq, lat, long, timezone, pressure)
ineichen_plot_data = daily_data(data, plot_date=plot_date)
joined_area_data = join_areas(data, st.session_state.get("areas", []))
area_plot_data = daily_data(joined_area_data, plot_date=plot_date)
integrated_plot_data = integrate_joined_data(joined_area_data)

if st.session_state.get("debug"):
    st.dataframe(integrated_plot_data)

sun_vec = get_solar_vector(plot_date, time_of_day, long, lat)

st.markdown(
    body=dedent(
        f"""
        Calculation for {long=:.2f} {lat=:.2f} at {altitude=:.0f}m in {timezone=}

        Values between {min_date=:%Y-%m-%d} and {max_date=:%Y-%m-%d} with {freq=}

        Base dataset contains {data.shape[0]} rows.
        Base data is using {data.memory_usage().sum() / 1e6:.1f}MB of RAM.
        Full data is using {area_plot_data.memory_usage().sum() / 1e6:.1f}MB of RAM.

        Pressure at given coordinates is {pressure / 1_000:.2f}kPa

        Solar vector at {plot_date} {time_of_day} is {sun_vec}.
        """
    ).strip()
)

st.header(f"Ineichen Irradiation on {plot_date:%Y-%m-%d}")
st.line_chart(ineichen_plot_data, y=["dhi", "dni", "ghi"])

if num_areas := len(st.session_state.get("areas", [])):
    area_factor_cols = [f"area_{i}_direction_factor" for i in range(num_areas)]
    area_irrad_cols = {
        f"area_{i}_irradiation": st.session_state.areas[i].get(
            "label", f"area_{i}_irradiation"
        )
        for i in range(num_areas)
    }
    area_integrated_cols = {
        f"area_{i}_integrated": st.session_state.areas[i].get(
            "label", f"area_{i}_integrated"
        )
        for i in range(num_areas)
    }
    if len(area_irrad_cols) > 1:
        area_irrad_cols["irradiation_sum"] = "Sum"
        area_integrated_cols["integrated_sum"] = "Sum"
    st.header("Daily Area Irradiation")
    st.line_chart(
        area_plot_data.rename(columns=area_irrad_cols),
        y=list(area_irrad_cols.values()),
        y_label="Irradiation / Watt",
    )
    st.header("Daily Energy Potential")
    st.line_chart(
        integrated_plot_data.rename(columns=area_integrated_cols),
        y_label="Energy / kWh",
    )

    st.line_chart(
        integrated_plot_data.rename(columns=area_integrated_cols).cumsum(),
        y_label="Energy / kWh",
    )
else:
    st.text("Define areas to evaluate irradiation over time.")


area_main_header_col, area_add_col = st.columns(2)
with area_main_header_col:
    st.markdown("## Areas")
with area_add_col:
    st.button("Add Area", on_click=add_area)

st.text(f"{st.session_state.areas=}")

for i, area in enumerate(st.session_state.areas):
    header_col, delete_col = st.columns(2)
    with header_col:
        st.markdown(f"### Area {area.get('label', i)}")
    with delete_col:
        st.button("Delete", on_click=remove_area, args=(i,), key=f"area_delete_{i}")

    with st.form(f"area_{i}"):
        label_col, azimuth_col, elevation_col, area_col = st.columns(4)
        with label_col:
            label = st.text_input(
                "label",
                key=f"area_text_{i}",
                value=st.session_state.get("areas")[i].get("label"),
            )
        with azimuth_col:
            azimuth = st.number_input(
                "azimuth",
                key=f"area_azimuth_{i}",
                step=1,
                value=st.session_state.get("areas")[i].get("azimuth", 0),
            )
        with elevation_col:
            elevation = st.number_input(
                "elevation",
                key=f"area_elevation_{i}",
                step=1,
                value=st.session_state.get("areas")[i].get("elevation", 0),
            )
        with area_col:
            area = st.number_input(
                "area",
                key=f"area_size_{i}",
                step=0.1,
                value=st.session_state.get("areas")[i].get("size", 1.0),
            )

        area_direction = direction_vec(elevation, azimuth)
        factor = irradiation_factor(sun_vec, area_direction)
        if st.session_state.get("debug"):
            st.text(f"Area vector {area_direction=}")
            st.text(f"Irradiation {factor=}")

        st.form_submit_button("Save", on_click=sync_i, args=(i,))

st.toggle(label="Debug mode", key="debug")
