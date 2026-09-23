from cryoet_organizer.tabs.custom import CustomTab


class EmbeddedCustomRuntime(CustomTab):
    tab_id = "embedded_custom"

    def __init__(self, host, parent):
        self.execution_host = host
        super().__init__(host.app, parent)
        self.frame.configure(padding=0)
        for row in (0, 1):
            for widget in self.content.grid_slaves(row=row):
                widget.grid_remove()
        self.runtime_frame.grid_configure(row=0, pady=0)

    def _build_builder_ui(self):
        pass

    def _refresh_job_options(self):
        pass

    def _global_ts_entries(self):
        if self.execution_host.tab_id == "tomograms":
            return list(self.execution_host.selected_entries)
        return super()._global_ts_entries()
