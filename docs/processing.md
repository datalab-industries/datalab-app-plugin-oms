# Data Processing

## Calibration

The calibration file (`.xlsm`) maps OMS partial pressures (Torr) to species molar fractions using a linear relationship:

```
pct = (P_torr - intercept) / slope
```

This fraction is then converted to a molar flux (nmol/s) via the ideal gas law using the user-supplied flow rate, temperature, and pressure.

### CO2 sign convention

For CO2, the calibration intercept is **added** rather than subtracted:

```
pct = (P_torr + intercept) / slope
```

This is an empirical quirk of the calibration spreadsheet format used with this instrument — the intercept for CO2 is stored with the opposite sign convention to all other species. The code accounts for this explicitly.

## Baseline correction

After conversion to nmol/s, a baseline correction is applied to each species to remove the instrument background signal and slow drift.

### Method: percentile envelope

A rolling window percentile (default: 5th percentile, window size 101 points) is computed across the signal. This envelope tracks the lower bound of the signal — i.e., the background — while being largely insensitive to peaks of interest. The envelope is then smoothed with a Savitzky-Golay filter (default: window 201 points, polynomial order 2) to remove artefacts at the window edges.

The corrected signal is:

```
corrected = signal - baseline
```

### Why this method?

OMS signals typically consist of sharp peaks on top of a slowly varying background. A simple global offset would not account for drift over long experiments. A percentile-based rolling window was chosen over a mean or median because:

- The **5th percentile** selects the background floor rather than the signal midpoint, making it robust to large peaks that would bias a mean or median upward
- **Rolling** (local) computation handles slow drift that a single global estimate would miss
- The subsequent **Savitzky-Golay smoothing** removes the stepped artefacts that rolling percentile windows introduce at boundaries, giving a smooth baseline that can be visually validated in the plot

The raw signal and fitted baseline are shown together in the first plot panel, and the baseline-corrected signal in the second, so the user can visually verify the correction is sensible before interpreting the extracted values.
