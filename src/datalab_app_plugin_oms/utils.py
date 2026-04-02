"""
Utility functions for parsing OMS (Omnistar Mass Spectrometer) files

OMS files come in three formats:
1. .csv - Manual export with headers
2. .dat - Binary live-updating format (46-byte records)
3. .exp - ASCII live-updating format (space-separated integers)

The .dat and .exp files continue updating during data acquisition and may contain
more timepoints than a manually exported CSV snapshot.
"""

import struct
from pathlib import Path

import numpy as np
import pandas as pd
from pydatalab.logger import LOGGER
from scipy.signal import savgol_filter


def _auto_detect_num_species(
    total_records: int, data: bytes, first_v1: int, max_species: int = 20
) -> int | None:
    """
    Auto-detect the number of species by testing which configuration produces
    the smoothest/most stable signal.

    Args:
        total_records: Total number of V1 records in the DAT file
        data: Raw binary data from the DAT file
        first_v1: Position of the first V1 marker
        max_species: Maximum number of species to test (default 20)

    Returns:
        The detected number of species (excluding vacuum), or None if detection fails
    """
    # Find all valid divisors (records_per_timepoint values that divide evenly)
    valid_configs = []
    for records_per_timepoint in range(2, max_species + 2):  # +1 for vacuum, so 2 to max_species+1
        if total_records % records_per_timepoint == 0:
            num_species = records_per_timepoint - 1  # -1 for vacuum
            num_timepoints = total_records // records_per_timepoint
            valid_configs.append((num_species, num_timepoints, records_per_timepoint))

    if not valid_configs:
        return None

    # If only one valid configuration, return it
    if len(valid_configs) == 1:
        return valid_configs[0][0]

    # Multiple valid configurations - analyze each to find the smoothest
    config_scores = []

    for num_species, num_timepoints, records_per_timepoint in valid_configs:
        # Parse the data with this configuration
        values = []
        pos = first_v1
        for _ in range(total_records):
            value_pos = pos + 38
            value = struct.unpack("<d", data[value_pos : value_pos + 8])[0]
            values.append(value)
            pos += 46

        # Reshape into timepoints x species
        values_array = np.array(values).reshape(num_timepoints, records_per_timepoint)

        # Calculate smoothness score for each species (column)
        # Lower score = smoother signal = more likely correct
        total_deviation = 0
        for species_idx in range(records_per_timepoint):
            species_values = values_array[:, species_idx]
            # Calculate standard deviation of differences (measure of smoothness)
            if len(species_values) > 1:
                diffs = np.diff(species_values)
                deviation = np.std(diffs)
                total_deviation += deviation

        # Average deviation across all species
        avg_deviation = total_deviation / records_per_timepoint

        config_scores.append((num_species, avg_deviation))

    LOGGER.debug(f"Smoothness calculated: {config_scores}")
    # Choose configuration with lowest average deviation (smoothest signals)
    config_scores.sort(key=lambda x: x[1])  # Sort by deviation ascending
    return config_scores[0][0]


def parse_oms_dat(
    filepath: str | Path,
    csv_filepath: str | Path | None = None,
    num_species: int | None = None,
    species_names: list[str] | None = None,
) -> pd.DataFrame:
    """
    Parse OMS .dat binary file

    The .dat format contains 46-byte binary records, each starting with a 'V1' marker.
    The number of records per timepoint is variable depending on instrument configuration.

    File structure:
        - 46-byte records starting with 'V1' marker (2 bytes)
        - Value stored as double-precision float (8 bytes) at offset 38 from V1
        - First V1 marker starts at byte 5 in the file

    Record order per timepoint:
        0: Vacuum (total pressure measurement)
        1-n: Species 1 through n (measured species)

    To parse the file, you must provide EITHER:
    1. csv_filepath: Path to companion .csv file (to auto-detect number of species from columns)
    2. num_species: Number of species being measured (excluding vacuum)

    Args:
        filepath: Path to .dat file
        csv_filepath: Path to companion .csv file (used to determine number of species from columns).
                     If not provided and num_species is not specified, will look for a
                     .csv file with the same base name.
        num_species: Number of species being measured (excluding the vacuum measurement).
                    If provided, csv_filepath is not required.
        species_names: Optional list of names for the species. Length must equal num_species.
                      If not provided, species will be named "Species 1", "Species 2", etc.

    Returns:
        DataFrame with columns:
        - Data Point: Sequential measurement index (0, 1, 2, ...)
          NOTE: .dat files do not contain timestamp information
        - Vacuum: Total pressure measurement
        - Species columns (named or numbered)

    Raises:
        ValueError: If no V1 markers found in file, or if configuration is invalid
        FileNotFoundError: If companion CSV file not found when csv_filepath is used
    """
    filepath = Path(filepath)

    # Determine number of species and get CSV data for name matching
    # Always try to find and use the CSV file first
    csv_data = None
    csv_species_columns = None

    # Try to locate the CSV file if not explicitly provided
    if csv_filepath is None:
        csv_filepath = filepath.with_suffix(".csv")
    else:
        csv_filepath = Path(csv_filepath)

    # Read binary .dat file first to count records
    with open(filepath, "rb") as f:
        data = f.read()

    # Find first V1 marker
    first_v1 = data.find(b"V1")
    if first_v1 == -1:
        raise ValueError("No V1 markers found in .dat file")

    # Count total V1 markers
    total_records = 0
    pos = first_v1
    while pos + 46 <= len(data):
        if data[pos : pos + 2] != b"V1":
            break
        total_records += 1
        pos += 46

    # Determine species count with correct priority: num_species > CSV > auto-detect
    if num_species is not None:
        # Priority 1: User provided num_species - use it
        records_per_timepoint = num_species + 1  # +1 for vacuum

        # Still try to load CSV for name matching only
        if csv_filepath.exists():
            csv_data = parse_oms_csv(csv_filepath)
            csv_species_columns = [
                col
                for col in csv_data.columns
                if col not in ["Time", "ms", "Time (s)", "Data Point", "timepoint"]
            ]
    elif csv_filepath.exists():
        # Priority 2: CSV file exists - use it for species count and name matching
        csv_data = parse_oms_csv(csv_filepath)
        csv_species_columns = [
            col
            for col in csv_data.columns
            if col not in ["Time", "ms", "Time (s)", "Data Point", "timepoint"]
        ]
        num_species = len(csv_species_columns)
        records_per_timepoint = num_species + 1  # +1 for vacuum
    else:
        # Priority 3: No num_species and no CSV - try auto-detection
        detected_num_species = _auto_detect_num_species(total_records, data, first_v1)
        if detected_num_species is None:
            raise ValueError(
                f"Could not auto-detect number of species from {total_records} records.\n"
                f"Please provide either a CSV file or specify num_species parameter."
            )
        num_species = detected_num_species
        records_per_timepoint = num_species + 1

    # Validate configuration
    if total_records % records_per_timepoint != 0:
        raise ValueError(
            f"Total records ({total_records}) is not evenly divisible by "
            f"records per timepoint ({records_per_timepoint} = {num_species} species + 1 vacuum). "
            f"Check that num_species is correct."
        )

    # Validate species_names if provided
    if species_names is not None:
        if len(species_names) != num_species:
            raise ValueError(
                f"Length of species_names ({len(species_names)}) must equal "
                f"num_species ({num_species})"
            )

    # Generate species mapping: position 0 is "Vacuum", rest are named or numbered
    species_map = {0: "Vacuum"}
    for i in range(1, records_per_timepoint):
        if species_names is not None:
            species_map[i] = species_names[i - 1]  # -1 because species_names doesn't include vacuum
        else:
            species_map[i] = f"Species {i}"

    records = []

    # Parse all records starting from first V1
    pos = first_v1
    record_num = 0

    while pos + 46 <= len(data):
        # Check for V1 marker
        if data[pos : pos + 2] != b"V1":
            break

        # Read value at offset 38 (double-precision float, 8 bytes)
        value_pos = pos + 38
        value = struct.unpack("<d", data[value_pos : value_pos + 8])[0]

        # Determine data point and species (data_point being a row in the csv)
        data_point = record_num // records_per_timepoint
        species_idx = record_num % records_per_timepoint
        species = species_map.get(species_idx, f"Unknown_{species_idx}")

        records.append(
            {
                "data_point": data_point,
                "species": species,
                "value": value,
            }
        )

        pos += 46
        record_num += 1

    # Convert to DataFrame and pivot to wide format
    df = pd.DataFrame(records)

    # Pivot to wide format (one row per data_point, one column per species)
    pivot_df = df.pivot(index="data_point", columns="species", values="value")

    # If we have CSV data, match species names by comparing first data point values
    if csv_data is not None and csv_species_columns is not None and len(csv_data) > 0:
        # Get the first row of CSV data for each species
        csv_first_values = {}
        for col in csv_species_columns:
            csv_first_values[col] = csv_data[col].iloc[0]

        # Get the first row of DAT data for each species (excluding Vacuum)
        dat_species_cols = [col for col in pivot_df.columns if col != "Vacuum"]
        dat_first_values = {}
        for col in dat_species_cols:
            dat_first_values[col] = pivot_df[col].iloc[0]

        # Match DAT species to CSV species by finding matching first values
        # Since both files come from the same instrument, values should match to high precision
        species_name_mapping = {}
        all_matches_good = True

        for dat_species, dat_value in dat_first_values.items():
            best_match = None
            best_diff = float("inf")

            # Find the closest match first
            for csv_species, csv_value in csv_first_values.items():
                diff = abs(dat_value - csv_value)
                if diff < best_diff:
                    best_diff = diff
                    best_match = csv_species

            # Verify the best match is within instrument precision
            if best_match is not None:
                csv_value = csv_first_values[best_match]
                # Use numpy.isclose for robust floating point comparison
                # rtol=1e-9, atol=1e-12 handles typical instrument precision
                if np.isclose(dat_value, csv_value, rtol=1e-9, atol=1e-12):
                    species_name_mapping[dat_species] = best_match
                    # Remove matched CSV species to avoid duplicate matches
                    csv_first_values.pop(best_match, None)
                else:
                    # Best match is not close enough - cannot reliably map species names
                    all_matches_good = False
                    break
            else:
                all_matches_good = False
                break

        # Only rename columns if all matches were within tolerance
        if all_matches_good and len(species_name_mapping) == len(dat_first_values):
            pivot_df = pivot_df.rename(columns=species_name_mapping)

    # Order columns: Vacuum first, then the species columns
    column_order = ["Vacuum"] + [col for col in pivot_df.columns if col != "Vacuum"]
    result_df = pivot_df[column_order].reset_index()

    # .dat files don't contain real time information, so we use data_point index
    result_df["Data Point"] = result_df["data_point"]

    # Reorder to put Data Point first
    cols = ["Data Point"] + [
        col for col in result_df.columns if col not in ["Data Point", "data_point"]
    ]
    result_df = result_df[cols]

    return result_df


def parse_oms_exp(filepath: str | Path) -> pd.DataFrame:
    """
    Parse OMS .exp ASCII file

    The .exp format contains space-separated integer codes that update live during
    data acquisition. There are 7 values per timepoint matching the .dat structure.

    Pattern observed:
        - Repeating sequence: 105 5 X 5 5 1 114
        - X increments by 322 each timepoint
        - Purpose unclear - may be quality codes, instrument status, or encoded parameters

    Args:
        filepath: Path to .exp file

    Returns:
        DataFrame with columns:
        - timepoint: Timepoint index
        - position_0 through position_6: The 7 integer values for each timepoint

    Note:
        The exact meaning of these values is not fully documented and may require
        consultation with instrument manufacturer documentation.
    """
    filepath = Path(filepath)

    with open(filepath) as f:
        content = f.read()

    # Split by whitespace
    numbers = content.split()

    # Parse numbers into groups of 7 (one group per timepoint)
    timepoint_data: dict[int, dict[str, int]] = {}

    for i, num_str in enumerate(numbers):
        try:
            value = int(num_str)
            timepoint = i // 7
            position_in_group = i % 7

            # Initialize timepoint dict if not exists
            if timepoint not in timepoint_data:
                timepoint_data[timepoint] = {}

            # Add value to correct position
            timepoint_data[timepoint][f"position_{position_in_group}"] = value

        except ValueError:
            # Skip non-integer values
            continue

    # Convert to DataFrame
    if len(timepoint_data) > 0:
        result_df = pd.DataFrame.from_dict(timepoint_data, orient="index")
        result_df.index.name = "timepoint"
        result_df = result_df.reset_index()

        # Ensure position columns are in order
        position_cols = [f"position_{i}" for i in range(7) if f"position_{i}" in result_df.columns]
        result_df = result_df[["timepoint"] + position_cols]
    else:
        result_df = pd.DataFrame()

    return result_df


def parse_oms_csv(filename: str | Path, auto_detect_header: bool = True) -> pd.DataFrame:
    """
    Parse .csv OMS data from mass spectrometer

    The file consists of a header with metadata. The header size is specified
    in a line containing "header" (e.g., "header",0000000026,"lines"), normally on line 2.

    Args:
        filename: Path to the .csv file
        auto_detect_header: If True, searches first 10 lines for header size.
                           If False, assumes header size of 27 lines.

    Returns:
        OMS dataframe with time and species concentration columns.
        Includes 'Time (s)' column converted from 'ms' column.

    Raises:
        ValueError: If auto_detect_header=True and header size cannot be found
    """
    filename = Path(filename)

    if auto_detect_header:
        # Search the first 10 lines for the header size
        header_size = None
        with open(filename) as f:
            for i in range(10):
                line = f.readline()
                if not line:
                    break
                if "header" in line.lower():
                    # Parse the header size from the line
                    # Format: "header",0000000026,"lines"
                    header_parts = line.strip().split(",")
                    header_size = int(header_parts[1])
                    break

        if header_size is None:
            raise ValueError("Could not find header size information in the first 10 lines")
    else:
        header_size = 27

    # Read the data, skipping the header (+1 as header seems to appear one line lower)
    oms_data = pd.read_csv(filename, skiprows=header_size + 1)

    # Drop any unnamed columns (caused by trailing commas in the CSV)
    oms_data = oms_data.loc[:, ~oms_data.columns.str.contains("^Unnamed")]

    # Convert milliseconds to seconds
    if "ms" in oms_data.columns:
        oms_data["Time (s)"] = oms_data["ms"] / 1000.0

    return oms_data


def parse_calibration_xlsm(filepath: str | Path) -> dict[str, dict[str, float]]:
    """
    Parse calibration slope and intercept values from an OMS calibration .xlsm file.

    Reads the sheet named "Calibration" (falling back to the first sheet if that name is
    absent), which contains linear calibration data mapping
    OMS partial pressure (Torr) to species percentage::

        y = Pressure / Torr  |  x = % O2   |  ...  |  y = Pressure / Torr  |  x = % CO2
        ...data rows...
        Slope                |  <value>     |  ...  |  Slope                |  <value>
        Intercept            |  <value>     |  ...  |  Intercept            |  <value>

    Species are detected from headers of the form ``x = % <Species>``.
    The slope and intercept are taken from rows 7 and 8 of the same column.

    Args:
        filepath: Path to the .xlsm calibration file.

    Returns:
        Dict mapping species name to calibration parameters, e.g.::

            {"O2": {"slope": 9.038e-8, "intercept": 1.532e-9},
             "CO2": {"slope": 1.165e-6, "intercept": -3.157e-9}}

    Raises:
        ValueError: If no calibration species can be found in the file.
    """
    filepath = Path(filepath)

    # Prefer the sheet named "Calibration"; fall back to the first sheet if absent.
    xl = pd.ExcelFile(filepath)
    sheet = "Calibration" if "Calibration" in xl.sheet_names else 0
    df = pd.read_excel(xl, sheet_name=sheet, header=None)

    # Row 0 (Excel row 1): find columns whose value matches "x = % <Species>"
    header_row = df.iloc[0]
    calibration: dict[str, dict[str, float]] = {}

    for col_idx, val in header_row.items():
        if not isinstance(val, str):
            continue
        val_stripped = val.strip()
        if not val_stripped.startswith("x = %"):
            continue
        species = val_stripped[len("x = %") :].strip()
        try:
            slope = df.iloc[6, col_idx]  # Row 7 (0-indexed: 6)
            intercept = df.iloc[7, col_idx]  # Row 8 (0-indexed: 7)
        except IndexError:
            raise ValueError(
                f"Calibration file '{filepath.name}' has fewer than 8 rows — "
                "expected slope in row 7 and intercept in row 8."
            )
        if pd.isna(slope) or pd.isna(intercept):
            LOGGER.warning(
                f"Calibration: missing slope/intercept for '{species}' in column {col_idx} — skipping."
            )
            continue
        calibration[species] = {"slope": float(slope), "intercept": float(intercept)}
        LOGGER.debug(f"Calibration: {species} slope={slope}, intercept={intercept}")

    if not calibration:
        raise ValueError(
            f"No calibration species found in {filepath.name}. "
            "Expected headers of the form 'x = % <Species>' in row 1 of Sheet 1."
        )

    return calibration


def apply_calibration(
    oms_df: pd.DataFrame,
    calibration: dict[str, dict[str, float]],
    flow_rate: float = 1.0,
    T: float = 298.0,
    P_total: float = 1e5,
    rate_t_start: float = 0.0,
    rate_t_end: float = 1800.0,
) -> tuple[pd.DataFrame | None, dict[str, dict[str, float]]]:
    """
    Convert OMS partial-pressure data to nmol/s using calibration slope/intercept values.

    Conversion chain (per species, per timepoint):

    1. Fraction: ``pct = (P_torr - intercept) / slope``
    2. Volumetric flow: ``mL/min = flow_rate * pct``
    3. SI flow: ``m³/s = mL/min × 1e-6 / 60``
    4. Molar flow: ``mol/s = P_total × m³/s / (R × T)``  (ideal gas PV = nRT)
    5. Scaled: ``nmol/s = mol/s × 1e9``

    Negative values are preserved — a value below zero indicates the species partial
    pressure is below the calibration baseline (e.g. the species is being consumed).

    Integration uses the trapezoidal rule over ``Time (s)`` to give total nmol.

    Args:
        oms_df: DataFrame from :func:`parse_oms_csv` — must contain a ``Time (s)``
            column and species pressure columns in Torr.
        calibration: Dict from :func:`parse_calibration_xlsm`.
        flow_rate: Total carrier gas flow rate in mL/min (default 1.0).
        T: Temperature in Kelvin (default 298.0 K).
        P_total: Total pressure in Pa (default 1e5 Pa = 1 bar).

    Returns:
        Tuple of ``(nmol_df, summary)`` where:

        - ``nmol_df``: DataFrame with ``Time (s)`` and ``<Species>_nmol_s`` columns,
          or ``None`` if no species overlap between calibration and OMS data.
        - ``summary``: ``{species: {"peak_flux_nmol_s": float, "total_nmol": float}}``.
    """
    _R = 8.314  # J / (mol·K)

    if "Time (s)" not in oms_df.columns:
        return None, {}

    time_s = oms_df["Time (s)"].to_numpy(dtype=float)
    result_cols: dict[str, np.ndarray] = {}
    summary: dict[str, dict[str, float]] = {}

    for species, cal in calibration.items():
        if species not in oms_df.columns:
            LOGGER.debug(f"Calibration species '{species}' not found in OMS data — skipping.")
            continue
        p_torr = oms_df[species].to_numpy(dtype=float)
        # In example excel sheet the intercept is added rather than subtracted as expected for y=mx+c
        if species == "CO2":
            pct = (p_torr + cal["intercept"]) / cal["slope"]
        else:
            pct = (p_torr - cal["intercept"]) / cal["slope"]
        mol_s = P_total * (flow_rate * pct * 1e-6 / 60.0) / (_R * T)
        nmol_s = mol_s * 1e9
        raw_col_name = f"{species}_raw_nmol_s"
        result_cols[raw_col_name] = nmol_s
        nmol_s, baseline = percentile_envelope_baseline(nmol_s, window_size=101, percentile=5)
        col_name = f"{species}_nmol_s"
        baseline_col_name = f"{species}_baseline"
        result_cols[col_name] = nmol_s
        result_cols[baseline_col_name] = baseline

        peak_flux = float(nmol_s.max())
        total_nmol = float(np.trapz(nmol_s, time_s))
        mask = (time_s >= rate_t_start) & (time_s <= rate_t_end)
        initial_rate = float(nmol_s[mask].mean()) if mask.any() else float("nan")
        summary[species] = {
            "peak_flux_nmol_s": peak_flux,
            "total_nmol": total_nmol,
            "initial_rate_nmol_s": initial_rate,
        }
        LOGGER.debug(
            f"Calibration applied: {species} peak={peak_flux:.4g} nmol/s, total={total_nmol:.4g} nmol"
        )

    if not result_cols:
        raise ValueError(
            f"No overlap between calibration species ({list(calibration.keys())}) "
            f"and OMS data columns ({[c for c in oms_df.columns if c != 'Time (s)']}). "
            "Check that species names in the calibration file match the OMS data."
        )

    nmol_df = pd.DataFrame({"Time (s)": time_s, **result_cols})
    return nmol_df, summary


def percentile_envelope_baseline(
    signal, window_size=101, percentile=5, smooth=True, smooth_window=201, polyorder=2
):
    s = pd.Series(signal)

    # Step 1: percentile envelope
    baseline = (
        s.rolling(window=window_size, center=True, min_periods=1)
        .quantile(percentile / 100.0)
        .values
    )

    # Step 2: optional smoothing
    # savgol_filter requires smooth_window <= len(baseline), smooth_window odd, polyorder < smooth_window
    if smooth:
        n = len(baseline)
        sw = min(smooth_window, n)
        if sw % 2 == 0:
            sw -= 1
        if sw > polyorder and sw >= 1:
            baseline = savgol_filter(baseline, sw, polyorder)
        # else: signal too short to smooth, use unsmoothed baseline

    # Step 3: subtract
    corrected = signal - baseline

    return corrected, baseline
