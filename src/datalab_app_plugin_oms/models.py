from pydantic import BaseModel, Field
from pydatalab.models.blocks import DataBlockResponse


class OMSSpeciesCalibrationResult(BaseModel):
    """Extracted calibration results for a single species."""

    peak_flux_nmol_s: float
    """Peak molar flux in nmol/s."""

    total_nmol: float
    """Total moles evolved over the full measurement, in nmol (trapezoidal integration)."""

    initial_rate_nmol_s: float | None = None
    """Mean molar flux in nmol/s over the user-defined rate window."""

    class Config:
        extra = "forbid"


class OMSMetadata(BaseModel):
    """Metadata extracted from an OMS block, suitable for database search."""

    flow_rate_mL_min: float | None = None
    """Carrier gas flow rate in mL/min used for the nmol/s conversion."""

    temperature_K: float | None = None
    """Temperature in Kelvin used for the ideal gas conversion."""

    pressure_Pa: float | None = None
    """Total pressure in Pa used for the ideal gas conversion."""

    rate_window_start_s: float | None = None
    """Start of the time window used to compute the initial rate, in seconds."""

    rate_window_end_s: float | None = None
    """End of the time window used to compute the initial rate, in seconds."""

    calibration_results: dict[str, OMSSpeciesCalibrationResult] | None = None
    """Per-species extracted values keyed by species name (e.g. 'O2', 'CO2')."""

    class Config:
        extra = "forbid"


class OMSModel(DataBlockResponse):
    """Response model for the OMS block — defines the full schema of self.data."""

    blocktype: str = Field("oms", const=True)

    # Instrument / conversion parameters
    flow_rate: float = 1.0
    """Carrier gas flow rate in mL/min."""

    temperature: float = 298.0
    """Temperature in Kelvin."""

    pressure: float = 1e5
    """Total pressure in Pa."""

    rate_t_start: float = 0.0
    """Start of the initial rate window in seconds."""

    rate_t_end: float = 1800.0
    """End of the initial rate window in seconds."""

    # .dat file parsing
    num_species: int | None = None
    """Number of species (excluding vacuum) for .dat file parsing."""

    species_names: list[str] | None = None
    """Optional species names for .dat file parsing."""

    # Validated metadata (overrides the base class `dict | None`)
    metadata: OMSMetadata | None = None
