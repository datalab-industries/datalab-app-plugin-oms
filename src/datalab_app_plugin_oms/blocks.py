from pathlib import Path

import bokeh.embed
import pandas as pd
from bokeh.events import DoubleTap
from bokeh.layouts import column, row
from bokeh.models import (
    BoxAnnotation,
    Button,
    CustomJS,
    Div,
    HoverTool,
    Legend,
    Panel,
    Tabs,
    TextInput,
)
from bokeh.palettes import Category10_10
from bokeh.plotting import ColumnDataSource, figure
from pydatalab.blocks.base import DataBlock, event, generate_js_callback_single_float_parameter
from pydatalab.bokeh_plots import DATALAB_BOKEH_THEME, TOOLS
from pydatalab.file_utils import get_file_info_by_id
from pydatalab.logger import LOGGER
from pydatalab.mongo import flask_mongo

from datalab_app_plugin_oms.utils import (
    apply_calibration,
    parse_calibration_xlsm,
    parse_oms_csv,
    parse_oms_dat,
)


class OMSBlock(DataBlock):
    blocktype = "oms"
    name = "OMS"
    description = "Block for plotting OMS time series data."
    accepted_file_extensions: tuple[str, ...] = (".csv", ".dat", ".exp", ".xlsm")
    multi_file = True
    defaults = {
        "flow_rate": 1.0,
        "temperature": 298.0,
        "pressure": 1e5,
        "rate_t_start": 0.0,
        "rate_t_end": 1800.0,
    }

    @property
    def plot_functions(self):
        return (self.generate_oms_plot,)

    def _find_companion_csv(self, current_file_path: Path) -> Path | None:
        """
        Search for a companion CSV file with the same base name in the item's attached files.

        Args:
            current_file_path: Path to the current file (e.g., .dat file)

        Returns:
            Path to companion CSV file if found, None otherwise
        """
        if "item_id" not in self.data:
            return None

        # Get the base name of the current file (without extension)
        current_base_name = current_file_path.stem

        # Query the database for all files attached to this item
        item_info = flask_mongo.db.items.find_one(
            {"item_id": self.data["item_id"]},
            projection={"file_ObjectIds": 1},
        )

        if not item_info or "file_ObjectIds" not in item_info:
            return None

        # Search through all attached files for a CSV with the same base name
        for file_id in item_info["file_ObjectIds"]:
            try:
                file_info = get_file_info_by_id(file_id, update_if_live=False)
                file_path = Path(file_info["location"])

                # Check if this is a CSV file with the same base name
                if file_path.suffix.lower() == ".csv" and file_path.stem == current_base_name:
                    LOGGER.debug(f"Found companion CSV file: {file_path} for {current_file_path}")
                    return file_path

            except OSError:
                LOGGER.warning(f"Missing file found in database but not on disk: {file_id}")
                continue

        return None

    @event()
    def set_num_species(self, num_species: str):
        """
        Updates self.data with the user-inputted number of species for .dat file parsing

        Args:
            num_species: positive integer as a string representing the number of species (excluding vacuum) in the .dat file
        """
        try:
            num_species_int = int(num_species)
            if num_species_int < 1:
                raise ValueError("Number of species must be at least 1")
            LOGGER.debug(f"Setting num_species to {num_species_int}")
            self.data["num_species"] = num_species_int
        except ValueError as e:
            raise ValueError(f"Invalid num_species. Must be a positive integer: {e}")

    @event()
    def set_species_names(self, species_names: str):
        """
        Updates self.data with user-inputted species names for .dat file parsing.

        Args:
            species_names: comma-separated string of species names
        """
        names = [name.strip() for name in species_names.split(",") if name.strip()]
        if not names:
            raise ValueError("No valid species names provided")
        LOGGER.debug(f"Setting species_names to {names}")
        self.data["species_names"] = names
        self.data["num_species"] = len(names)

    @event()
    def reset_species_settings(self, **kwargs):
        """Clears both num_species and species_names from stored data.

        Uses empty list / 0 rather than None because to_web() strips None values
        (exclude_none=True) and the frontend uses Object.assign which won't delete
        keys missing from the response.
        """
        LOGGER.debug("Resetting species settings")
        self.data["num_species"] = 0
        self.data["species_names"] = []

    @event()
    def set_flow_rate(self, flow_rate: str):
        """Set the carrier gas flow rate used for nmol/s calibration.

        Args:
            flow_rate: Flow rate in mL/min as a string. Must be a positive number.
        """
        try:
            value = float(flow_rate)
            if value <= 0:
                raise ValueError("Flow rate must be positive")
            self.data["flow_rate"] = value
        except ValueError as e:
            raise ValueError(f"Invalid flow_rate. Must be a positive number: {e}")

    @event()
    def set_temperature(self, temperature: str):
        """Set the temperature used for the ideal gas conversion.

        Args:
            temperature: Temperature in Kelvin as a string. Must be a positive number.
        """
        try:
            value = float(temperature)
            if value <= 0:
                raise ValueError("Temperature must be positive")
            self.data["temperature"] = value
        except ValueError as e:
            raise ValueError(f"Invalid temperature. Must be a positive number in Kelvin: {e}")

    @event()
    def set_pressure(self, pressure: str):
        """Set the total pressure used for the ideal gas conversion.

        Args:
            pressure: Total pressure in Pa as a string. Must be a positive number.
        """
        try:
            value = float(pressure)
            if value <= 0:
                raise ValueError("Pressure must be positive")
            self.data["pressure"] = value
        except ValueError as e:
            raise ValueError(f"Invalid pressure. Must be a positive number in Pa: {e}")

    @event()
    def set_rate_t_start(self, rate_t_start: str):
        """Set the start of the time window for initial rate calculation.

        Args:
            rate_t_start: Start time in seconds as a string. Must be non-negative.
        """
        try:
            value = float(rate_t_start)
            if value < 0:
                raise ValueError("Start time must be non-negative")
            self.data["rate_t_start"] = value
        except ValueError as e:
            raise ValueError(f"Invalid rate_t_start. Must be a non-negative number: {e}")

    @event()
    def set_rate_t_end(self, rate_t_end: str):
        """Set the end of the time window for initial rate calculation.

        Args:
            rate_t_end: End time in seconds as a string. Must be positive.
        """
        try:
            value = float(rate_t_end)
            if value <= 0:
                raise ValueError("End time must be positive")
            self.data["rate_t_end"] = value
        except ValueError as e:
            raise ValueError(f"Invalid rate_t_end. Must be a positive number: {e}")

    def _create_error_div(self, message: str) -> bokeh.layouts.layout:
        """Return a simple Bokeh layout containing an error message."""
        return column(
            Div(
                text=f'<p style="color:#d9534f;"><b>Error:</b> {message}</p>',
                sizing_mode="stretch_width",
                margin=(5, 5, 5, 5),
            ),
            sizing_mode="stretch_width",
        )

    def _make_reset_button(self) -> Button:
        """Create a reset button that clears num_species and species_names."""
        reset_button = Button(
            label="Reset species settings",
            button_type="primary",
            width_policy="min",
            margin=(15, 5, 10, 5),
        )
        reset_button.js_on_click(
            CustomJS(
                code=f"""
const block_event = new CustomEvent('block-event', {{
    detail: {{
        block_id: '{self.block_id}',
        event_name: 'reset_species_settings',
    }}, bubbles: true
}});
document.dispatchEvent(block_event);
"""
            )
        )
        return reset_button

    def _create_species_input_widget(
        self, error_message: str | None = None
    ) -> bokeh.layouts.layout:
        """Create a standalone widget for inputting number of species when data can't be parsed yet

        Args:
            error_message: Optional error message from failed parsing attempt

        Returns:
            bokeh.layouts.layout: Bokeh layout with instructions and input widget
        """
        # Get current stored values
        current_num_species = self.data.get("num_species", "")
        current_num_text = str(current_num_species) if current_num_species else "Not set"
        current_species_names = self.data.get("species_names", None)
        current_names_text = (
            ", ".join(current_species_names) if current_species_names else "Not set"
        )

        # Create compact instruction message
        if error_message:
            instruction_text = (
                f'<p style="color:#d9534f;"><b>Error parsing .dat file:</b> {error_message}</p>'
                f"<p>Correct the number of species or enter species names below, then refresh.</p>"
            )
        else:
            instruction_text = (
                '<p style="color:#d9534f;"><b>Cannot parse .dat file.</b></p>'
                "<p>Enter the number of species (or species names) below, then refresh. "
                "Alternatively, upload a companion .csv file.</p>"
            )

        instruction_div = Div(
            text=instruction_text, visible=True, sizing_mode="stretch_width", margin=(5, 5, 0, 5)
        )

        species_input = TextInput(
            value="",
            title=f"Number of species, excl. vacuum (current: {current_num_text}):",
        )
        species_display = Div(text=current_num_text, visible=False)
        species_input.js_link("value", species_display, "text")
        species_display.js_on_change(
            "text",
            CustomJS(
                code=generate_js_callback_single_float_parameter(
                    "set_num_species", "num_species", self.block_id, throttled=False
                )
            ),
        )

        names_input = TextInput(
            value="",
            title=f"Species names, comma-separated, excl. vacuum (current: {current_names_text}):",
        )
        names_display = Div(text=current_names_text, visible=False)
        names_input.js_link("value", names_display, "text")
        names_display.js_on_change(
            "text",
            CustomJS(
                code=generate_js_callback_single_float_parameter(
                    "set_species_names", "species_names", self.block_id, throttled=False
                )
            ),
        )

        reset_button = self._make_reset_button()

        layout = column(
            children=[
                instruction_div,
                species_input,
                names_input,
                reset_button,
                species_display,
                names_display,
            ],
            sizing_mode="stretch_width",
        )

        return layout

    def _make_param_input(self, title: str, current_val: float, event_name: str, param_name: str):
        """Create a labelled TextInput wired to a block event via JS callback.

        Returns a tuple of (input_widget, hidden_display_div) to be added to the layout.
        """
        text_input = TextInput(value="", title=f"{title} (current: {current_val})")
        display = Div(text=str(current_val), visible=False)
        text_input.js_link("value", display, "text")
        display.js_on_change(
            "text",
            CustomJS(
                code=generate_js_callback_single_float_parameter(
                    event_name, param_name, self.block_id, throttled=False
                )
            ),
        )
        return text_input, display

    def _format_oms_plot(
        self,
        oms_data: pd.DataFrame,
        show_species_input: bool = False,
        nmol_df: pd.DataFrame | None = None,
        calibration_summary: dict | None = None,
        rate_t_start: float = 0.0,
        rate_t_end: float = 1800.0,
    ) -> bokeh.layouts.layout:
        """Formats OMS data for plotting in Bokeh with all species plotted and toggleable legends

        Args:
            oms_data: OMS dataframe with time and species columns
            show_species_input: Whether to show the species number input (for .dat files only)

        Returns:
            bokeh.layouts.layout: Bokeh layout with OMS data plotted
        """
        # Determine x-axis column and label based on what's available
        if "Time (s)" in oms_data.columns:
            x_column = "Time (s)"
            x_label = "Time (s)"
        elif "Data Point" in oms_data.columns:
            x_column = "Data Point"
            x_label = "Data Point"
        else:
            # Fallback - shouldn't happen
            x_column = oms_data.columns[0]
            x_label = x_column

        # Get all columns except Time, ms, Time (s), Data Point, and timepoint
        species_columns = [
            col
            for col in oms_data.columns
            if col not in ["Time", "ms", "Time (s)", "Data Point", "timepoint"]
        ]

        # Calculate mean of all species for the dummy hover glyph
        oms_data["_mean_concentration"] = oms_data[species_columns].mean(axis=1)

        # Create a ColumnDataSource (shared between both plots)
        source = ColumnDataSource(oms_data)

        # Plot all species with different colors
        colors = Category10_10

        # Helper function to create a plot with given y_axis_type
        def create_plot(y_axis_type):
            p = figure(
                sizing_mode="scale_width",
                height=250,
                x_axis_label=x_label,
                y_axis_label="Concentration",
                tools=TOOLS + ", pan, wheel_zoom",
                y_axis_type=y_axis_type,
            )

            p.toolbar.logo = "grey"
            p.xaxis.ticker.desired_num_ticks = 5
            p.yaxis.ticker.desired_num_ticks = 5
            p.js_on_event(DoubleTap, CustomJS(args=dict(p=p), code="p.reset.emit()"))

            # Create an invisible dummy glyph for hover that won't be hidden by legend
            # Use mean concentration to stay within the data range
            dummy_hover_glyph = p.line(
                x=x_column,
                y="_mean_concentration",  # Use mean to stay in concentration range
                source=source,
                alpha=0,  # Completely invisible
                level="overlay",  # Ensure it's on top for hover
            )

            legend_items = []

            for i, species in enumerate(species_columns):
                color = colors[i % len(colors)]

                # Plot line
                line = p.line(
                    x=x_column, y=species, source=source, color=color, line_width=2, name=species
                )

                # Plot points
                circle = p.circle(
                    x=x_column, y=species, source=source, color=color, size=4, name=species
                )

                # Add to legend items
                legend_items.append((species, [line, circle]))

            # Create external legend with click policy
            legend = Legend(
                items=legend_items,
                click_policy="hide",
                background_fill_alpha=0.8,
                label_text_font_size="9pt",
                spacing=1,
                margin=5,
            )
            p.add_layout(legend, "right")

            # Build tooltips dynamically for each species with scientific notation
            # Tooltip label adjusts based on whether we have real time or just data points
            tooltip_label = x_label if "Time" in x_label else "Data Point"
            tooltip_format = "{0,0.0} s" if "Time" in x_label else "{0,0}"
            tooltips = [(tooltip_label, f"@{{{x_column}}}{tooltip_format}")]
            formatters = {}

            for species in species_columns:
                tooltips.append((species, f"@{{{species}}}{{%0.2e}}"))
                formatters[f"@{{{species}}}"] = "printf"

            # Add hover tool attached to only the dummy glyph
            hover = HoverTool(
                tooltips=tooltips,
                formatters=formatters,
                renderers=[dummy_hover_glyph],
                mode="vline",
                line_policy="none",
            )
            p.add_tools(hover)

            return p

        # Create both linear and log plots in tabs
        p_linear = create_plot("linear")
        p_log = create_plot("log")
        scale_tabs = Tabs(
            tabs=[Panel(child=p_linear, title="Linear"), Panel(child=p_log, title="Log")],
            margin=(5, 0, 5, 0),
        )
        controls_layout = row(scale_tabs, sizing_mode="scale_width")

        # Add species number input for .dat files (if requested)
        if show_species_input:
            # Get current stored values
            current_num_species = self.data.get("num_species", "")
            current_num_text = str(current_num_species) if current_num_species else "Not set"
            current_species_names = self.data.get("species_names", None)
            current_names_text = (
                ", ".join(current_species_names) if current_species_names else "Not set"
            )

            species_input = TextInput(
                value="",
                title=f"Number of species, excl. vacuum (current: {current_num_text}):",
            )
            species_display = Div(text=current_num_text, visible=False)
            species_input.js_link("value", species_display, "text")
            species_display.js_on_change(
                "text",
                CustomJS(
                    code=generate_js_callback_single_float_parameter(
                        "set_num_species", "num_species", self.block_id, throttled=False
                    )
                ),
            )

            names_input = TextInput(
                value="",
                title=f"Species names, comma-separated, excl. vacuum (current: {current_names_text}):",
            )
            names_display = Div(text=current_names_text, visible=False)
            names_input.js_link("value", names_display, "text")
            names_display.js_on_change(
                "text",
                CustomJS(
                    code=generate_js_callback_single_float_parameter(
                        "set_species_names", "species_names", self.block_id, throttled=False
                    )
                ),
            )

            reset_button = self._make_reset_button()

            layout_children = [
                species_input,
                names_input,
                reset_button,
                species_display,
                names_display,
                controls_layout,
            ]
        else:
            layout_children = [controls_layout]

        # --- Calibration section (only when nmol data is available) ---
        if nmol_df is not None and calibration_summary:
            nmol_species_cols = [
                c
                for c in nmol_df.columns
                if c != "Time (s)" and not c.endswith("_baseline") and not c.endswith("_raw_nmol_s")
            ]
            raw_species_cols = [c for c in nmol_df.columns if c.endswith("_raw_nmol_s")]
            nmol_source = ColumnDataSource(nmol_df)

            def _make_figure(y_axis_type, y_label):
                p = figure(
                    sizing_mode="scale_width",
                    height=250,
                    x_axis_label="Time (s)",
                    y_axis_label=y_label,
                    tools=TOOLS + ", pan, wheel_zoom",
                    y_axis_type=y_axis_type,
                )
                p.toolbar.logo = "grey"
                p.xaxis.ticker.desired_num_ticks = 5
                p.yaxis.ticker.desired_num_ticks = 5
                p.js_on_event(DoubleTap, CustomJS(args=dict(p=p), code="p.reset.emit()"))
                return p

            def _add_legend_and_hover(p, legend_items, hover_cols, dummy_col):
                nmol_df[dummy_col] = nmol_df[hover_cols].mean(axis=1)
                dummy = p.line(
                    x="Time (s)", y=dummy_col, source=nmol_source, alpha=0, level="overlay"
                )
                legend = Legend(
                    items=legend_items,
                    click_policy="hide",
                    background_fill_alpha=0.8,
                    label_text_font_size="9pt",
                    spacing=1,
                    margin=5,
                )
                p.add_layout(legend, "right")
                tooltips = [("Time (s)", "@{Time (s)}{0,0.0} s")]
                formatters = {}
                for col in hover_cols:
                    label = col.replace("_raw_nmol_s", "").replace("_nmol_s", "")
                    tooltips.append((f"{label} (nmol/s)", f"@{{{col}}}{{%0.4g}}"))
                    formatters[f"@{{{col}}}"] = "printf"
                p.add_tools(
                    HoverTool(
                        tooltips=tooltips,
                        formatters=formatters,
                        renderers=[dummy],
                        mode="vline",
                        line_policy="none",
                    )
                )

            def create_raw_plot(y_axis_type):
                p = _make_figure(y_axis_type, "nmol/s (raw)")
                legend_items = []
                for i, col in enumerate(raw_species_cols):
                    color = colors[i % len(colors)]
                    label = col.replace("_raw_nmol_s", "")
                    line = p.line(
                        x="Time (s)", y=col, source=nmol_source, color=color, line_width=2
                    )
                    circle = p.circle(x="Time (s)", y=col, source=nmol_source, color=color, size=4)
                    legend_items.append((label, [line, circle]))
                    baseline_col = f"{label}_baseline"
                    if baseline_col in nmol_df.columns:
                        bl = p.line(
                            x="Time (s)",
                            y=baseline_col,
                            source=nmol_source,
                            color=color,
                            line_width=1,
                            line_dash="dashed",
                            alpha=0.6,
                        )
                        legend_items.append((f"{label} baseline", [bl]))
                _add_legend_and_hover(p, legend_items, raw_species_cols, "_raw_mean")
                return p

            def create_corrected_plot(y_axis_type):
                p = _make_figure(y_axis_type, "nmol/s (corrected)")
                p.add_layout(
                    BoxAnnotation(
                        left=rate_t_start,
                        right=rate_t_end,
                        fill_color="grey",
                        fill_alpha=0.15,
                        line_color="grey",
                        line_dash="dashed",
                        line_alpha=0.4,
                    )
                )
                legend_items = []
                for i, col in enumerate(nmol_species_cols):
                    color = colors[i % len(colors)]
                    label = col.replace("_nmol_s", "")
                    line = p.line(
                        x="Time (s)", y=col, source=nmol_source, color=color, line_width=2
                    )
                    circle = p.circle(x="Time (s)", y=col, source=nmol_source, color=color, size=4)
                    legend_items.append((label, [line, circle]))
                _add_legend_and_hover(p, legend_items, nmol_species_cols, "_corrected_mean")
                return p

            p_raw_linear = create_raw_plot("linear")
            p_raw_log = create_raw_plot("log")
            p_corr_linear = create_corrected_plot("linear")
            p_corr_log = create_corrected_plot("log")
            raw_tabs = Tabs(
                tabs=[
                    Panel(child=p_raw_linear, title="Linear"),
                    Panel(child=p_raw_log, title="Log"),
                ],
                margin=(5, 0, 5, 0),
            )
            corr_tabs = Tabs(
                tabs=[
                    Panel(child=p_corr_linear, title="Linear"),
                    Panel(child=p_corr_log, title="Log"),
                ],
                margin=(5, 0, 5, 0),
            )

            # Parameter inputs — built before the OMS plot so they appear above it
            flow_rate_val = self.data["flow_rate"]
            temperature_val = self.data["temperature"]
            pressure_val = self.data["pressure"]

            fr_input, fr_display = self._make_param_input(
                "Flow rate (mL/min)", flow_rate_val, "set_flow_rate", "flow_rate"
            )
            temp_input, temp_display = self._make_param_input(
                "Temperature (K)", temperature_val, "set_temperature", "temperature"
            )
            pres_input, pres_display = self._make_param_input(
                "Pressure (Pa)", pressure_val, "set_pressure", "pressure"
            )
            t_start_input, t_start_display = self._make_param_input(
                "Rate window start (s)", rate_t_start, "set_rate_t_start", "rate_t_start"
            )
            t_end_input, t_end_display = self._make_param_input(
                "Rate window end (s)", rate_t_end, "set_rate_t_end", "rate_t_end"
            )
            param_inputs_row = row(
                fr_input,
                temp_input,
                pres_input,
                t_start_input,
                t_end_input,
                fr_display,
                temp_display,
                pres_display,
                t_start_display,
                t_end_display,
                sizing_mode="stretch_width",
                margin=(10, 0, 50, 0),
            )
            # Insert param inputs before the OMS plot (controls_layout is last in layout_children)
            layout_children.insert(-1, param_inputs_row)

            # Summary table
            rows_html = ""
            for species, stats in calibration_summary.items():
                peak = f"{stats['peak_flux_nmol_s']:.4g}"
                total = f"{stats['total_nmol']:.4g}"
                initial_rate = stats.get("initial_rate_nmol_s")
                rate_str = (
                    f"{initial_rate:.4g}"
                    if initial_rate is not None and not (initial_rate != initial_rate)
                    else "N/A"
                )
                rows_html += (
                    f"<tr><td>{species}</td><td>{peak}</td><td>{total}</td><td>{rate_str}</td></tr>"
                )
            summary_div = Div(
                text=(
                    "<b>Calibration Summary</b>"
                    '<table style="border-collapse:collapse;margin-top:6px">'
                    "<tr><th style='padding:2px 12px 2px 0'>Species</th>"
                    "<th style='padding:2px 12px 2px 0'>Peak flux (nmol/s)</th>"
                    "<th style='padding:2px 12px 2px 0'>Total (nmol)</th>"
                    f"<th style='padding:2px 0'>Initial rate {rate_t_start:.4g}–{rate_t_end:.4g} s (nmol/s)</th></tr>"
                    f"{rows_html}"
                    "</table>"
                ),
                sizing_mode="stretch_width",
                margin=(10, 5, 5, 5),
            )

            layout_children += [
                raw_tabs,
                corr_tabs,
                summary_div,
            ]

        return column(children=layout_children, sizing_mode="stretch_width")

    def generate_oms_plot(self):
        """Generate OMS plot from uploaded file(s).

        Supports three OMS data formats:
        - .csv: Manual export with headers (standard format)
        - .dat: Binary live-updating format (46-byte records)
        - .exp: ASCII live-updating format (space-separated integers)

        Optionally accepts a .xlsm calibration file alongside the OMS data file.
        When a .xlsm is present with a .csv, nmol/s conversion and integration are shown.
        Calibration is not supported for .dat files (no time axis).
        """
        file_ids = []
        if self.data.get("file_ids"):
            file_ids = self.data["file_ids"]
        elif self.data.get("file_id"):
            file_ids = [self.data["file_id"]]

        if not file_ids:
            return

        file_infos = [get_file_info_by_id(id_, update_if_live=True) for id_ in file_ids]
        if not file_infos:
            raise RuntimeError("No file information found for the provided file IDs.")

        filenames = [Path(info["location"]) for info in file_infos]

        xlsm_files = [f for f in filenames if f.suffix.lower() == ".xlsm"]
        data_files = [f for f in filenames if f.suffix.lower() in (".csv", ".dat", ".exp")]

        # Error cases when a calibration file is present
        if xlsm_files and not data_files:
            layout = self._create_error_div(
                "Calibration file detected. Please also attach a .csv data file to enable plotting."
            )
            self.data["bokeh_plot_data"] = bokeh.embed.json_item(layout, theme=DATALAB_BOKEH_THEME)
            return

        if xlsm_files and data_files and all(f.suffix.lower() != ".csv" for f in data_files):
            layout = self._create_error_div(
                ".dat files do not have a time axis so calibration cannot be applied. "
                "Please also attach the corresponding .csv file."
            )
            self.data["bokeh_plot_data"] = bokeh.embed.json_item(layout, theme=DATALAB_BOKEH_THEME)
            return

        # Pick the data file: prefer .csv, then .dat, then .exp
        csv_files = [f for f in data_files if f.suffix.lower() == ".csv"]
        dat_files = [f for f in data_files if f.suffix.lower() == ".dat"]
        exp_files = [f for f in data_files if f.suffix.lower() == ".exp"]

        file_path = (csv_files or dat_files or exp_files)[0]
        ext = file_path.suffix.lower()

        # Get user-specified num_species and species_names if available
        # Falsy values (0, [], None) are treated as "not set"
        num_species = self.data.get("num_species", None) or None
        species_names = self.data.get("species_names", None) or None
        show_species_input = False
        parsing_error = None
        oms_data = None

        if species_names and num_species is None:
            num_species = len(species_names)

        if ext == ".csv":
            oms_data = parse_oms_csv(file_path)
        elif ext == ".dat":
            show_species_input = True
            if num_species is not None:
                try:
                    oms_data = parse_oms_dat(
                        file_path, num_species=num_species, species_names=species_names
                    )
                except ValueError as e:
                    parsing_error = str(e)
                    LOGGER.warning(f"Failed to parse .dat file with num_species={num_species}: {e}")
                    oms_data = None
            else:
                csv_path = self._find_companion_csv(file_path)
                try:
                    oms_data = parse_oms_dat(file_path, csv_filepath=csv_path)
                except ValueError as e:
                    parsing_error = str(e)
                    LOGGER.warning(f"Failed to parse .dat file: {e}")
                    oms_data = None
        elif ext == ".exp":
            base_path = file_path.with_suffix("")
            dat_path = base_path.with_suffix(".dat")
            csv_path = base_path.with_suffix(".csv")
            if dat_path.exists():
                oms_data = parse_oms_dat(dat_path)
            elif csv_path.exists():
                oms_data = parse_oms_csv(csv_path)
            else:
                raise ValueError(
                    f".exp file '{file_path.name}' found, but cannot be plotted directly. "
                    "Please upload the corresponding .dat or .csv file instead."
                )

        # Apply calibration if we have a .xlsm and a successfully parsed .csv
        nmol_df = None
        calibration_summary = None
        if oms_data is not None and xlsm_files and ext == ".csv":
            try:
                flow_rate = float(self.data["flow_rate"])
                temperature = float(self.data["temperature"])
                pressure = float(self.data["pressure"])
                rate_t_start = float(self.data["rate_t_start"])
                rate_t_end = float(self.data["rate_t_end"])
                calibration = parse_calibration_xlsm(xlsm_files[0])
                nmol_df, calibration_summary = apply_calibration(
                    oms_data,
                    calibration,
                    flow_rate,
                    temperature,
                    pressure,
                    rate_t_start,
                    rate_t_end,
                )
                if calibration_summary:
                    LOGGER.debug(f"Calibration summary: {calibration_summary}")
                    if not self.data.get("metadata"):
                        self.data["metadata"] = {}
                    self.data["metadata"]["calibration_results"] = calibration_summary
            except Exception as e:
                LOGGER.warning(f"Calibration failed: {e}")

        if oms_data is not None:
            layout = self._format_oms_plot(
                oms_data,
                show_species_input=show_species_input,
                nmol_df=nmol_df,
                calibration_summary=calibration_summary,
                rate_t_start=self.data["rate_t_start"],
                rate_t_end=self.data["rate_t_end"],
            )
            self.data["bokeh_plot_data"] = bokeh.embed.json_item(layout, theme=DATALAB_BOKEH_THEME)
        elif show_species_input:
            layout = self._create_species_input_widget(error_message=parsing_error)
            self.data["bokeh_plot_data"] = bokeh.embed.json_item(layout, theme=DATALAB_BOKEH_THEME)
