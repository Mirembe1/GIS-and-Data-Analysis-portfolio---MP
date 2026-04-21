import re
import pandas as pd
import sqlite3
from uuid import uuid4
from tethys_sdk.components.utils import event


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_identifier(name):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise ValueError(f"Invalid SQL identifier: {name}")
    return name


def _resolve_page_decorator():
    app_obj = globals().get("App")
    return app_obj.page if app_obj and hasattr(app_obj, "page") else (lambda func: func)


def data_to_sqlite(db_fpath, table_name, data):
    """Insert rows into SQLite, creating or migrating the table as needed."""
    table_name = _safe_identifier(table_name)
    if not data:
        return

    conn = sqlite3.connect(str(db_fpath))
    cursor = conn.cursor()
    try:
        first_row = data[0]
        columns = [_safe_identifier(key) for key in first_row.keys() if key != "id"]

        cursor.execute(
            f"SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        )
        if not cursor.fetchone():
            col_defs = ", ".join([f'"{c}" TEXT' for c in columns])
            cursor.execute(
                f'CREATE TABLE "{table_name}" ({col_defs}, id INTEGER PRIMARY KEY AUTOINCREMENT)'
            )
        else:
            cursor.execute(f'PRAGMA table_info("{table_name}")')
            existing = {row[1] for row in cursor.fetchall()}
            for c in columns:
                if c not in existing:
                    cursor.execute(f'ALTER TABLE "{table_name}" ADD COLUMN "{c}" TEXT')

        for row in data:
            vals = [str(row.get(c, "") or "") for c in columns]
            placeholders = ", ".join(["?" for _ in vals])
            col_names = ", ".join([f'"{c}"' for c in columns])
            cursor.execute(
                f'INSERT INTO "{table_name}" ({col_names}) VALUES ({placeholders})',
                vals,
            )

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def data_from_sqlite(db_fpath, table_name):
    """Return all rows from the table as a list of dicts (id included)."""
    table_name = _safe_identifier(table_name)
    if not db_fpath.exists():
        return []
    conn = sqlite3.connect(str(db_fpath))
    try:
        return pd.read_sql_query(
            f'SELECT * FROM "{table_name}"', conn
        ).to_dict("records")
    except Exception:
        return []
    finally:
        conn.close()


def update_row_in_sqlite(db_fpath, table_name, row_id, updated_fields):
    """Update a single row identified by id."""
    table_name = _safe_identifier(table_name)
    if not updated_fields:
        return
    conn = sqlite3.connect(str(db_fpath))
    try:
        set_clause = ", ".join(
            [f'"{_safe_identifier(k)}" = ?' for k in updated_fields.keys()]
        )
        vals = list(updated_fields.values()) + [row_id]
        conn.execute(
            f'UPDATE "{table_name}" SET {set_clause} WHERE id = ?', vals
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def delete_row_from_sqlite(db_fpath, table_name, row_id):
    """Delete a single row identified by id."""
    table_name = _safe_identifier(table_name)
    conn = sqlite3.connect(str(db_fpath))
    try:
        conn.execute(f'DELETE FROM "{table_name}" WHERE id = ?', (row_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Page component
# ---------------------------------------------------------------------------

@_resolve_page_decorator()
def map_location(lib):
    # ---- state ----
    displayed_data, set_displayed_data = lib.hooks.use_state([])
    selected_row, set_selected_row = lib.hooks.use_state(None)

    # modal visibility
    show_edit_modal, set_show_edit_modal = lib.hooks.use_state(False)
    show_delete_modal, set_show_delete_modal = lib.hooks.use_state(False)

    # edit form state (dict of field -> value)
    edit_fields, set_edit_fields = lib.hooks.use_state({})

    # feedback
    error_message, set_error_message = lib.hooks.use_state(None)
    success_message, set_success_message = lib.hooks.use_state(None)

    # form submission
    form_key, set_form_key = lib.hooks.use_state(str(uuid4()))
    is_loading, set_is_loading = lib.hooks.use_state(False)
    submit_success, set_submit_success = lib.hooks.use_state(None)

    # sketch canvas
    color, set_color = lib.hooks.use_state("#100a0a")
    width, set_width = lib.hooks.use_state(4)

    # ---- resources ----
    resources = lib.hooks.use_resources()
    db_fpath = resources.path / "my_database.sqlite"
    table_name = "Map_Location"

    # ---- auto-load on mount ----
    def auto_load():
        try:
            rows = data_from_sqlite(db_fpath, table_name)
            set_displayed_data(rows)
        except Exception as err:
            print(f"Auto-load error: {err}")

    lib.hooks.use_effect(auto_load, [])

    # ---- libraries ----
    lib.register(
        "sketch_canvas.js",
        "sc",
        host="/static/component_playground/js",
        default_export="SketchCanvas",
    )
    lib.register(
        "react-tabs",
        "tabs",
        styles=["https://esm.sh/react-tabs@6.1.0/style/react-tabs.css"],
    )

    event_fn = globals().get("event")
    submit_handler = (
        event_fn(lambda e: handle_submit(e), prevent_default=True, stop_propagation=True)
        if callable(event_fn)
        else lambda e: handle_submit(e)
    )

    # ---- form fields definition ----
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

    # ---- handlers ----
    def handle_submit(e):
        if is_loading:
            return
        set_is_loading(True)
        set_error_message(None)
        set_submit_success(None)
        try:
            form_data = e["formData"]
            data_to_sqlite(db_fpath, table_name, [form_data])
            set_submit_success(True)
            set_form_key(str(uuid4()))
            rows = data_from_sqlite(db_fpath, table_name)
            set_displayed_data(rows)
            lib.utils.background_execute(lambda: set_submit_success(None), delay_seconds=4)
        except Exception as err:
            set_error_message(f"❌ Error submitting: {str(err)[:120]}")
            set_submit_success(False)
        finally:
            set_is_loading(False)

    def handle_refresh():
        try:
            rows = data_from_sqlite(db_fpath, table_name)
            set_displayed_data(rows)
            set_error_message(None)
        except Exception as err:
            set_error_message(f"Load error: {str(err)[:120]}")

    def handle_row_selected(e):
        """Called when AgGrid row selection changes."""
        try:
            sel = e.api.getSelectedRows()
            if sel and len(sel) > 0:
                row = sel[0]
                set_selected_row(dict(row) if not isinstance(row, dict) else row)
            else:
                set_selected_row(None)
        except Exception as err:
            print(f"Row select error: {err}")

    def open_edit_modal(_):
        if not selected_row:
            set_error_message("Please select a row first.")
            return
        # Populate edit fields from selected row (exclude id)
        fields = {k: v for k, v in selected_row.items() if k != "id"}
        set_edit_fields(fields)
        set_error_message(None)
        set_success_message(None)
        set_show_edit_modal(True)

    def open_delete_modal(_):
        if not selected_row:
            set_error_message("Please select a row first.")
            return
        set_error_message(None)
        set_success_message(None)
        set_show_delete_modal(True)

    def close_edit_modal(_):
        set_show_edit_modal(False)

    def close_delete_modal(_):
        set_show_delete_modal(False)

    def handle_edit_field_change(field_name, value):
        new_fields = dict(edit_fields)
        new_fields[field_name] = value
        set_edit_fields(new_fields)

    def handle_save_edit(_):
        if not selected_row:
            return
        try:
            row_id = selected_row.get("id")
            update_row_in_sqlite(db_fpath, table_name, row_id, edit_fields)
            rows = data_from_sqlite(db_fpath, table_name)
            set_displayed_data(rows)
            set_selected_row(None)
            set_show_edit_modal(False)
            set_success_message("✓ Row updated successfully.")
            lib.utils.background_execute(lambda: set_success_message(None), delay_seconds=4)
        except Exception as err:
            set_error_message(f"❌ Edit error: {str(err)[:120]}")

    def handle_confirm_delete(_):
        if not selected_row:
            return
        try:
            row_id = selected_row.get("id")
            delete_row_from_sqlite(db_fpath, table_name, row_id)
            rows = data_from_sqlite(db_fpath, table_name)
            set_displayed_data(rows)
            set_selected_row(None)
            set_show_delete_modal(False)
            set_success_message("✓ Row deleted successfully.")
            lib.utils.background_execute(lambda: set_success_message(None), delay_seconds=4)
        except Exception as err:
            set_error_message(f"❌ Delete error: {str(err)[:120]}")

    # ---- build column definitions ----
    def _build_col_defs(rows):
        if not rows:
            return []
        col_defs = [
            {
                "field": "",
                "headerName": "",
                "checkboxSelection": True,
                "headerCheckboxSelection": False,
                "width": 50,
                "pinned": "left",
                "lockPosition": True,
                "suppressMovable": True,
                "editable": False,
                "resizable": False,
                "sortable": False,
                "filter": False,
            }
        ]
        for key in rows[0].keys():
            col_defs.append({
                "field": key,
                "filter": "agTextColumnFilter",
                "sortable": True,
                "resizable": True,
                "minWidth": 120,
                "editable": True,
                "wrapText": True,
                "autoHeight": True,
            })
        return col_defs

    # ---- form rows builder ----
    def _build_form_rows():
        rows = []
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
            rows.append(lib.bs.Row()(*row_elements))
        return rows

    # ---- edit modal content ----
    def _build_edit_modal():
        if not show_edit_modal:
            return None

        field_inputs = []
        for field_name, current_value in edit_fields.items():
            field_inputs.append(
                lib.html.div(style=lib.Style(marginBottom="12px"))(
                    lib.html.label(
                        style=lib.Style(display="block", fontWeight="600", marginBottom="4px", fontSize="13px")
                    )(field_name.replace("_", " ").title() + ":"),
                    lib.html.input(
                        type="text",
                        className="form-control",
                        value=str(current_value) if current_value is not None else "",
                        onChange=lambda e, fn=field_name: handle_edit_field_change(fn, e.target.value),
                        style=lib.Style(width="100%", padding="8px", fontSize="14px"),
                    ),
                )
            )

        return lib.html.div(
            style=lib.Style(
                position="fixed",
                top="0",
                left="0",
                width="100%",
                height="100%",
                backgroundColor="rgba(0,0,0,0.5)",
                zIndex="1050",
                display="flex",
                alignItems="center",
                justifyContent="center",
            )
        )(
            lib.html.div(
                style=lib.Style(
                    backgroundColor="white",
                    borderRadius="8px",
                    padding="30px",
                    maxWidth="650px",
                    width="90%",
                    maxHeight="80vh",
                    overflowY="auto",
                    boxShadow="0 10px 30px rgba(0,0,0,0.3)",
                )
            )(
                lib.html.div(
                    style=lib.Style(
                        display="flex",
                        justifyContent="space-between",
                        alignItems="center",
                        marginBottom="20px",
                        borderBottom="2px solid #e9ecef",
                        paddingBottom="15px",
                    )
                )(
                    lib.html.h4(style=lib.Style(margin="0", color="#333"))("✏️ Edit Record"),
                    lib.html.button(
                        onClick=close_edit_modal,
                        style=lib.Style(
                            background="none",
                            border="none",
                            fontSize="24px",
                            cursor="pointer",
                            color="#666",
                            padding="0",
                            lineHeight="1",
                        ),
                    )("×"),
                ),
                *field_inputs,
                lib.html.div(
                    style=lib.Style(
                        display="flex",
                        gap="10px",
                        justifyContent="flex-end",
                        marginTop="20px",
                        borderTop="1px solid #e9ecef",
                        paddingTop="15px",
                    )
                )(
                    lib.bs.Button(
                        variant="secondary",
                        onClick=close_edit_modal,
                        style=lib.Style(minWidth="100px"),
                    )("Cancel"),
                    lib.bs.Button(
                        variant="primary",
                        onClick=handle_save_edit,
                        style=lib.Style(minWidth="100px"),
                    )("💾 Save"),
                ),
            )
        )

    # ---- delete modal content ----
    def _build_delete_modal():
        if not show_delete_modal or not selected_row:
            return None

        row_id = selected_row.get("id", "?")
        village = selected_row.get("village", "")
        ves_no = selected_row.get("ves_no", "")

        return lib.html.div(
            style=lib.Style(
                position="fixed",
                top="0",
                left="0",
                width="100%",
                height="100%",
                backgroundColor="rgba(0,0,0,0.5)",
                zIndex="1050",
                display="flex",
                alignItems="center",
                justifyContent="center",
            )
        )(
            lib.html.div(
                style=lib.Style(
                    backgroundColor="white",
                    borderRadius="8px",
                    padding="30px",
                    maxWidth="480px",
                    width="90%",
                    boxShadow="0 10px 30px rgba(0,0,0,0.3)",
                    textAlign="center",
                )
            )(
                lib.html.div(style=lib.Style(fontSize="48px", marginBottom="15px"))("🗑️"),
                lib.html.h4(style=lib.Style(color="#333", marginBottom="10px"))(
                    "Confirm Delete"
                ),
                lib.html.p(style=lib.Style(color="#555", marginBottom="5px"))(
                    f"Are you sure you want to delete this record?"
                ),
                lib.html.p(
                    style=lib.Style(
                        color="#666",
                        fontSize="13px",
                        backgroundColor="#f8f9fa",
                        padding="8px 12px",
                        borderRadius="4px",
                        marginBottom="20px",
                    )
                )(
                    f"ID: {row_id}"
                    + (f" | Village: {village}" if village else "")
                    + (f" | VES: {ves_no}" if ves_no else "")
                ),
                lib.html.p(
                    style=lib.Style(color="#dc3545", fontSize="13px", marginBottom="20px")
                )("⚠️ This action cannot be undone."),
                lib.html.div(style=lib.Style(display="flex", gap="10px", justifyContent="center"))(
                    lib.bs.Button(
                        variant="secondary",
                        onClick=close_delete_modal,
                        style=lib.Style(minWidth="120px"),
                    )("Cancel"),
                    lib.bs.Button(
                        variant="danger",
                        onClick=handle_confirm_delete,
                        style=lib.Style(minWidth="120px"),
                    )("🗑️ Delete"),
                ),
            )
        )

    # ---- AgGrid table ----
    def _build_table():
        if not displayed_data:
            return lib.html.div(
                style=lib.Style(
                    padding="40px",
                    textAlign="center",
                    color="#999",
                    fontSize="16px",
                    border="1px dashed #ddd",
                    borderRadius="4px",
                )
            )("📭 No data yet. Submit a form in the 'Add Data' tab to see records here.")

        col_defs = _build_col_defs(displayed_data)

        return lib.html.div(
            style=lib.Style(
                height="550px",
                border="1px solid #ddd",
                borderRadius="4px",
                overflow="hidden",
                backgroundColor="white",
            )
        )(
            lib.ag.AgGridReact(
                rowData=displayed_data,
                columnDefs=col_defs,
                defaultColDef=lib.Props(
                    flex=1,
                    resizable=True,
                    sortable=True,
                    filter=True,
                    editable=True,
                    wrapText=True,
                    autoHeight=True,
                ),
                rowSelection="single",
                onSelectionChanged=handle_row_selected,
                pagination=True,
                paginationPageSize=20,
                paginationPageSizeSelector=[10, 20, 50, 100],
                enableBrowserTooltips=True,
                suppressRowClickSelection=False,
            )
        )

    # ---- render ----
    return lib.html.div()(
        lib.html.style()("""
            @keyframes spin {
                from { transform: rotate(0deg); }
                to { transform: rotate(360deg); }
            }
            .spinner { display: inline-block; animation: spin 1s linear infinite; margin-right: 6px; }
        """),

        # Modals (rendered at top level so they sit above everything)
        _build_edit_modal(),
        _build_delete_modal(),

        lib.tabs.Tabs(
            lib.tabs.TabList(
                lib.tabs.Tab("Location Map"),
                lib.tabs.Tab("Add Data"),
                lib.tabs.Tab("View Data"),
            ),

            # ---- Tab 1: Location Map ----
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
                                style=lib.Style(
                                    border="0.0625rem solid #9c9c9c",
                                    borderRadius="0.25rem",
                                    width="100%",
                                    height="500px",
                                ),
                                width="100%",
                                height="500px",
                                strokeWidth=width,
                                strokeColor=color,
                            )
                        ),
                    ),
                )
            ),

            # ---- Tab 2: Add Data ----
            lib.tabs.TabPanel(
                lib.html.div(style=lib.Style(padding="20px"))(
                    lib.html.h2("Map Location Survey Form"),
                    lib.bs.Alert(variant="danger")(error_message) if error_message else None,
                    lib.bs.Alert(
                        variant="success",
                        style=lib.Style(borderLeft="4px solid #28a745"),
                    )("✓ Form submitted successfully! View Data tab has been updated.") if submit_success else None,
                    lib.bs.Form(key=form_key, onSubmit=submit_handler)(
                        lib.bs.Container()(
                            *_build_form_rows(),
                            lib.bs.Button(
                                type="submit",
                                variant="primary",
                                size="lg",
                                disabled=is_loading,
                                style=lib.Style(
                                    opacity="0.7" if is_loading else "1",
                                    cursor="not-allowed" if is_loading else "pointer",
                                    width="200px",
                                    padding="12px 24px",
                                    fontSize="16px",
                                    fontWeight="600",
                                ),
                            )(
                                lib.html.span(className="spinner")("⟳ ") if is_loading else "📤 ",
                                "Submitting..." if is_loading else "Submit Form",
                            ),
                        )
                    ),
                )
            ),

            # ---- Tab 3: View Data ----
            lib.tabs.TabPanel(
                lib.html.div(style=lib.Style(padding="20px"))(
                    lib.html.h2("📊 Form Data — All Submissions"),

                    # Feedback banners
                    lib.bs.Alert(variant="danger")(error_message) if error_message else None,
                    lib.bs.Alert(
                        variant="success",
                        style=lib.Style(borderLeft="4px solid #28a745"),
                    )(success_message) if success_message else None,

                    # Toolbar
                    lib.html.div(
                        style=lib.Style(
                            display="flex",
                            gap="10px",
                            marginBottom="16px",
                            alignItems="center",
                            flexWrap="wrap",
                        )
                    )(
                        lib.bs.Button(
                            onClick=lambda _: handle_refresh(),
                            variant="info",
                            size="sm",
                        )("🔄 Refresh"),
                        lib.bs.Button(
                            onClick=open_edit_modal,
                            variant="warning",
                            size="sm",
                            disabled=not selected_row,
                            title="Select a row to edit",
                        )("✏️ Edit Selected"),
                        lib.bs.Button(
                            onClick=open_delete_modal,
                            variant="danger",
                            size="sm",
                            disabled=not selected_row,
                            title="Select a row to delete",
                        )("🗑️ Delete Selected"),
                        lib.html.span(
                            style=lib.Style(
                                fontSize="13px",
                                color="#666",
                                marginLeft="6px",
                                alignSelf="center",
                            )
                        )(
                            f"✅ Selected: ID {selected_row.get('id', '?')} — {selected_row.get('village', '')}"
                            if selected_row
                            else "☑️ Click a row checkbox to select it"
                        ),
                    ),

                    lib.html.p(
                        style=lib.Style(fontSize="12px", color="#888", marginBottom="10px")
                    )(
                        f"Total records: {len(displayed_data)} | "
                        "Double-click a cell to edit inline. "
                        "Use ✏️ Edit / 🗑️ Delete buttons after selecting a row."
                    ),

                    _build_table(),
                )
            ),
        ),
    )
