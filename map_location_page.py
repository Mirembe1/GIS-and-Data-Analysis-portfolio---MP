import re
import sqlite3
from uuid import uuid4

import pandas as pd


def _safe_identifier(name):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise ValueError(f"Invalid SQL identifier: {name}")
    return name


def _resolve_page_decorator():
    app_obj = globals().get("App")
    return app_obj.page if app_obj and hasattr(app_obj, "page") else (lambda func: func)


def data_to_sqlite(db_fpath, table_name, data):
    table_name = _safe_identifier(table_name)
    df = pd.DataFrame(data)
    df["created_at"] = pd.Timestamp.now()
    conn = sqlite3.connect(db_fpath)
    try:
        df.to_sql(table_name, conn, if_exists="append", index=False)
    finally:
        conn.close()


def data_from_sqlite(db_fpath, table_name):
    table_name = _safe_identifier(table_name)
    if not db_fpath.exists():
        return []
    conn = sqlite3.connect(db_fpath)
    try:
        # Safe because table_name is strictly validated by _safe_identifier.
        query = f'SELECT * FROM "{table_name}"'
        return pd.read_sql_query(query, conn).to_dict("records")
    except Exception:
        return []
    finally:
        conn.close()


def reactive_table(lib, row_data=None):
    row_data = row_data or []
    has_rows = isinstance(row_data, (list, tuple)) and len(row_data) > 0 and isinstance(row_data[0], dict)
    first_row = row_data[0] if has_rows else {}

    def _col_filter(value):
        return "agNumberColumnFilter" if isinstance(value, (int, float)) else "agTextColumnFilter"

    col_defs = [
        {"field": key, "filter": _col_filter(val), "sortable": True, "resizable": True}
        for key, val in first_row.items()
    ]
    default_col_def = lib.Props(flex=1, resizable=True, sortable=True)
    return lib.html.div(style=lib.Style(height="500px"))(
        lib.ag.AgGridReact(
            rowData=row_data,
            columnDefs=col_defs,
            defaultColDef=default_col_def,
            pagination=True,
            paginationPageSize=20,
        )
    )


@_resolve_page_decorator()
def map_location(lib):
    displayed_data, set_displayed_data = lib.hooks.use_state([])
    submit_success, set_submit_success = lib.hooks.use_state(None)
    form_key, set_form_key = lib.hooks.use_state(str(uuid4()))

    resources = lib.hooks.use_resources()
    db_fpath = resources.path / "my_database.sqlite"
    table_name = "Map_Location"

    form_fields = [
        [("village", "Village"), ("ves_no", "VES No."), ("map_sheet_no", "Map Sheet No."), ("mapped_by", "Mapped By")],
        [("parish", "Parish"), ("subcounty", "Sub-County"), ("county", "County"), ("district", "District")],
        [("grid_east", "Grid East"), ("grid_north", "Grid North"), ("altitude", "Altitude")],
        [("village_code", "Village Code"), ("date_of_survey", "Date of Survey"), ("source_name_2", "Source Name")],
        [("proposed_type_of_water_source", "Proposed Type of Water Source")],
        [("expected_depth_to_rock_m", "Expected Depth to Rock (m)"), ("expected_depth_to_water_m", "Expected Depth to Water (m)")],
        [("expected_formation", "Expected Formation")],
        [("expected_borehole_depth_m", "Expected Borehole Depth (m)"), ("accessibility_to_site", "Accessibility to Site")],
        [("expected_depth_to_screen_m", "Expected Depth to Screen (m)")],
    ]

    form_rows = []
    for row in form_fields:
        row_elements = []
        for field_name, label_text in row:
            row_elements.append(
                lib.bs.Col()(
                    lib.html.label(
                        style=lib.Style(display="block", fontWeight="bold", marginBottom="5px", fontSize="14px"),
                        for_=field_name,
                    )(f"{label_text}:"),
                    lib.html.input(
                        name=field_name,
                        type="text",
                        className="form-control",
                        style=lib.Style(width="100%", padding="8px", marginBottom="10px"),
                    ),
                )
            )
        form_rows.append(lib.bs.Row()(*row_elements))

    def handle_submit(e):
        form_data = e["formData"]
        data_to_sqlite(db_fpath, table_name, [form_data])
        set_submit_success(True)
        set_form_key(str(uuid4()))
        lib.utils.background_execute(lambda: set_submit_success(None), delay_seconds=3)

    lib.register("sketch_canvas.js", "sc", host="/static/component_playground/js", default_export="SketchCanvas")
    lib.register("react-tabs", "tabs", styles=["https://esm.sh/react-tabs@6.1.0/style/react-tabs.css"])

    color, set_color = lib.hooks.use_state("#100a0a")
    width, set_width = lib.hooks.use_state(4)
    event_fn = globals().get("event")
    submit_handler = (
        event_fn(handle_submit, prevent_default=True, stop_propagation=True)
        if callable(event_fn)
        else handle_submit
    )

    return lib.tabs.Tabs(
        lib.tabs.TabList(lib.tabs.Tab("Location Map"), lib.tabs.Tab("Add Data"), lib.tabs.Tab("View Data")),
        lib.tabs.TabPanel(
            lib.html.div(style=lib.Style(padding="20px"))(
                lib.html.h1("LOCATION MAP"),
                lib.bs.Row(
                    lib.bs.Col(
                        lib.html.label("Draw Color:"),
                        lib.html.input(
                            type="color",
                            value=color,
                            onChange=lambda e: set_color(e.target.value),
                            style=lib.Style(marginRight="10px"),
                        ),
                        lib.html.label("Brush Width:"),
                        lib.html.input(
                            type="range",
                            min="1",
                            max="10",
                            value=width,
                            onChange=lambda e: set_width(int(e.target.value)),
                        ),
                    ),
                ),
                lib.bs.Row(
                    lib.bs.Col(
                        lib.sc.SketchCanvas(
                            style=lib.Style(border="0.0625rem solid #9c9c9c", borderRadius="0.25rem", width="100%", height="500px"),
                            width="100%",
                            height="500px",
                            strokeWidth=width,
                            strokeColor=color,
                        )
                    ),
                ),
            )
        ),
        lib.tabs.TabPanel(
            lib.html.div(style=lib.Style(padding="20px"))(
                lib.html.h2("Map Location Survey Form"),
                lib.bs.Form(key=form_key, onSubmit=submit_handler)(
                    lib.bs.Container()(*form_rows, lib.bs.Button(type="submit", variant="primary")("Submit")),
                    lib.bs.Alert(variant="success")("Form submitted successfully!") if submit_success else None,
                ),
            )
        ),
        lib.tabs.TabPanel(
            lib.html.div(style=lib.Style(padding="20px"))(
                lib.bs.Button(
                    disabled=not db_fpath.exists(),
                    onClick=lambda _: set_displayed_data(data_from_sqlite(db_fpath, table_name)),
                )(f"{'Load' if not displayed_data else 'Reload'} Data from SQLite Database"),
                reactive_table(lib, displayed_data)
                if displayed_data
                else lib.html.div("No data loaded yet."),
            )
        ),
    )
