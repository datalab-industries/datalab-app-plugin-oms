import os
from pathlib import Path

import bokeh.embed
import pandas as pd
from bokeh.layouts import column, row
from bokeh.models import Button, CustomJS, Div, HoverTool, Legend, TextInput
from bokeh.palettes import Category10_10
from bokeh.plotting import ColumnDataSource, figure
from pydatalab.blocks.base import DataBlock, event, generate_js_callback_single_float_parameter
from pydatalab.bokeh_plots import DATALAB_BOKEH_THEME, TOOLS
from pydatalab.file_utils import get_file_info_by_id
from pydatalab.logger import LOGGER
from pydatalab.mongo import flask_mongo

from datalab_app_plugin_oms.utils import parse_oms_csv, parse_oms_dat


class OMSBlock(DataBlock):
    blocktype = "oms"
    name = "OMS"
    description = "Block for plotting OMS time series data."
    accepted_file_extensions: tuple[str, ...] = (".csv", ".dat", ".exp")

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

    def _make_reset_button(self) -> Button:
        """Create a reset button that clears num_species and species_names."""
        reset_button = Button(
            label="Reset species settings",
            button_type="warning",
            width_policy="min",
            margin=(20, 5, 10, 5),
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

    def _format_oms_plot(
        self, oms_data: pd.DataFrame, show_species_input: bool = False
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
                aspect_ratio=1.5,
                x_axis_label=x_label,
                y_axis_label="Concentration",
                tools=TOOLS,
                y_axis_type=y_axis_type,
            )

            p.toolbar.logo = "grey"
            p.xaxis.ticker.desired_num_ticks = 5
            p.yaxis.ticker.desired_num_ticks = 5

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

        # Create both linear and log plots
        p_linear = create_plot("linear")
        p_log = create_plot("log")

        # Set initial visibility
        p_linear.visible = True
        p_log.visible = False

        # Add log/linear scale toggle button
        scale_button = Button(
            label="Log scale", button_type="default", width_policy="min", margin=(2, 5, 2, 5)
        )

        # Callback to switch which plot is visible (bokeh can't dynamically change scale as far as I'm aware)
        scale_callback = CustomJS(
            args=dict(btn=scale_button, p_linear=p_linear, p_log=p_log),
            code="""
                if (btn.label === 'Log scale') {
                    p_linear.visible = false;
                    p_log.visible = true;
                    btn.label = 'Linear scale';
                    btn.button_type = 'default';
                } else {
                    p_linear.visible = true;
                    p_log.visible = false;
                    btn.label = 'Log scale';
                    btn.button_type = 'default';
                }
            """,
        )

        scale_button.js_on_click(scale_callback)

        # Create controls layout
        controls_layout = row(scale_button, sizing_mode="scale_width", margin=(10, 0, 10, 0))

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

            layout = column(
                children=[
                    species_input,
                    names_input,
                    reset_button,
                    species_display,
                    names_display,
                    controls_layout,
                    p_linear,
                    p_log,
                ],
                sizing_mode="stretch_width",
            )
        else:
            layout = column(controls_layout, p_linear, p_log, sizing_mode="stretch_width")

        return layout

    def generate_oms_plot(self):
        """Generate OMS plot from uploaded file

        Supports three file formats:
        - .csv: Manual export with headers (standard format)
        - .dat: Binary live-updating format (46-byte records)
        - .exp: ASCII live-updating format (space-separated integers)

        The .dat and .exp formats may contain more timepoints than CSV if they
        were still updating when the CSV was exported.
        """
        file_info = None
        oms_data = None

        if "file_id" not in self.data:
            return

        file_info = get_file_info_by_id(self.data["file_id"], update_if_live=True)
        ext = os.path.splitext(file_info["location"].split("/")[-1])[-1].lower()

        if ext not in self.accepted_file_extensions:
            raise ValueError(
                f"Extension not in recognised extensions: {self.accepted_file_extensions}"
            )

        file_path = Path(file_info["location"])

        # Get user-specified num_species and species_names if available
        # Falsy values (0, [], None) are treated as "not set"
        num_species = self.data.get("num_species", None) or None
        species_names = self.data.get("species_names", None) or None
        show_species_input = False

        # If species_names is set but num_species is not, derive it
        if species_names and num_species is None:
            num_species = len(species_names)

        # Track parsing errors to show in UI
        parsing_error = None

        if ext == ".csv":
            oms_data = parse_oms_csv(file_path)
        elif ext == ".dat":
            show_species_input = True  # Show input widget for .dat files

            # Try to parse with num_species if provided, otherwise fallback to CSV
            if num_species is not None:
                try:
                    oms_data = parse_oms_dat(
                        file_path, num_species=num_species, species_names=species_names
                    )
                except ValueError as e:
                    # Parsing failed - likely wrong num_species
                    parsing_error = str(e)
                    LOGGER.warning(f"Failed to parse .dat file with num_species={num_species}: {e}")
                    oms_data = None
            else:
                # Try to find companion CSV in the database, then auto-detect as fallback
                csv_path = self._find_companion_csv(file_path)
                try:
                    # parse_oms_dat will:
                    # 1. Use CSV if found (csv_path is not None)
                    # 2. Auto-detect if CSV not found (csv_path is None)
                    oms_data = parse_oms_dat(file_path, csv_filepath=csv_path)
                except ValueError as e:
                    # Parsing failed (CSV error, auto-detection failed, etc.)
                    parsing_error = str(e)
                    LOGGER.warning(f"Failed to parse .dat file: {e}")
                    oms_data = None
        elif ext == ".exp":
            # .exp files don't contain the actual concentration data,
            # only quality/status codes, so we can't plot them directly.
            # Try to find a corresponding .dat or .csv file instead.
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
                    f"Please upload the corresponding .dat or .csv file instead."
                )

        if oms_data is not None:
            layout = self._format_oms_plot(oms_data, show_species_input=show_species_input)
            self.data["bokeh_plot_data"] = bokeh.embed.json_item(layout, theme=DATALAB_BOKEH_THEME)
        elif show_species_input:
            # Show input widget even without data (for .dat files that need num_species)
            layout = self._create_species_input_widget(error_message=parsing_error)
            self.data["bokeh_plot_data"] = bokeh.embed.json_item(layout, theme=DATALAB_BOKEH_THEME)
