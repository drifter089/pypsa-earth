#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Thu Jul 14 21:18:06 2022

@author: user
"""

import logging
import os
from itertools import product

import numpy as np
import pandas as pd
from helpers import read_csv_nafix, sets_path_to_root, three_2_two_digits_country

_logger = logging.getLogger(__name__)


def calculate_end_values(df):
    return (1 + df) ** no_years


def country_to_nodal(industrial_production, keys):
    # keys["country"] = keys.index.str[:2]  # TODO 2digit_3_digit adaptation needed

    nodal_production = pd.DataFrame(
        index=keys.index, columns=industrial_production.columns, dtype=float
    )

    countries = keys.country.unique()
    sectors = industrial_production.columns

    for country, sector in product(countries, sectors):
        buses = keys.index[keys.country == country]

        if sector not in dist_keys.columns or dist_keys[sector].sum() == 0:
            mapping = "gdp"
        else:
            mapping = sector

        key = keys.loc[buses, mapping]
        print(sector)
        nodal_production.loc[buses, sector] = (
            industrial_production.at[country, sector] * key
        )

    return nodal_production


if __name__ == "__main__":
    if "snakemake" not in globals():
        from helpers import mock_snakemake, sets_path_to_root

        os.chdir(os.path.dirname(os.path.abspath(__file__)))

        snakemake = mock_snakemake(
            "build_industry_demand",
            simpl="",
            clusters=4,
            planning_horizons=2030,
            demand="AB",
        )

        sets_path_to_root("pypsa-earth-sec")

    no_years = int(snakemake.wildcards.planning_horizons) - int(
        snakemake.config["demand_data"]["base_year"]
    )

    cagr = read_csv_nafix(snakemake.input.industry_growth_cagr, index_col=0)

    countries = snakemake.config["countries"]
    # countries = ["EG", "BH"]

    for country in countries:
        if country not in cagr.index:
            cagr.loc[country] = cagr.loc["DEFAULT"]
            _logger.warning(
                "No industry growth data for "
                + country
                + " using default data instead."
            )

    cagr = cagr[cagr.index.isin(countries)]

    growth_factors = calculate_end_values(cagr)

    industry_base_totals = read_csv_nafix(
        snakemake.input["base_industry_totals"], index_col=[0, 1]
    )

    production_base = cagr.applymap(lambda x: 1)
    production_tom = production_base * growth_factors

    industry_totals = (production_tom * industry_base_totals).fillna(0)

    industry_util_factor = snakemake.config["sector"]["industry_util_factor"]

    # Load distribution keys
    keys_path = snakemake.input.industrial_distribution_key

    dist_keys = pd.read_csv(
        keys_path, index_col=0, keep_default_na=False, na_values=[""]
    )

    # material demand per node and industry (kton/a)
    nodal_production_tom = country_to_nodal(production_tom, dist_keys)

    clean_industry_list = [
        "iron and steel",
        "chemical and petrochemical",
        "non-ferrous metals",
        "non-metallic minerals",
        "transport equipment",
        "machinery",
        "mining and quarrying",
        "food and tobacco",
        "paper pulp and print",
        "wood and wood products",
        "textile and leather",
        "construction",
        "other",
    ]

    emission_factors = {  # Based on JR data following PyPSA-EUR
        "iron and steel": 0.025,
        "chemical and petrochemical": 0.51,  # taken from HVC including process and feedstock
        "non-ferrous metals": 1.5,  # taken from Aluminum primary
        "non-metallic minerals": 0.542,  # taken for cement
        "transport equipment": 0,
        "machinery": 0,
        "mining and quarrying": 0,  # assumed
        "food and tobacco": 0,
        "paper pulp and print": 0,
        "wood and wood products": 0,
        "textile and leather": 0,
        "construction": 0,  # assumed
        "other": 0,
    }

    geo_locs = pd.read_csv(
        snakemake.input.industrial_database,
        sep=",",
        header=0,
        keep_default_na=False,
        index_col=0,
    )
    geo_locs["capacity"] = pd.to_numeric(geo_locs.capacity)

    def match_technology(df):
        industry_mapping = {
            "Integrated steelworks": "iron and steel",
            "DRI + Electric arc": "iron and steel",
            "Electric arc": "iron and steel",
            "Cement": "non-metallic minerals",
            "HVC": "chemical and petrochemical",
            "Paper": "paper pulp and print",
        }

        df["industry"] = df["technology"].map(industry_mapping)
        return df

    geo_locs = match_technology(geo_locs).loc[countries]

    AL = read_csv_nafix("data/AL_production.csv", index_col=0)
    AL_prod_tom = AL["production[ktons/a]"].loc[countries]
    AL_emissions = AL_prod_tom * emission_factors["non-ferrous metals"]

    Steel_emissions = (
        geo_locs[geo_locs.industry == "iron and steel"]
        .groupby("country")
        .sum()
        .capacity
        * 1000
        * emission_factors["iron and steel"]
        * industry_util_factor
    )
    NMM_emissions = (
        geo_locs[geo_locs.industry == "non-metallic minerals"]
        .groupby("country")
        .sum()
        .capacity
        * 1000
        * emission_factors["non-metallic minerals"]
        * industry_util_factor
    )
    refinery_emissons = (
        geo_locs[geo_locs.industry == "chemical and petrochemical"]
        .groupby("country")
        .sum()
        .capacity
        * emission_factors["chemical and petrochemical"]
        * 0.136
        * 365
        * industry_util_factor
    )

    for country in countries:
        industry_base_totals.loc[(country, "process emissions"), :] = 0
        try:
            industry_base_totals.loc[
                (country, "process emissions"), "non-metallic minerals"
            ] = NMM_emissions.loc[country]
        except KeyError:
            pass

        try:
            industry_base_totals.loc[
                (country, "process emissions"), "iron and steel"
            ] = Steel_emissions.loc[country]
        except KeyError:
            pass  # # Code to handle the KeyError
        try:
            industry_base_totals.loc[
                (country, "process emissions"), "non-ferrous metals"
            ] = AL_emissions.loc[country]
        except KeyError:
            pass  # Code to handle the KeyError
        try:
            industry_base_totals.loc[
                (country, "process emissions"), "chemical and petrochemical"
            ] = refinery_emissons.loc[country]
        except KeyError:
            pass  # Code to handle the KeyError
    industry_base_totals = industry_base_totals.sort_index()

    all_carriers = [
        "electricity",
        "gas",
        "coal",
        "oil",
        "hydrogen",
        "biomass",
        "low-temperature heat",
    ]

    for country in countries:
        carriers_present = industry_base_totals.xs(country, level="country").index
        missing_carriers = set(all_carriers) - set(carriers_present)
        for carrier in missing_carriers:
            # Add the missing carrier with a value of 0
            industry_base_totals.loc[(country, carrier), :] = 0

    nodal_df = pd.DataFrame()

    for country in countries:
        nodal_production_tom_co = nodal_production_tom[
            nodal_production_tom.index.to_series().str.startswith(country)
        ]
        industry_base_totals_co = industry_base_totals.loc[country]
        # final energy consumption per node and industry (TWh/a)
        nodal_df_co = nodal_production_tom_co.dot(industry_base_totals_co.T)
        nodal_df = pd.concat([nodal_df, nodal_df_co])

    rename_sectors = {
        "elec": "electricity",
        "biomass": "solid biomass",
        "heat": "low-temperature heat",
    }
    nodal_df.rename(columns=rename_sectors, inplace=True)

    nodal_df.index.name = "TWh/a (MtCO2/a)"

    nodal_df.to_csv(
        snakemake.output.industrial_energy_demand_per_node, float_format="%.2f"
    )
