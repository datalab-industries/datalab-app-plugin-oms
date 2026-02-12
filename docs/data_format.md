# OMS (Omnistar Mass Spectrometer) File Formats

This directory contains example data from a Mass Spectrometer in three formats.

## File Formats

### 1. `.csv` - Manual Export Format
- **Type**: ASCII, comma-separated values
- **Generation**: Manually exported snapshot from the instrument software
- **Structure**:
  - 27 header lines containing metadata (date, instrument ID, scan configuration, etc.)
  - Data section with columns: `Time`, `ms`, `CO2`, `O2`, `Ar`, `CO/N2`, `H2`, `C2H2`
- **Scans**: 6 species measured at specific masses:
  - Scan 1: mass 44.00 → CO2
  - Scan 2: mass 32.00 → O2
  - Scan 3: mass 40.00 → Ar
  - Scan 4: mass 28.00 → CO/N2 (both CO and N2 have mass 28)
  - Scan 5: mass 2.00  → H2
  - Scan 6: mass 26.00 → C2H2 (acetylene)

### 2. `.dat` - Binary Live Format
- **Type**: Binary
- **Generation**: Live-updating during data acquisition
- **Structure**:
  - Fixed-length 46-byte records
  - Each record starts with 'V1' marker (2 bytes)
  - Value stored as double-precision float (8 bytes) at offset 38 from V1 marker
  - **7 records per timepoint** (6 species + 1 additional measurement)
  - First V1 marker starts at byte 5 in the file

**Record order per timepoint**:
0. Total pressure or reference measurement (~1.7e-06, not in CSV export)
1. O2 (Scan 2, mass 32)
2. Ar (Scan 3, mass 40)
3. CO/N2 (Scan 4, mass 28)
4. H2 (Scan 5, mass 2)
5. C2H2 (Scan 6, mass 26)
6. CO2 (Scan 1, mass 44)

**Note**: The .dat file may contain more timepoints than the CSV if the CSV was exported mid-experiment.

### 3. `.exp` - ASCII Live Format
- **Type**: ASCII text with no line terminators
- **Generation**: Live-updating during data acquisition
- **Structure**:
  - Space-separated integer values
  - **7 values per timepoint** (matching .dat structure)
  - Same number of total values as .dat records (296 timepoints × 7 = 2072 values)

**Pattern observed**:
- Repeating sequence: `105 5 X 5 5 1 114` where X increments
- First timepoint: `105 5 5 5 5 1 114`
- Second timepoint: `105 5 327 5 5 1 114`
- Third timepoint: `105 5 649 5 5 1 114`
- Increment pattern: +322 at position 2

**Purpose**: Unknown - may be quality codes, instrument status, or encoded parameters.

## Data Correspondence

For the example file `2025_11_21_kdj_354_F`:
- **CSV**: 296 timepoints (manual export snapshot)
- **DAT**: 296 timepoints = 2072 records (296 × 7)
- **EXP**: 296 timepoints = 2072 values (296 × 7)

The .csv and .dat files contain mostly the same measurement data. However the .dat file doesn't contain the elapsed time of the experiment, nor the molar masses or chemical identities of the species being measured. This means that it's currently not possible to completely recreate the .csv data from the .dat data. The chemical species also differ in order from the .dat to the .csv. The .exp file doesn't contain any numerical data corresponding to the .csv file that I can find.

## Parsing

See `parse_oms_files.py` in the repository root for Python functions to read and convert between these formats.

Example usage:
```python
from parse_oms_files import parse_oms_dat, parse_oms_csv, dat_to_csv_format

# Parse binary .dat file
dat_df = parse_oms_dat("2025_11_21_kdj_354_F.dat")

# Convert to CSV-like format
csv_format = dat_to_csv_format(dat_df)

# Compare with actual CSV
csv_df = parse_oms_csv("2025_11_21_kdj_354_F.csv")
```

## Notes

- Values are in scientific notation (e.g., 1.33121e-09)
- The .dat and .exp files have an additional measurement (record 0) that is not exported to CSV
- This appears to be "vacuum"
- The species order differs between .dat (O2, Ar, CO/N2, H2, C2H2, CO2) and CSV (CO2, O2, Ar, CO/N2, H2, C2H2)
