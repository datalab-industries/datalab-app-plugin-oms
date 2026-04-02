"""Tests for OMS (Omnistar Mass Spectrometer) file parsing and calibration"""

from pathlib import Path

import numpy as np
import pytest

from datalab_app_plugin_oms.utils import (
    apply_calibration,
    parse_calibration_xlsm,
    parse_oms_csv,
    parse_oms_dat,
)

OMS_DATA_DIR = Path(__file__).parent.parent / "example_data"
OMS_TEST_FILE = OMS_DATA_DIR / "2025_11_21_kdj_354_F"
CALIBRATION_FILE = OMS_DATA_DIR / "Example_DEMS_Calibration.xlsm"

SPECIES = ["CO2", "O2", "Ar", "CO/N2", "H2", "C2H2"]


# ---------------------------------------------------------------------------
# CSV parsing
# ---------------------------------------------------------------------------


class TestParseCSV:
    def test_columns(self):
        df = parse_oms_csv(OMS_TEST_FILE.with_suffix(".csv"))
        assert "Time (s)" in df.columns
        for sp in SPECIES:
            assert sp in df.columns

    def test_shape(self):
        df = parse_oms_csv(OMS_TEST_FILE.with_suffix(".csv"))
        assert len(df) == 296

    def test_time_increases(self):
        df = parse_oms_csv(OMS_TEST_FILE.with_suffix(".csv"))
        assert (df["Time (s)"].diff().dropna() > 0).all()

    def test_no_nulls(self):
        df = parse_oms_csv(OMS_TEST_FILE.with_suffix(".csv"))
        assert df[SPECIES].isna().sum().sum() == 0


# ---------------------------------------------------------------------------
# DAT parsing — three pathways
# ---------------------------------------------------------------------------


class TestParseDat:
    def test_via_companion_csv(self):
        """Species names and count inferred from companion CSV."""
        df = parse_oms_dat(
            OMS_TEST_FILE.with_suffix(".dat"),
            csv_filepath=OMS_TEST_FILE.with_suffix(".csv"),
        )
        assert "Data Point" in df.columns
        assert "Vacuum" in df.columns
        for sp in SPECIES:
            assert sp in df.columns
        assert len(df) == 296

    def test_via_num_species(self):
        """Species count provided explicitly, names auto-detected via value matching."""
        df = parse_oms_dat(OMS_TEST_FILE.with_suffix(".dat"), num_species=6)
        assert "Data Point" in df.columns
        assert "Vacuum" in df.columns
        for sp in SPECIES:
            assert sp in df.columns
        assert len(df) == 296

    def test_via_species_names(self):
        """Species names provided explicitly."""
        df = parse_oms_dat(
            OMS_TEST_FILE.with_suffix(".dat"),
            num_species=6,
            species_names=SPECIES,
        )
        assert "Data Point" in df.columns
        assert "Vacuum" in df.columns
        for sp in SPECIES:
            assert sp in df.columns
        assert len(df) == 296

    def test_data_point_sequential(self):
        df = parse_oms_dat(OMS_TEST_FILE.with_suffix(".dat"), num_species=6)
        assert list(df["Data Point"]) == list(range(len(df)))

    def test_no_time_column(self):
        df = parse_oms_dat(OMS_TEST_FILE.with_suffix(".dat"), num_species=6)
        assert "Time (s)" not in df.columns

    def test_wrong_num_species_raises(self):
        with pytest.raises(ValueError, match="not evenly divisible"):
            parse_oms_dat(OMS_TEST_FILE.with_suffix(".dat"), num_species=5)


# ---------------------------------------------------------------------------
# CSV vs DAT data agreement
# ---------------------------------------------------------------------------


class TestCSVvsDat:
    @pytest.fixture(scope="class")
    def csv_df(self):
        return parse_oms_csv(OMS_TEST_FILE.with_suffix(".csv"))

    @pytest.fixture(scope="class")
    def dat_df(self):
        return parse_oms_dat(
            OMS_TEST_FILE.with_suffix(".dat"),
            csv_filepath=OMS_TEST_FILE.with_suffix(".csv"),
        )

    def test_same_row_count(self, csv_df, dat_df):
        assert len(csv_df) == len(dat_df)

    @pytest.mark.parametrize("species", SPECIES)
    def test_species_values_match(self, csv_df, dat_df, species):
        """Values from DAT should match CSV to floating-point precision."""
        assert np.allclose(csv_df[species].values, dat_df[species].values, rtol=1e-9, atol=1e-12)

    def test_dat_has_vacuum_csv_does_not(self, csv_df, dat_df):
        assert "Vacuum" in dat_df.columns
        assert "Vacuum" not in csv_df.columns


# ---------------------------------------------------------------------------
# Calibration file parsing
# ---------------------------------------------------------------------------


class TestParseCalibration:
    @pytest.fixture(scope="class")
    def calibration(self):
        return parse_calibration_xlsm(CALIBRATION_FILE)

    def test_species_present(self, calibration):
        assert "O2" in calibration
        assert "CO2" in calibration

    def test_slope_intercept_keys(self, calibration):
        for species, cal in calibration.items():
            assert "slope" in cal
            assert "intercept" in cal

    def test_known_values(self, calibration):
        """Slope and intercept should match known values from Example_DEMS_Calibration.xlsm."""
        assert np.isclose(calibration["O2"]["slope"], 1.0711656549520764e-07, rtol=1e-6)
        assert np.isclose(calibration["O2"]["intercept"], 9.314283706070297e-10, rtol=1e-6)
        assert np.isclose(calibration["CO2"]["slope"], 1.26309963099631e-06, rtol=1e-6)
        assert np.isclose(calibration["CO2"]["intercept"], -7.020295202952036e-09, rtol=1e-6)


# ---------------------------------------------------------------------------
# Calibration application
# ---------------------------------------------------------------------------


class TestApplyCalibration:
    @pytest.fixture(scope="class")
    def calibration(self):
        return parse_calibration_xlsm(CALIBRATION_FILE)

    @pytest.fixture(scope="class")
    def csv_df(self):
        return parse_oms_csv(OMS_TEST_FILE.with_suffix(".csv"))

    @pytest.fixture(scope="class")
    def result(self, csv_df, calibration):
        nmol_df, summary = apply_calibration(csv_df, calibration)
        return nmol_df, summary

    def test_returns_dataframe_and_summary(self, result):
        nmol_df, summary = result
        assert nmol_df is not None
        assert isinstance(summary, dict)

    def test_nmol_df_columns(self, result):
        nmol_df, _ = result
        assert "Time (s)" in nmol_df.columns
        for sp in ["O2", "CO2"]:
            assert f"{sp}_nmol_s" in nmol_df.columns
            assert f"{sp}_raw_nmol_s" in nmol_df.columns
            assert f"{sp}_baseline" in nmol_df.columns

    def test_summary_keys(self, result):
        _, summary = result
        for sp in ["O2", "CO2"]:
            assert sp in summary
            assert "peak_flux_nmol_s" in summary[sp]
            assert "total_nmol" in summary[sp]
            assert "initial_rate_nmol_s" in summary[sp]

    def test_known_summary_values(self, result):
        """Summary values should match known output for this calibration + data combination."""
        _, summary = result
        assert np.isclose(summary["O2"]["peak_flux_nmol_s"], 204.026, rtol=1e-3)
        assert np.isclose(summary["O2"]["total_nmol"], 40785.8, rtol=1e-3)
        assert np.isclose(summary["CO2"]["peak_flux_nmol_s"], 0.6724, rtol=1e-3)
        assert np.isclose(summary["CO2"]["total_nmol"], 1030.4, rtol=1e-3)

    def test_missing_time_column_returns_none(self, calibration):
        """apply_calibration should return (None, {}) if no Time (s) column."""
        dat_df = parse_oms_dat(OMS_TEST_FILE.with_suffix(".dat"), num_species=6)
        nmol_df, summary = apply_calibration(dat_df, calibration)
        assert nmol_df is None
        assert summary == {}


# ---------------------------------------------------------------------------
# Block creation
# ---------------------------------------------------------------------------


class TestOMSBlock:
    def test_block_creation(self):
        from datalab_app_plugin_oms import OMSBlock

        block = OMSBlock(item_id="test-id")
        assert block.blocktype == "oms"
        assert block.name == "OMS"

    def test_defaults_populated(self):
        from datalab_app_plugin_oms import OMSBlock

        block = OMSBlock(item_id="test-id")
        assert block.data["flow_rate"] == 1.0
        assert block.data["temperature"] == 298.0
        assert block.data["pressure"] == 1e5
        assert block.data["rate_t_start"] == 0.0
        assert block.data["rate_t_end"] == 1800.0

    def test_accepted_extensions(self):
        from datalab_app_plugin_oms import OMSBlock

        block = OMSBlock(item_id="test-id")
        assert ".csv" in block.accepted_file_extensions
        assert ".dat" in block.accepted_file_extensions
        assert ".xlsm" in block.accepted_file_extensions
