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

from datalab_app_plugin_oms.utils import parse_oms_csv, parse_oms_dat


class OMSBlock(DataBlock):
    blocktype = "oms"
    name = "OMS"
    description = "Block for plotting OMS time series data."
    accepted_file_extensions: tuple[str, ...] = (".csv", ".dat", ".exp")

    @property
    def plot_functions(self):
        return (self.generate_oms_plot,)

    @event()
    def set_num_species(self, num_species):
        """
        Updates self.data with the user-inputted number of species for .dat file parsing

        Args:
            num_species: integer or 'clear' to remove the stored value
        """
        if num_species == "clear":
            LOGGER.debug("Clearing num_species")
            self.data.pop("num_species", None)
        else:
            try:
                num_species_int = int(num_species)
                if num_species_int < 1:
                    raise ValueError("Number of species must be at least 1")
                LOGGER.debug(f"Setting num_species to {num_species_int}")
                self.data["num_species"] = num_species_int
            except ValueError as e:
                raise ValueError(f"Invalid num_species. Must be a positive integer or 'clear': {e}")

    def _create_species_input_widget(
        self, error_message: str | None = None
    ) -> bokeh.layouts.layout:
        """Create a standalone widget for inputting number of species when data can't be parsed yet

        Args:
            error_message: Optional error message from failed parsing attempt

        Returns:
            bokeh.layouts.layout: Bokeh layout with instructions and input widget
        """
        # Get current stored value
        current_num_species = self.data.get("num_species", "")
        current_text = str(current_num_species) if current_num_species else "Not set"

        # Create instruction message with error if present
        if error_message:
            instruction_text = (
                f'<p style="font-size:16px; color:#d9534f;"><b>Error parsing .dat file</b></p>'
                f'<p style="font-size:14px; color:#d9534f;"><b>Error:</b> {error_message}</p>'
                f'<p style="font-size:14px;">Please correct the number of species below and refresh the page.</p>'
            )
        else:
            instruction_text = (
                '<p style="font-size:16px; color:#d9534f;"><b>Cannot parse .dat file</b></p>'
                '<p style="font-size:14px;">To parse this .dat file, you must either:</p>'
                '<ul style="font-size:14px;">'
                "<li>Enter the number of species below, OR</li>"
                "<li>Upload a companion .csv file with the same base name</li>"
                "</ul>"
                '<p style="font-size:14px;">After entering the number of species and pressing Enter, '
                "refresh the page to generate the plot.</p>"
            )

        instruction_div = Div(text=instruction_text, visible=True, sizing_mode="stretch_width")

        # Create input widget
        species_input_title = Div(
            text='<p style="font-size:14px;"><b>Number of Species for .dat file parsing</b></p>'
            "Enter the number of chemical species being measured (excluding vacuum measurement).<br>"
            'Enter "clear" to remove stored value.',
            visible=True,
        )

        species_input = TextInput(
            value="", title="Number of species (excluding vacuum):", visible=True
        )

        species_display_title = Div(
            text='<p style="font-size:14px;"><b>Currently stored value</b></p>', visible=True
        )

        species_display = Div(text=current_text, style={"font-size": "16px"}, visible=True)

        # Link input to display and trigger callback
        species_input.js_link("value", species_display, "text")
        species_display.js_on_change(
            "text",
            CustomJS(
                code=generate_js_callback_single_float_parameter(
                    "set_num_species", "num_species", self.block_id, throttled=False
                )
            ),
        )

        species_current = column(children=[species_display_title, species_display])
        species_layout = row(children=[species_input, species_current])

        layout = column(
            instruction_div, species_input_title, species_layout, sizing_mode="stretch_width"
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
            # Get current stored value
            current_num_species = self.data.get("num_species", "")
            current_text = (
                str(current_num_species) if current_num_species else "Not set (will use CSV file)"
            )

            # Create input widget
            species_input_title = Div(
                text='<p style="font-size:14px;"><b>Number of Species for .dat file parsing</b></p>'
                "Enter the number of chemical species being measured (excluding vacuum measurement).<br>"
                "If not set, will attempt to use companion CSV file to auto-detect.<br>"
                'Enter "clear" to remove stored value.',
                visible=True,
            )

            species_input = TextInput(
                value="", title="Number of species (excluding vacuum):", visible=True
            )

            species_display_title = Div(
                text='<p style="font-size:14px;"><b>Currently stored value</b></p>', visible=True
            )

            species_display = Div(text=current_text, style={"font-size": "16px"}, visible=True)

            # Link input to display and trigger callback
            species_input.js_link("value", species_display, "text")
            species_display.js_on_change(
                "text",
                CustomJS(
                    code=generate_js_callback_single_float_parameter(
                        "set_num_species", "num_species", self.block_id, throttled=False
                    )
                ),
            )

            species_current = column(children=[species_display_title, species_display])
            species_layout = row(children=[species_input, species_current])

            layout = column(
                controls_layout,
                p_linear,
                p_log,
                species_input_title,
                species_layout,
                sizing_mode="scale_width",
            )
        else:
            layout = column(controls_layout, p_linear, p_log, sizing_mode="scale_width")

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

        # Get user-specified num_species if available
        num_species = self.data.get("num_species", None)
        show_species_input = False

        # Track parsing errors to show in UI
        parsing_error = None

        if ext == ".csv":
            oms_data = parse_oms_csv(file_path)
        elif ext == ".dat":
            show_species_input = True  # Show input widget for .dat files

            # Try to parse with num_species if provided, otherwise fallback to CSV
            if num_species is not None:
                try:
                    oms_data = parse_oms_dat(file_path, num_species=num_species)
                except ValueError as e:
                    # Parsing failed - likely wrong num_species
                    parsing_error = str(e)
                    LOGGER.warning(f"Failed to parse .dat file with num_species={num_species}: {e}")
                    oms_data = None
            else:
                # Try to find companion CSV
                csv_path = file_path.with_suffix(".csv")
                if csv_path.exists():
                    try:
                        oms_data = parse_oms_dat(file_path, csv_filepath=csv_path)
                    except ValueError as e:
                        # Parsing failed with CSV
                        parsing_error = str(e)
                        LOGGER.warning(f"Failed to parse .dat file with CSV: {e}")
                        oms_data = None
                else:
                    # Neither num_species nor CSV available - show message instead of error
                    LOGGER.warning(
                        "Cannot parse .dat file without num_species or companion CSV. "
                        "Please enter the number of species in the input field."
                    )
                    oms_data = None  # Will trigger the message display below
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
